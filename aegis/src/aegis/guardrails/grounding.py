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

**Two findings, not one, because they deserve different answers.** "Unsupported"
and "contradicted" were a single boolean here, and collapsing them is what forced
the whole rail to be advisory:

* **Unsupported** — the answer asserts something the passages neither state nor
  deny. Extrapolation, a summary that reaches slightly past its source, a sentence
  of framing. Blocking these is what makes a grounding rail unusable, because a
  large share of them are fine, and an operator who cannot ship switches the rail
  off. So this stays **advisory (FLAG)** by default; a ``block`` knob lets an
  enterprise hard-block instead.
* **Contradicted** — the passages say the opposite. Retrieval found the answer and
  the model said something else. There is no legitimate turn of that shape: it is
  the case where the corpus is *right there* and the answer disagrees with it, and
  handing it to a person with a citation attached is worse than refusing. This
  **BLOCKs by default**, ``block`` knob or not.

Fail directions are set by which of the two is at stake. When ``block`` is True the
rail fails closed on *unsupported* (an unavailable/unparseable checker treats the
answer as ungrounded); the default advisory mode fails open there. ``contradicted``
never fails closed in either mode — a checker that could not answer has not found a
contradiction, and manufacturing one would hard-block a working deployment the
moment its gateway hiccuped.

With no ``contexts`` the rail reports ungrounded but the caller deliberately never
blocks on it — see
:meth:`aegis.guardrails.pipeline.Guardrails._screen_grounding`.
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
    "assistant. You are given CONTEXT passages and an ANSWER. Judge two separate "
    "things. (1) grounded: is every factual claim in the ANSWER supported by "
    "(entailed by) the CONTEXT? An ANSWER asserting facts the CONTEXT does not "
    "state is NOT grounded. General acknowledgements, refusals, and requests for "
    "clarification that make no factual claims ARE grounded. (2) contradicted: does "
    "the CONTEXT state the OPPOSITE of a claim in the ANSWER — a different number, "
    "date, name, limit, or an explicit denial? Merely being absent from the CONTEXT "
    "is NOT a contradiction; only set contradicted when the CONTEXT positively "
    "conflicts with the ANSWER. Respond with a single JSON object and nothing else: "
    '{"grounded": <true|false>, "contradicted": <true|false>, '
    '"reason": "<short; name the claim and, if contradicted, what the CONTEXT says>"}.'
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
    #: True only when the retrieved passages state the **opposite** of a claim in
    #: the answer. Strictly stronger than ``not grounded``: absence from the context
    #: is unsupported, presence of the opposite is contradicted. Never inferred and
    #: never defaulted to True — a checker that could not answer has not found a
    #: contradiction, and the caller hard-blocks on this flag in every mode.
    contradicted: bool = False


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
            grounded = bool(data["grounded"])
            # A contradiction is only meaningful against an ungrounded answer, and a
            # checker that says both "grounded" and "contradicted" has contradicted
            # itself — the conjunction is dropped rather than resolved in favour of
            # the harsher reading, because this flag hard-blocks.
            return GroundingVerdict(
                grounded=grounded,
                contradicted=bool(data.get("contradicted", False)) and not grounded,
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
        A :class:`GroundingVerdict`. ``grounded=False`` is a FLAG (or BLOCK when
        ``block``) in the pipeline mapping; ``contradicted=True`` is a BLOCK in
        either mode.
    """
    passages = [c for c in (contexts or []) if isinstance(c, str) and c.strip()]
    if not passages:
        # Not "skipped, therefore fine". An answer with no passages behind it is
        # ungrounded by definition, and saying otherwise is how a fabricated citation
        # reached a user under a clean output-rail verdict. The caller
        # (:meth:`~aegis.guardrails.pipeline.Guardrails._screen_grounding`) decides what
        # to do about it and deliberately never blocks on this branch; this function's
        # job is only to stop reporting it as grounded.
        return GroundingVerdict(
            grounded=bool(not answer.strip()),
            reason="No passages were retrieved, so nothing supports this answer.",
        )
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
