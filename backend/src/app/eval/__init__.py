"""Strangler shim: ``app.eval`` delegates to the standalone :mod:`aegis.evals` package.

The offline evaluation harness + CI quality gate (RAGAS-style deterministic proxies, the
optional injected LLM-as-judge, the DeepEval-pattern regression gate) now live in the
importable ``aegis.evals`` package. This package re-exports the historical public surface
so every ``from app.eval import ...`` call site is unchanged, and its submodules
(:mod:`app.eval.judge` / :mod:`app.eval.regression`) re-add the backend conveniences —
defaulting the judge's ``complete`` to :func:`app.core.llm.complete` and the regression
gate's router to :func:`app.agent.router.route_query`.

``python -m app.eval.harness`` / ``python -m app.eval.regression`` keep working.
"""

from __future__ import annotations

from aegis.evals import (
    DEFAULT_METRICS,
    DEFAULT_THRESHOLDS,
    SEED_CASES,
    SEED_CORPUS,
    CaseScore,
    EvalCase,
    EvalReport,
    EvalThresholds,
    JudgeSummary,
    JudgeVerdict,
    RegressionReport,
    aggregate,
    evaluate,
    score_case,
)

# Re-export the shim-wrapped judge + regression entry points (backend conveniences).
from .judge import judge_answer, judge_enabled
from .regression import run_regression_gate

__all__ = [
    "DEFAULT_METRICS",
    "DEFAULT_THRESHOLDS",
    "SEED_CASES",
    "SEED_CORPUS",
    "CaseScore",
    "EvalCase",
    "EvalReport",
    "EvalThresholds",
    "JudgeSummary",
    "JudgeVerdict",
    "RegressionReport",
    "aggregate",
    "evaluate",
    "judge_answer",
    "judge_enabled",
    "run_regression_gate",
    "score_case",
]
