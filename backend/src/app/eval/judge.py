"""Strangler shim: ``app.eval.judge`` delegates to :mod:`aegis.evals.judge`.

The optional LLM-as-judge now lives in the standalone ``aegis.evals`` package, where it is
**inject-only** (``complete`` is required). This shim re-adds the backend convenience of
defaulting ``complete`` to :func:`app.core.llm.complete`, so a maintainer's opt-in graded
run (``TAIF_EVAL_LLM_JUDGE``) can call :func:`judge_answer` without wiring the gateway by
hand — every other symbol is re-exported unchanged.
"""

from __future__ import annotations

from aegis.evals.judge import (
    JUDGE_ENV_FLAG,
    JudgeSummary,
    JudgeVerdict,
    judge_enabled,
    summarize_verdicts,
)
from aegis.evals.judge import judge_answer as _aegis_judge_answer

__all__ = [
    "JUDGE_ENV_FLAG",
    "JudgeSummary",
    "JudgeVerdict",
    "judge_answer",
    "judge_enabled",
    "summarize_verdicts",
]


async def judge_answer(
    question: str, context: str, answer: str, *, complete=None  # noqa: ANN001
) -> JudgeVerdict:
    """Grade one answer, defaulting ``complete`` to the live gateway when not injected.

    Delegates to :func:`aegis.evals.judge.judge_answer` (inject-only); when ``complete`` is
    ``None`` this shim supplies :func:`app.core.llm.complete` so the backend's opt-in graded
    pass keeps working, mirroring the pre-extraction lazy fallback.
    """
    if complete is None:
        from app.core.llm import complete as complete  # noqa: PLC0415
    return await _aegis_judge_answer(question, context, answer, complete=complete)
