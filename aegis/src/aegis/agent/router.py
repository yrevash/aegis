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
from dataclasses import dataclass
from typing import Any

from aegis.core.models import ModelRole

logger = logging.getLogger(__name__)

# A ``qa``-only roster the core falls back to when no roster is available.
_QA_ONLY_ROLE = "qa"


@dataclass(frozen=True)
class RouterDecision:
    """The supervisor's hand-off decision for one turn.

    Attributes:
        role: The specialist role id the turn is dispatched to (a roster role).
        reason: A demoable, glass-box explanation of why (the visible hand-off).
        used_llm: Whether the cheap-LLM tiebreak was consulted (deterministic when
            ``False`` — the common, clean-trace path).
    """

    role: str
    reason: str
    used_llm: bool = False


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
