"""Optional LLM-as-judge for a richer, model-graded eval pass (off by default).

The deterministic gate (:mod:`aegis.evals.harness`) is the CI bar and stays fully offline.
This module adds a *reasoning-model* judge — DeepSeek-R1 / Phi-4-reasoning routed through
the :class:`~aegis.core.models.ModelRole.REASONING` role at the gateway — that grades an
answer's **groundedness** and **relevance** against its retrieved context. It is gated
behind :func:`judge_enabled` (the ``TAIF_EVAL_LLM_JUDGE`` env flag), so a normal ``pytest``
run never touches the network; a maintainer opts in to run the graded pass.

The chat-completion callable is **inject-only**: :func:`judge_answer` requires a
``complete`` to be passed (``complete=None`` means the judge is disabled). There is no
lazy fallback to a host completer — the caller wires the gateway in.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass

from aegis.core.models import ModelRole

#: Environment flag that opts a run into the (networked) LLM-as-judge pass.
JUDGE_ENV_FLAG = "TAIF_EVAL_LLM_JUDGE"

#: A ``<think>…</think>`` (or unterminated ``<think>…``) reasoning preamble — the
#: routine wrapper a REASONING-role model (DeepSeek-R1 / Phi-4-reasoning) puts in
#: front of its JSON. Stripped before parsing so the *expected* shape parses.
_THINK_BLOCK = re.compile(r"<think>.*?(?:</think>|\Z)", re.DOTALL | re.IGNORECASE)

#: A ```` ```json … ``` ```` fence wrapper.
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

#: How much of an unparseable reply to quote back in the error (bounded, so a
#: judge outage cannot dump a whole model response into a log line).
_SNIPPET_LEN = 240

_JUDGE_SYSTEM = (
    "You are a strict evaluation judge for a retrieval-augmented answer. "
    "Given a QUESTION, the retrieved CONTEXT, and an ANSWER, rate two things on a "
    "0.0-1.0 scale: (1) groundedness — is every claim in the answer supported by the "
    "context; (2) relevance — does the answer address the question. Respond with ONLY "
    'a JSON object {"groundedness": <float>, "relevance": <float>} and nothing else.'
)


class JudgeUnavailableError(RuntimeError):
    """The judge produced no usable verdict — a judge **failure**, never a score.

    Raised by :func:`judge_answer` when the model's reply cannot be parsed into a
    verdict (non-JSON prose, a truncated ``<think>`` ramble, a missing/NaN field, …).

    This exists so a judge outage is *distinguishable from a genuine ``0.0``*. Any
    caller that gates a release on the judge MUST let this propagate (fail closed):
    substituting ``0.0`` makes a draft and its baseline score identically ``0.0``,
    which silently PASSES a ``margin=0.0`` eval gate and auto-promotes every
    candidate prompt. A control that cannot run must stop the release, not wave it
    through.
    """


def judge_enabled() -> bool:
    """Return whether the LLM-as-judge pass is enabled (``TAIF_EVAL_LLM_JUDGE`` truthy)."""
    return os.environ.get(JUDGE_ENV_FLAG, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class JudgeVerdict:
    """A judge's scores for one answer.

    Attributes:
        groundedness: 0.0-1.0 — is the answer supported by the retrieved context.
        relevance: 0.0-1.0 — does the answer address the question.
    """

    groundedness: float
    relevance: float


@dataclass(frozen=True)
class JudgeSummary:
    """Corpus-level means over per-answer :class:`JudgeVerdict` scores.

    Attributes:
        groundedness: Mean model-graded groundedness across the judged answers.
        relevance: Mean model-graded relevance across the judged answers.
        cases: How many answers the judge graded.
    """

    groundedness: float
    relevance: float
    cases: int


def summarize_verdicts(verdicts: Sequence[JudgeVerdict]) -> JudgeSummary:
    """Average per-answer verdicts into a :class:`JudgeSummary` (zeros when empty)."""
    n = len(verdicts)
    if n == 0:
        return JudgeSummary(groundedness=0.0, relevance=0.0, cases=0)
    return JudgeSummary(
        groundedness=sum(v.groundedness for v in verdicts) / n,
        relevance=sum(v.relevance for v in verdicts) / n,
        cases=n,
    )


def _json_candidates(content: str) -> list[str]:
    """Yield progressively-more-salvaged JSON candidates from a raw judge reply.

    The judge runs on :data:`~aegis.core.models.ModelRole.REASONING`, whose models
    routinely wrap the object in a ``<think>`` preamble, a markdown fence, or a
    sentence of prose. Those are *expected* formatting drift, not a judge failure —
    so they are stripped/extracted here. Anything left unparseable after this is a
    real failure and becomes a :class:`JudgeUnavailableError`.
    """
    text = (content or "").strip()
    candidates = [text]

    stripped = _FENCE.sub("", _THINK_BLOCK.sub("", text)).strip()
    if stripped and stripped != text:
        candidates.append(stripped)

    # First balanced {...} object in whatever is left (prose before/after the JSON).
    depth = 0
    start = -1
    for i, ch in enumerate(stripped):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                candidates.append(stripped[start : i + 1])
                break
    return candidates


def _parse_verdict(content: str) -> JudgeVerdict:
    """Parse the judge's reply into a :class:`JudgeVerdict` (clamped to ``[0, 1]``).

    Tolerant of the reasoning-model formatting drift the judge actually sees
    (``<think>`` preamble, markdown fence, prose around the object). **Not** tolerant
    of an actually-unusable reply: that raises rather than returning zeros, so the
    caller cannot mistake a broken judge for a badly-scored answer.

    Raises:
        JudgeUnavailableError: When no usable verdict can be recovered.
    """
    for candidate in _json_candidates(content):
        try:
            data = json.loads(candidate)
            g = float(data["groundedness"])
            r = float(data["relevance"])
        except (json.JSONDecodeError, TypeError, ValueError, KeyError):
            continue
        # NaN/inf would silently poison every downstream comparison (``nan < x`` is
        # always False, so a NaN score PASSES a gate) — treat it as unparseable.
        if not (g == g and r == r) or g in (float("inf"), float("-inf")) or r in (
            float("inf"),
            float("-inf"),
        ):
            continue
        clamp = lambda x: max(0.0, min(1.0, x))  # noqa: E731 - tiny local clamp
        return JudgeVerdict(groundedness=clamp(g), relevance=clamp(r))

    snippet = (content or "").strip()[:_SNIPPET_LEN]
    raise JudgeUnavailableError(
        "LLM judge returned no usable verdict (expected a JSON object with float "
        f"'groundedness' and 'relevance'); got: {snippet!r}"
    )


async def judge_answer(
    question: str, context: str, answer: str, *, complete=None  # noqa: ANN001
) -> JudgeVerdict:
    """Grade one answer with the reasoning-model judge via the injected gateway.

    Args:
        question: The user question.
        context: The retrieved context the answer should be grounded in.
        answer: The model's answer to grade.
        complete: The chat-completion callable (shape of a gateway ``complete``);
            **required** — ``None`` means the judge is disabled and raises ``ValueError``.

    Returns:
        The :class:`JudgeVerdict` (groundedness + relevance in ``[0, 1]``).

    Raises:
        ValueError: When ``complete`` is ``None`` (the judge is inject-only).
        JudgeUnavailableError: When the model's reply carries no usable verdict.
            **Never** caught-and-zeroed here: a judge that could not run must not be
            indistinguishable from an answer that scored zero (see
            :class:`JudgeUnavailableError`).
    """
    if complete is None:
        raise ValueError(
            "judge_answer requires an injected `complete` callable; the LLM-as-judge is "
            "inject-only (complete=None disables it)."
        )
    messages = [
        {"role": "system", "content": _JUDGE_SYSTEM},
        {
            "role": "user",
            "content": (
                f"QUESTION:\n{question}\n\nCONTEXT:\n{context}\n\nANSWER:\n{answer}"
            ),
        },
    ]
    result = await complete(
        ModelRole.REASONING,
        messages,
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    return _parse_verdict(result.content)
