"""Output grounding / hallucination self-check (OWASP LLM09 Misinformation).

The SOTA "self-check facts" pattern — NeMo Guardrails' ``self_check_facts`` rail
and the RAGAS *groundedness* metric: given a generated answer and the retrieved
context passages it was supposed to be based on, an LLM self-check judges whether
the answer's claims are actually supported by those passages. An answer that
asserts facts not present in (or contradicted by) the contexts is *ungrounded* —
a likely hallucination.

Design mirrors :mod:`aegis.guardrails.content_safety` / :mod:`classifier`: an
injected-``ChatCompleter`` self-check returning a small verdict dataclass.

**There is one deterministic backstop, and it is about citations rather than
entailment.** Groundedness itself is a semantic judgement and needs a model. Whether an
answer's *citation* points at a passage that was actually retrieved does not: the answer
context this platform builds numbers every passage it hands the model
(:func:`aegis.retrieval.spotlight.build_spotlighted_context` writes ``[source 1]``,
``[source 2]``, …), so the set of source labels a truthful answer may cite is known
exactly, from our own generated text, with no model in the loop.
:func:`check_citation_integrity` compares the two sets and reports every citation the run
cannot support. That is what keeps this rail worth something with no completer wired —
see :func:`aegis.guardrails.pipeline.Guardrails._screen_grounding`, which blocks on it.

What it catches is a **false attribution**: an answer telling a reader that a claim came
from a passage that does not exist. It does not catch a fabricated claim carrying no
citation, and it does not catch a real citation used to support something it does not
say — :mod:`aegis.retrieval.citations` checks *quotes* against the chunk they name, and
the model rail below judges entailment. Three different questions, three mechanisms, and
only this one runs offline.

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
import re
from dataclasses import dataclass, field

from aegis.core.interfaces import ChatCompleter
from aegis.guardrails.verdict_parsing import parse_bool_field

logger = logging.getLogger(__name__)

#: The source label :mod:`aegis.retrieval.spotlight` writes above every retrieved passage,
#: and therefore the only citation form an answer can have read off its own context. It is
#: matched here rather than guessed at: this is a format **this codebase emits**, not a
#: heuristic for prose, which is the difference between a deterministic check and a
#: fragile one. Case and inner spacing are tolerated because a model re-typing the label
#: is still citing it; nothing else is.
_SOURCE_LABEL = re.compile(r"\[\s*source\s+(\d+)\s*\]", re.IGNORECASE)

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


@dataclass(frozen=True)
class CitationVerdict:
    """What :func:`check_citation_integrity` found, deterministically.

    Attributes:
        fabricated: Every source number the answer cited that this run did not retrieve,
            ascending. Empty means nothing was fabricated — which is *not* the same as
            "the answer is well cited"; an answer citing nothing also lands here.
        cited: Every source number the answer cited at all. Carried so a caller can tell
            "cited nothing" from "cited only real passages".
        retrieved: The source numbers this run actually had. Empty when the run retrieved
            nothing, which is what makes any citation at all a fabrication.
    """

    fabricated: tuple[int, ...] = ()
    cited: tuple[int, ...] = ()
    retrieved: tuple[int, ...] = ()

    #: Set when the check ran; a caller that sees ``False`` knows nothing was measured.
    ran: bool = field(default=True)

    @property
    def ok(self) -> bool:
        """Whether every citation in the answer points at a passage that was retrieved."""
        return not self.fabricated

    def reason(self) -> str:
        """A sentence naming the offending citations and what the run actually had."""
        cited = ", ".join(f"[source {n}]" for n in self.fabricated)
        if not self.retrieved:
            return (
                f"the answer cites {cited}, but this run retrieved no passages at all, "
                "so there is no source of that name for it to have come from"
            )
        have = ", ".join(f"[source {n}]" for n in self.retrieved)
        return (
            f"the answer cites {cited}, which this run did not retrieve; the passages "
            f"it was given were {have}"
        )


def retrieved_source_labels(contexts: list[str] | None) -> set[int]:
    """Return the source numbers this run genuinely had, from the context it built.

    Two shapes of ``contexts`` reach the output rail and both are read here, because
    guessing wrong in the strict direction would block a legitimate answer:

    * **one assembled context** — what the agent passes (``[state["context"]]``, built by
      :func:`aegis.retrieval.spotlight.build_spotlighted_context`). Its ``[source N]``
      labels are the authoritative list, and they are parsed out.
    * **a list of raw passages** — what a caller wiring the rail directly hands it. There
      are no labels to parse, so the numbers a model could legitimately use are the
      ordinals ``1..len(passages)``, matching how the assembler would have numbered them.

    The two are **unioned** rather than chosen between. A caller who supplies both shapes
    at once, or an assembler whose numbering later changes, then widens the permitted set
    rather than narrowing it — the right direction for a check whose finding blocks.

    Args:
        contexts: The retrieved passages the answer was given, in either shape.

    Returns:
        The source numbers that may legitimately be cited. Empty when nothing was
        retrieved.
    """
    passages = [c for c in (contexts or []) if isinstance(c, str) and c.strip()]
    labels = {int(n) for c in passages for n in _SOURCE_LABEL.findall(c)}
    return labels | set(range(1, len(passages) + 1))


def check_citation_integrity(answer: str, contexts: list[str] | None) -> CitationVerdict:
    """Check every source label the answer cites against the ones this run retrieved.

    Fully deterministic and offline: no model, no network, no configuration. It is the
    reason this rail is worth something on a deployment with no completer wired, and it
    is the machine-checkable half of the failure that motivated the rail — an audited run
    that retrieved nothing and answered by citing a document id that exists in no corpus.

    A citation to a passage that does not exist is not extrapolation and not a matter of
    degree. The answer is telling a reader where a claim came from, and the place does not
    exist; there is no legitimate turn of that shape, which is why the caller treats this
    finding the way it treats a contradiction rather than the way it treats "unsupported".

    Args:
        answer: The generated answer.
        contexts: The retrieved passages it was given (see
            :func:`retrieved_source_labels` for the two shapes).

    Returns:
        A :class:`CitationVerdict`. ``ok`` is ``True`` for an answer that cites nothing,
        because citing nothing is a different finding — the grounding rail's, not this
        one's — and this check must not become a rule that answers have to carry
        citations.
    """
    cited = sorted({int(n) for n in _SOURCE_LABEL.findall(answer or "")})
    retrieved = retrieved_source_labels(contexts)
    return CitationVerdict(
        fabricated=tuple(n for n in cited if n not in retrieved),
        cited=tuple(cited),
        retrieved=tuple(sorted(retrieved)),
    )


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
