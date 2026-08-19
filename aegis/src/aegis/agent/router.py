"""The supervisor router — core-owned intent classification + hand-off protocol.

The multi-agent supervisor is split cleanly across the core/adapter seam:

* the **core** (this module) owns the *mechanism* — a deterministic-first classifier
  and the hand-off contract (:class:`RouterDecision`), plus a bounded cheap-LLM
  tiebreak used only when the deterministic pass is genuinely ambiguous;
* the **host adapter** declares *which* specialists exist and the keyword hints that
  recognise each one, injected into the graph as the ``agent_roster`` hook.

Deterministic-FIRST is deliberate: for the common, clear cases (a normal question, or
a plainly self-referential "what do you know about me") the role is decided by phrase
matching with **no model call at all**, so the money-shot trace stays clean and the
whole thing is offline-testable. Only a tie between two named specialists escalates to
:data:`~aegis.core.models.ModelRole.CHEAP`; if no cheap model is available, or its
answer is not a known role, the router falls back to the roster's default (``qa``).

The core reads the roster **defensively**: the app-side wiring supplies the real
adapter roster through the injected ``deps.agent_roster`` hook; when no roster is
available :func:`load_roster` degrades to a ``qa``-only roster and the supervisor
becomes a transparent pass-through to the existing pipeline. The adapter-backed
``load_roster`` lives host-side (in the composition root), never here.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from aegis.core.models import ModelRole

logger = logging.getLogger(__name__)

# A ``qa``-only roster the core falls back to when no roster is available.
_QA_ONLY_ROLE = "qa"


class Depth(StrEnum):
    """How WIDE a turn runs: one lane, or a concurrent team of sub-agents."""

    #: One specialist, one lane — the default and the cheap path.
    SINGLE = "single"
    #: A concurrent fan-out of ``fanout`` sub-agents, merged by a synthesis step.
    TEAM = "team"


class DepthMode(StrEnum):
    """The *requested* width — the field Phase 6's composer mode control writes to.

    This phase owns the field; Phase 6 owns the control that sets it. Its five buttons
    (Auto · Fast · Deep · Team · Custom) map onto three honest values, so the console
    can rename or regroup its buttons without a core change:

    * ``Auto``            → :attr:`AUTO` — the classifier decides.
    * ``Fast`` / ``Deep`` → :attr:`SINGLE` — one lane (Deep tunes *depth per lane*,
      which is ``max_plan_iterations``, not width).
    * ``Team``            → :attr:`TEAM` with no explicit width (the platform's default).
    * ``Custom``          → :attr:`TEAM` with an explicit ``requested_fanout``.
    """

    #: Let the classifier decide (the only mode in which it runs at all).
    AUTO = "auto"
    #: The user asked for one lane. Honoured exactly; the classifier is SKIPPED.
    SINGLE = "single"
    #: The user asked for a team. Honoured exactly, clamped only by the platform cap.
    TEAM = "team"


@dataclass(frozen=True)
class DepthPolicy:
    """Everything :func:`decide_depth` is allowed to consider, as one value object.

    Attributes:
        mode: The user's requested width (:attr:`DepthMode.AUTO` → classifier).
        requested_fanout: An explicit width from the user (``Custom``), or ``None``.
        max_parallel_agents: The platform/tenant cap. A manual choice may be
            **narrowed** by it, never widened by the user — ``Custom`` is not a way
            around a budget cap, and the clamp lives here, in the same place the cap
            is read, rather than in the browser.
        min_fanout: The smallest team worth forming (a "team" of one is a single).
        team_enabled: Master switch. ``False`` → SINGLE, always, whatever was asked.
        available_agents: How many sub-agents the host's roster can actually field.
            Tracked separately from ``team_enabled`` so the two SINGLE outcomes stay
            distinguishable on screen — "your tenant has team mode off" and "nobody
            declared a team" are different facts, and a trace that conflates them sends
            somebody to the wrong settings page. ``0`` means the host declared none;
            ``None`` means no roster constrains this decision, which is the shape a
            unit test of the classifier alone uses. The graph always passes a number.
    """

    mode: DepthMode = DepthMode.AUTO
    requested_fanout: int | None = None
    max_parallel_agents: int = 4
    min_fanout: int = 2
    team_enabled: bool = True
    available_agents: int | None = None

    @property
    def ceiling(self) -> int:
        """The largest team actually formable: the platform cap ∩ the roster's size."""
        if self.available_agents is None:
            return self.max_parallel_agents
        return min(self.max_parallel_agents, max(0, self.available_agents))


@dataclass(frozen=True)
class DepthDecision:
    """The width decision for one turn, with who decided it and why.

    Attributes:
        depth: SINGLE or TEAM.
        fanout: ``0`` for SINGLE; ``min_fanout..max_parallel_agents`` for TEAM.
        reason: A demoable, glass-box explanation. **Never a width with no reason.**
        decided_by: Who decided — ``auto`` (the classifier), ``user`` (an explicit
            mode), ``tenant_default`` (team disabled / no roster), or ``platform_cap``
            (the user's width was narrowed by ``max_parallel_agents``).
        used_llm: Whether the one cheap-model call was spent on this decision.
    """

    depth: Depth = Depth.SINGLE
    fanout: int = 0
    reason: str = ""
    decided_by: str = "auto"
    used_llm: bool = False


@dataclass(frozen=True)
class RouterDecision:
    """The supervisor's hand-off decision for one turn.

    Attributes:
        role: The specialist role id the turn is dispatched to (a roster role).
        reason: A demoable, glass-box explanation of why (the visible hand-off).
        used_llm: Whether the cheap-LLM tiebreak was consulted (deterministic when
            ``False`` — the common, clean-trace path).
        depth: How wide the turn runs (:class:`Depth`). ``SINGLE`` by default and on
            every failure path.
        fanout: How many sub-agents a ``TEAM`` turn fans out to (``0`` for SINGLE).
        decided_by: Who decided the width — see :class:`DepthDecision`.
    """

    role: str
    reason: str
    used_llm: bool = False
    depth: Depth = Depth.SINGLE
    fanout: int = 0
    decided_by: str = "auto"

    def with_depth(self, decision: DepthDecision) -> RouterDecision:
        """Return a copy carrying ``decision``'s width, folding its reason into ours.

        The role reason and the width reason are two different explanations of the
        same hand-off, and the console shows one line, so they are joined here rather
        than at each call site.
        """
        reason = f"{self.reason}; {decision.reason}" if decision.reason else self.reason
        return replace(
            self,
            reason=reason,
            depth=decision.depth,
            fanout=decision.fanout,
            decided_by=decision.decided_by,
            used_llm=self.used_llm or decision.used_llm,
        )


def load_roster() -> Any:  # noqa: ANN401 - AgentRoster duck-type
    """Return the core ``qa``-only fallback roster.

    The adapter-backed roster is supplied host-side through the graph's injected
    ``deps.agent_roster`` hook; this is only the defensive fallback the graph uses
    when that hook is missing or fails. The supervisor then only ever routes to
    ``qa`` and the graph behaves exactly as it did before the router existed.
    """
    return _FallbackRoster()


@dataclass(frozen=True)
class _FallbackRoster:
    """The minimal ``qa``-only roster used when no adapter roster is available.

    Mirrors the read shape of a real ``AgentRoster`` (``default_role`` property,
    ``roles``/``named``/``specialists``) so the classifier treats it identically.
    """

    @property
    def default_role(self) -> str:
        return _QA_ONLY_ROLE

    @property
    def specialists(self) -> tuple[Any, ...]:
        return ()

    def roles(self) -> list[str]:
        return [_QA_ONLY_ROLE]

    def named(self) -> list[Any]:
        return []


def _phrase_present(phrase: str, query_lc: str) -> bool:
    r"""Whether ``phrase`` occurs in ``query_lc`` on **word boundaries**.

    A bare substring test made "memory" match "memorandum" and "bill" match "billboard",
    so a specialist could win on a word it has nothing to do with. Boundaries are
    alphanumeric-aware rather than ``\\b``-only so a multi-word or punctuated hint
    ("out of office", "p&l") still matches.
    """
    pattern = r"(?<![a-z0-9])" + re.escape(phrase.lower()) + r"(?![a-z0-9])"
    return re.search(pattern, query_lc) is not None


def _match_score(query_lc: str, spec: Any) -> tuple[int, list[str]]:  # noqa: ANN401
    """Return ``(hits, matched_phrases)`` for one specialist against a lower-cased query.

    A hit is a keyword phrase that occurs in the query on word boundaries. Duplicated
    hints are counted once (a roster that lists the same phrase twice must not out-score
    one that lists it once), and matches are ordered longest-first so the most specific
    phrase leads the hand-off reason.
    """
    seen: set[str] = set()
    matched: list[str] = []
    for kw in getattr(spec, "keywords", ()):
        if not kw:
            continue
        lowered = kw.lower()
        if lowered in seen:
            continue
        if _phrase_present(lowered, query_lc):
            seen.add(lowered)
            matched.append(kw)
    matched.sort(key=len, reverse=True)
    return (len(matched), matched)


def classify_deterministic(query: str, roster: Any) -> tuple[str | None, str]:  # noqa: ANN401
    """Classify ``query`` by keyword hints alone (no model call).

    Scoring is ``(distinct hits, total matched characters)``: the hit count is the
    primary signal, and the *specificity* of what matched breaks a tie, so a specialist
    matching one long, precise phrase is not automatically beaten by one matching two
    generic words. Only a dead heat on both is reported as ambiguous.

    Returns:
        ``(role, reason)`` when a single specialist wins outright or nothing matches
        (fall through to the roster default); ``(None, reason)`` when two or more named
        specialists tie on the top score — the *ambiguous* case the caller may escalate
        to the cheap-LLM tiebreak.
    """
    query_lc = query.lower()
    default_role = roster.default_role

    scored = [
        (spec, *_match_score(query_lc, spec)) for spec in roster.named()
    ]
    positives = [(spec, hits, phrases) for spec, hits, phrases in scored if hits > 0]
    if not positives:
        return default_role, "no specialist keywords matched; default pipeline"

    def _rank(entry: tuple[Any, int, list[str]]) -> tuple[int, int]:
        _spec, hits, phrases = entry
        return (hits, sum(len(p) for p in phrases))

    top = max(_rank(entry) for entry in positives)
    winners = [(spec, phrases) for entry in positives
               if _rank(entry) == top
               for spec, _hits, phrases in (entry,)]
    if len(winners) == 1:
        spec, phrases = winners[0]
        hint = ", ".join(f"'{p}'" for p in phrases[:2])
        return spec.role, f"matched {spec.role} hint(s) {hint}"

    tied = ", ".join(spec.role for spec, _ in winners)
    return None, f"ambiguous: specialists {tied} tied on {top[0]} hint(s)"


# The injected chat-completion callable the tiebreak uses (kept loose to avoid coupling).
CompleteFn = Callable[..., Awaitable[Any]]


async def route_query(
    query: str, roster: Any, *, complete: CompleteFn | None = None  # noqa: ANN401
) -> RouterDecision:
    """Decide which specialist handles ``query`` — deterministic first, LLM only on ties.

    Args:
        query: The (guardrail-cleaned) user query.
        roster: The adapter roster of routable specialists.
        complete: Optional cheap-model chat callable used to break a genuine tie; when
            ``None`` an ambiguous result falls back to the roster default.

    Returns:
        A :class:`RouterDecision` carrying the chosen role, a demoable reason, and
        whether the model was consulted.
    """
    role, reason = classify_deterministic(query, roster)
    if role is not None:
        return RouterDecision(role=role, reason=reason, used_llm=False)

    # Ambiguous: two named specialists tied. Escalate to a cheap tiebreak if we can.
    default_role = roster.default_role
    if complete is None:
        return RouterDecision(
            role=default_role,
            reason=f"{reason}; no tiebreak model, defaulted to {default_role}",
            used_llm=False,
        )
    try:
        picked = await _llm_tiebreak(query, roster, complete)
    except Exception:  # noqa: BLE001 - a tiebreak failure must never fail the run
        logger.warning("Router LLM tiebreak failed; defaulting", exc_info=True)
        picked = None
    if picked in roster.roles():
        return RouterDecision(
            role=picked, reason=f"{reason}; cheap-LLM tiebreak chose {picked}", used_llm=True
        )
    return RouterDecision(
        role=default_role,
        reason=f"{reason}; tiebreak inconclusive, defaulted to {default_role}",
        used_llm=True,
    )


async def _llm_tiebreak(query: str, roster: Any, complete: CompleteFn) -> str | None:  # noqa: ANN401
    """Ask a cheap model to pick a role from the roster; return a bare role id or ``None``.

    The prompt is a closed menu of role ids + descriptions and asks for one token back.
    The reply is normalised to a known role id; anything unrecognised — **or ambiguous** —
    yields ``None`` so the caller falls back to the default (the router never trusts free
    text).

    Matching is deliberately strict. Scanning the roster in order for a role id anywhere
    in the reply made ``"not qa — use memory"`` return ``qa``: the *rejected* role won
    because it was declared first. So an exact reply wins; otherwise the reply must
    mention exactly one role on word boundaries, and a reply naming several is a
    non-answer, not a vote for whichever the roster happens to list first.
    """
    menu = "\n".join(
        f"- {spec.role}: {getattr(spec, 'description', '')}" for spec in roster.specialists
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You are an intent router. Choose exactly ONE role id that best fits "
                "the user's request. Reply with only the role id, nothing else.\n"
                f"Roles:\n{menu}"
            ),
        },
        {"role": "user", "content": query},
    ]
    result = await complete(ModelRole.CHEAP, messages)
    reply = (getattr(result, "content", "") or "").strip().lower()
    if not reply:
        return None

    roles = list(roster.roles())
    # 1. The asked-for shape: the bare role id (optionally quoted/punctuated).
    bare = reply.strip("\"'`.,:;! \t\n")
    for role in roles:
        if bare == role.lower():
            return role
    # 2. Otherwise: exactly one role mentioned on word boundaries, else no answer.
    mentioned = [role for role in roles if _phrase_present(role.lower(), reply)]
    if len(mentioned) == 1:
        return mentioned[0]
    if len(mentioned) > 1:
        logger.warning(
            "Router tiebreak reply named %d roles (%s); treating as inconclusive",
            len(mentioned),
            ", ".join(mentioned),
        )
    return None


