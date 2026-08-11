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
from collections.abc import Sequence
from dataclasses import dataclass

from aegis.core.models import ModelRole

#: Environment flag that opts a run into the (networked) LLM-as-judge pass.
JUDGE_ENV_FLAG = "TAIF_EVAL_LLM_JUDGE"

_JUDGE_SYSTEM = (
    "You are a strict evaluation judge for a retrieval-augmented answer. "
    "Given a QUESTION, the retrieved CONTEXT, and an ANSWER, rate two things on a "
    "0.0-1.0 scale: (1) groundedness — is every claim in the answer supported by the "
    "context; (2) relevance — does the answer address the question. Respond with ONLY "
    'a JSON object {"groundedness": <float>, "relevance": <float>} and nothing else.'
)


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


def _parse_verdict(content: str) -> JudgeVerdict:
    """Parse the judge's JSON reply into a :class:`JudgeVerdict` (clamped to [0, 1])."""
    try:
        data = json.loads(content)
        g = float(data["groundedness"])
        r = float(data["relevance"])
    except (json.JSONDecodeError, TypeError, ValueError, KeyError):
        return JudgeVerdict(groundedness=0.0, relevance=0.0)
    clamp = lambda x: max(0.0, min(1.0, x))  # noqa: E731 - tiny local clamp
    return JudgeVerdict(groundedness=clamp(g), relevance=clamp(r))


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
