"""Output grounding / hallucination self-check (OWASP LLM09 Misinformation).

The SOTA "self-check facts" pattern — NeMo Guardrails' ``self_check_facts`` rail
and the RAGAS *groundedness* metric: given a generated answer and the retrieved
context passages it was supposed to be based on, an LLM self-check judges whether
the answer's claims are actually supported by those passages. An answer that
asserts facts not present in (or contradicted by) the contexts is *ungrounded* —
a likely hallucination.

Design mirrors :mod:`aegis.guardrails.content_safety` / :mod:`classifier`: an
injected-``ChatCompleter`` self-check returning a small verdict dataclass. There
is no deterministic backstop — groundedness is a semantic entailment judgement.

**Default posture is advisory (FLAG)** — an ungrounded answer surfaces a "this
may not be grounded in the retrieved sources" advisory in the trace without
withholding the answer; a ``block`` knob lets an enterprise hard-block instead.
When ``block`` is True the rail **fails closed** (an unavailable/unparseable
checker treats the answer as ungrounded); the default advisory mode fails *open*
(a downed checker never manufactures a spurious advisory). With no ``contexts``
the rail is a no-op PASS (nothing to ground against).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from aegis.core.interfaces import ChatCompleter
from aegis.guardrails.verdict_parsing import parse_bool_field

logger = logging.getLogger(__name__)

_GROUNDING_SYSTEM_PROMPT = (
    "You are a groundedness checker for a retrieval-augmented enterprise "
    "assistant. You are given CONTEXT passages and an ANSWER. Judge whether every "
    "factual claim in the ANSWER is supported by (entailed by) the CONTEXT. If the "
    "ANSWER asserts facts that are not present in, or are contradicted by, the "
    "CONTEXT, it is NOT grounded. General acknowledgements, refusals, and requests "
    "for clarification that make no factual claims ARE grounded. Respond with a "
    "single JSON object and nothing else: "
    '{"grounded": <true|false>, "reason": "<short; name the unsupported claim>"}.'
)


def _format_contexts(contexts: list[str]) -> str:
    """Render the retrieved passages into a numbered block for the prompt."""
    return "\n\n".join(f"[{i}] {c}" for i, c in enumerate(contexts, start=1))


def _build_user_message(answer: str, contexts: list[str]) -> str:
    return f"CONTEXT:\n{_format_contexts(contexts)}\n\nANSWER:\n{answer}"


@dataclass(frozen=True)
class GroundingVerdict:
    """The result of an output grounding self-check."""

    #: True when the answer's claims are judged supported by the contexts (or the
    #: rail is disabled / failed open in advisory mode).
    grounded: bool
    #: Human-readable rationale — names the unsupported claim when ungrounded.
    reason: str = ""


def _parse_verdict(raw: str, *, fail_closed: bool) -> GroundingVerdict:
    """Parse the checker's raw text into a :class:`GroundingVerdict`.

    Prefers a JSON object with a ``grounded`` field; falls back to a yes/no scan.
    On an unparseable response the direction is set by ``fail_closed``: a blocking
    rail treats it as ungrounded, an advisory rail lets it through.
    """
    text = raw.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "grounded" in data:
            return GroundingVerdict(
                grounded=bool(data["grounded"]),
                reason=str(data.get("reason", "")) or "Checker returned no reason.",
            )
    except (json.JSONDecodeError, ValueError, TypeError):
        logger.debug("Grounding checker returned non-JSON; using keyword fallback.")

    verdict = parse_bool_field(text, "grounded")
    if verdict is True:
        return GroundingVerdict(grounded=True, reason="Checker judged the answer grounded.")
    if verdict is False:
        return GroundingVerdict(grounded=False, reason="Checker judged the answer ungrounded.")

    # Ambiguous (e.g. a reply that merely *begins* with "yes"/"no") is no verdict at
    # all; the rail's own fail direction decides.
    if fail_closed:
        return GroundingVerdict(
            grounded=False, reason="Grounding checker response unparseable; flagged ungrounded."
        )
    return GroundingVerdict(
        grounded=True, reason="Grounding checker response unparseable; allowed (advisory rail)."
    )


async def check_grounding(
    answer: str,
    contexts: list[str] | None,
    *,
    completer: ChatCompleter | None,
    block: bool = False,
) -> GroundingVerdict:
    """Check whether ``answer`` is grounded in the retrieved ``contexts``.

    Args:
        answer: The generated answer to check.
        contexts: The retrieved context passages the answer should be based on.
            When ``None``/empty the rail is a no-op PASS (nothing to ground on).
        completer: The async chat-completion callable for the self-check, or
            ``None`` to disable the model layer (rail is a no-op PASS).
        block: When True the rail is a hard block and **fails closed** on error;
            when False (default) it is advisory and fails open.

    Returns:
        A :class:`GroundingVerdict`; ``grounded=False`` is a FLAG (or BLOCK when
        ``block``) in the pipeline mapping.
    """
    passages = [c for c in (contexts or []) if isinstance(c, str) and c.strip()]
    if not passages:
        return GroundingVerdict(grounded=True, reason="Grounding rail skipped (no contexts).")
    if not answer.strip():
        return GroundingVerdict(grounded=True, reason="Grounding rail skipped (empty answer).")
    if completer is None:
        logger.warning(
            "Grounding rail model layer disabled (no ChatCompleter configured); passing."
        )
        return GroundingVerdict(
            grounded=True, reason="Grounding rail model layer disabled (no completer)."
        )

    messages = [
        {"role": "system", "content": _GROUNDING_SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_message(answer, passages)},
    ]
    try:
        raw = await completer(messages, response_format={"type": "json_object"})
    except Exception:  # noqa: BLE001 - a blocking rail must fail closed
        logger.warning("Grounding checker call failed.", exc_info=True)
        if block:
            return GroundingVerdict(
                grounded=False,
                reason="Grounding checker unavailable; flagged ungrounded as a precaution.",
            )
        return GroundingVerdict(
            grounded=True, reason="Grounding checker unavailable; allowed (advisory rail)."
        )
    return _parse_verdict(raw, fail_closed=block)