# ── The depth classifier: how WIDE, not which specialist ──────────────────────
#
# Deterministic first, exactly as the role classifier above. The ONE cheap-model call
# is spent only on a genuinely ambiguous query, and only in AUTO mode, and its failure
# is SINGLE. A broken classifier that quietly fans out is the failure the budget
# cannot absorb, so every path out of this section that is not a positive, explained
# TEAM signal is SINGLE.

#: Below this word count a query is single-intent by construction. Nothing shorter
#: than this has ever needed four agents, and "what is my remaining budget?" is six.
_SINGLE_WORD_CEILING = 12

#: At or above this word count a query is long enough that the deterministic pass
#: hands it to the cheap model rather than guessing.
_AMBIGUOUS_WORD_FLOOR = 20

#: Phrases that make a query *explicitly* multi-part. These are conjunction-of-tasks
#: markers, not topic words, so they cannot fire on a long single-intent question.
_MULTIPART_MARKERS: tuple[str, ...] = (
    "compare",
    "compares",
    "compared",
    "comparison",
    "contrast",
    "versus",
    "vs",
    "and also",
    "as well as",
    "and tell me",
    "and then",
    "and which",
    "and what",
    "and how",
    "and whether",
    "difference between",
)

#: Phrases naming external / current information — a Research lane on its own.
_EXTERNAL_MARKERS: tuple[str, ...] = (
    "latest",
    "news",
    "this quarter",
    "this month",
    "recently",
    "changed in the regulation",
    "market",
    "competitor",
    "industry",
    "current state",
    "search the web",
)


