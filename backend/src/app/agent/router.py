"""The supervisor router — core-owned intent classification + hand-off protocol.

The multi-agent supervisor is split cleanly across the core/adapter seam:

* the **core** (this module) owns the *mechanism* — a deterministic-first classifier
  and the hand-off contract (:class:`RouterDecision`), plus a bounded cheap-LLM
  tiebreak used only when the deterministic pass is genuinely ambiguous;
* the **adapter** (:func:`app.adapter.roster.agent_roster`) declares *which*
  specialists exist and the keyword hints that recognise each one.

Deterministic-FIRST is deliberate: for the common, clear cases (a normal question, or
a plainly self-referential "what do you know about me") the role is decided by phrase
matching with **no model call at all**, so the money-shot trace stays clean and the
whole thing is offline-testable. Only a tie between two named specialists escalates to
:data:`~app.core.models.ModelRole.CHEAP`; if no cheap model is available, or its answer
is not a known role, the router falls back to the roster's default (``qa``).

The core reads the roster **defensively**: if the adapter does not expose an
``agent_roster`` contract, :func:`load_roster` degrades to a ``qa``-only roster and the
supervisor becomes a transparent pass-through to the existing pipeline.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.core.models import ModelRole

logger = logging.getLogger(__name__)

# A ``qa``-only roster the core falls back to when the adapter exposes no roster.
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


def load_roster() -> Any:  # noqa: ANN401 - adapter AgentRoster duck-type
    """Return the adapter's agent roster, or a ``qa``-only fallback.

    Read defensively so a domain that has not (yet) declared an ``agent_roster``
    contract still runs: the supervisor then only ever routes to ``qa`` and the graph
    behaves exactly as it did before the router existed.
    """
    try:
        from app.adapter import agent_roster

        roster = agent_roster()
        if roster is not None and roster.roles():
            return roster
    except Exception:  # noqa: BLE001 - the roster is an optional adapter contract
        logger.warning("Agent roster unavailable; routing everything to qa", exc_info=True)
    return _FallbackRoster()


@dataclass(frozen=True)
class _FallbackRoster:
    """The minimal ``qa``-only roster used when the adapter declares none.

    Mirrors the read shape of the real :class:`app.adapter.roster.AgentRoster`
    (``default_role`` property, ``roles``/``named``/``specialists``) so the classifier
    treats it identically.
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


def _match_score(query_lc: str, spec: Any) -> tuple[int, list[str]]:  # noqa: ANN401
    """Return ``(hits, matched_phrases)`` for one specialist against a lower-cased query.

    A hit is a keyword phrase that appears as a substring of the query. The count is
    the deterministic signal; the phrases feed the human-readable hand-off reason.
    """
    matched = [kw for kw in getattr(spec, "keywords", ()) if kw and kw in query_lc]
    return (len(matched), matched)


def classify_deterministic(query: str, roster: Any) -> tuple[str | None, str]:  # noqa: ANN401
    """Classify ``query`` by keyword hints alone (no model call).

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

    top = max(hits for _, hits, _ in positives)
    winners = [(spec, phrases) for spec, hits, phrases in positives if hits == top]
    if len(winners) == 1:
        spec, phrases = winners[0]
        hint = ", ".join(f"'{p}'" for p in phrases[:2])
        return spec.role, f"matched {spec.role} hint(s) {hint}"

    tied = ", ".join(spec.role for spec, _ in winners)
    return None, f"ambiguous: specialists {tied} tied on {top} hint(s)"


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
    The reply is normalised to a known role id; anything unrecognised yields ``None`` so
    the caller can fall back to the default (the router never trusts free text).
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
    for role in roster.roles():
        if role.lower() in reply:
            return role
    return None