def _clamp_fanout(width: int, policy: DepthPolicy) -> int:
    """Clamp ``width`` into ``[min_fanout, ceiling]`` (the cap ∩ the roster's size)."""
    ceiling = max(policy.min_fanout, policy.ceiling)
    return max(policy.min_fanout, min(int(width), ceiling))


def _subquestion_count(query: str) -> int:
    """Count the distinct asks in ``query`` by clause/question separators.

    Deliberately crude and deliberately *conservative*: it counts separators, so a
    single-intent sentence scores 1 and cannot fan out on this signal alone.
    """
    parts = [p for p in re.split(r"[?;]|\band also\b|\band then\b", query) if p.strip()]
    return max(1, len(parts))


def _explicit_decision(policy: DepthPolicy) -> DepthDecision | None:
    """Resolve an explicit (non-AUTO) width, or ``None`` when the classifier should run.

    **Manual wins.** The classifier is *skipped*, not overruled after the fact, so
    ``Fast`` never pays for the cheap-model call it is trying to avoid. The one thing
    the platform may do to a manual choice is NARROW it: a user who pins 6 against a
    cap of 4 gets 4, and the event says ``platform_cap`` so the screen can say why.
    Widening is never available to the user — that is what a cap means.
    """
    if policy.mode is DepthMode.AUTO:
        return None
    if policy.mode is DepthMode.SINGLE:
        return DepthDecision(
            depth=Depth.SINGLE,
            fanout=0,
            reason="you selected a single-agent run",
            decided_by="user",
        )
    requested = policy.requested_fanout or policy.ceiling
    fanout = _clamp_fanout(requested, policy)
    if policy.requested_fanout is not None and fanout < policy.requested_fanout:
        return DepthDecision(
            depth=Depth.TEAM,
            fanout=fanout,
            reason=(
                f"you selected Team ×{policy.requested_fanout}, narrowed to ×{fanout} "
                f"by the platform cap of {policy.ceiling}"
            ),
            decided_by="platform_cap",
        )
    return DepthDecision(
        depth=Depth.TEAM,
        fanout=fanout,
        reason=f"you selected Team mode (×{fanout})",
        decided_by="user",
    )


def _deterministic_depth(
    query: str, *, policy: DepthPolicy, role_is_default: bool
) -> DepthDecision | None:
    """Classify width by structure alone, or return ``None`` when genuinely ambiguous.

    Returns a decision for every case it is confident about — including the SINGLE
    ones, which are the majority and the cheap ones — and ``None`` only for the band
    the caller may spend one cheap-model call on.
    """
    words = query.split()
    query_lc = query.lower()

    if not role_is_default:
        return DepthDecision(
            depth=Depth.SINGLE,
            fanout=0,
            reason="routed to a specialist that answers in one pass",
        )
    if len(words) <= _SINGLE_WORD_CEILING and _subquestion_count(query) == 1:
        markers = [m for m in _MULTIPART_MARKERS if _phrase_present(m, query_lc)]
        if not markers:
            return DepthDecision(
                depth=Depth.SINGLE,
                fanout=0,
                reason="single-intent query, answering in one pass",
            )

    hits = [m for m in _MULTIPART_MARKERS if _phrase_present(m, query_lc)]
    external = [m for m in _EXTERNAL_MARKERS if _phrase_present(m, query_lc)]
    subquestions = _subquestion_count(query)
    if hits or external or subquestions > 1:
        width = _clamp_fanout(max(subquestions, len(hits) + len(external), 2), policy)
        detail = []
        if subquestions > 1:
            detail.append(f"{subquestions} sub-questions detected")
        if hits:
            detail.append("multi-part phrasing " + ", ".join(repr(h) for h in hits[:2]))
        if external:
            detail.append("external/current information requested")
        return DepthDecision(
            depth=Depth.TEAM,
            fanout=width,
            reason=f"{'; '.join(detail)}, fanning out to {width} agents",
        )
    if len(words) >= _AMBIGUOUS_WORD_FLOOR:
        return None
    return DepthDecision(
        depth=Depth.SINGLE,
        fanout=0,
        reason="single-intent query, answering in one pass",
    )


async def decide_depth(
    query: str,
    *,
    policy: DepthPolicy | None = None,
    complete: CompleteFn | None = None,
    role_is_default: bool = True,
) -> DepthDecision:
    """Decide how WIDE this turn runs — SINGLE, or TEAM of ``fanout`` sub-agents.

    The whole rule, in one line::

        effective_depth = user_mode if user_mode != AUTO else classifier_decision

    Args:
        query: The (guardrail-cleaned) user query.
        policy: The width policy — the user's requested mode, the platform cap and the
            master switch. ``None`` means "no team is possible here" and yields SINGLE.
        complete: The cheap-model callable used for the single ambiguity call. ``None``
            → the ambiguous band resolves to SINGLE.
        role_is_default: Whether the role classifier picked the roster default. A turn
            already routed to a narrow specialist (e.g. ``memory``) answers in one pass.

    Returns:
        A :class:`DepthDecision`. **SINGLE on every failure path**, always with a reason.
    """
    if policy is None:
        return DepthDecision(
            depth=Depth.SINGLE,
            fanout=0,
            reason="no sub-agent team is configured; answering in one pass",
            decided_by="tenant_default",
        )
    if not policy.team_enabled:
        return DepthDecision(
            depth=Depth.SINGLE,
            fanout=0,
            reason="team mode is disabled for this tenant; answering in one pass",
            decided_by="tenant_default",
        )
    if policy.ceiling < policy.min_fanout:
        return DepthDecision(
            depth=Depth.SINGLE,
            fanout=0,
            reason="no sub-agent team is configured; answering in one pass",
            decided_by="tenant_default",
        )
    explicit = _explicit_decision(policy)
    if explicit is not None:
        return explicit

    try:
        deterministic = _deterministic_depth(
            query, policy=policy, role_is_default=role_is_default
        )
    except Exception:  # noqa: BLE001 - the classifier must never be why a run dies
        logger.warning("Depth classifier failed; defaulting to SINGLE", exc_info=True)
        return DepthDecision(
            depth=Depth.SINGLE, fanout=0, reason="classifier unavailable, answering in one pass"
        )
    if deterministic is not None:
        return deterministic
    if complete is None:
        return DepthDecision(
            depth=Depth.SINGLE,
            fanout=0,
            reason="ambiguous width and no classifier model; answering in one pass",
        )
    try:
        width = await _llm_width(query, policy, complete)
    except Exception:  # noqa: BLE001 - and it must never be why a run gets expensive
        logger.warning("Depth classifier model call failed; defaulting to SINGLE",
                       exc_info=True)
        width = None
    if width is None or width < policy.min_fanout:
        return DepthDecision(
            depth=Depth.SINGLE,
            fanout=0,
            reason="classifier judged this a single-intent query, answering in one pass",
            used_llm=True,
        )
    clamped = _clamp_fanout(width, policy)
    return DepthDecision(
        depth=Depth.TEAM,
        fanout=clamped,
        reason=f"classifier split this into {clamped} sub-questions, fanning out",
        used_llm=True,
    )


async def _llm_width(query: str, policy: DepthPolicy, complete: CompleteFn) -> int | None:
    """Ask one cheap model for a width; return an int, or ``None`` for "not a number".

    The reply is normalised to the first integer in it, exactly like the role tiebreak
    normalises to a bare role id: the classifier never trusts free text, and anything
    it cannot read as a number is a non-answer (→ SINGLE), not a vote for fanning out.
    """
    messages = [
        {
            "role": "system",
            "content": (
                "You size a query for a multi-agent system. Answer with ONE integer "
                "and nothing else: the number of genuinely INDEPENDENT sub-questions "
                "the request contains. Answer 1 when it is one question, however long. "
                f"Never answer more than {policy.max_parallel_agents}."
            ),
        },
        {"role": "user", "content": query},
    ]
    result = await complete(ModelRole.CHEAP, messages)
    reply = (getattr(result, "content", "") or "").strip()
    match = re.search(r"\d+", reply)
    if match is None:
        logger.warning("Depth classifier reply %r carried no number; SINGLE", reply)
        return None
    return int(match.group())
