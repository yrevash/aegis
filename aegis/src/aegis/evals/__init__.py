"""Aegis evals — a pure, importable offline evaluation library + regression gate.

RAGAS-style deterministic lexical proxies (context-precision/recall/groundedness), an
optional **injected** LLM-as-judge, and a DeepEval-pattern per-metric regression gate —
all hand-rolled, with **no heavy deps** (no ``ragas``/``deepeval``) and **no ORM**. It
drives the real hybrid :class:`aegis.retrieval.Retriever` over a fixed seed corpus with a
deterministic local embedding + a pass-through reranker, so the default run is fully
offline and deterministic.

Everything is inject-only where a model is involved: the LLM-as-judge takes a ``complete``
callable (``None`` disables it), and the agentic tool-selection case takes a ``route_fn``
+ ``roster`` (absent, it is skipped and the RAG-path metrics stand alone). Importing this
package pulls ``aegis.retrieval`` + ``aegis.gateway`` **types** but no ``fastapi`` /
``litellm`` (see ``tests/evals/test_isolation.py``).

Optional AG-UI streaming lives in :mod:`aegis.evals.stream`.
"""

from __future__ import annotations

from .corpus import SEED_CASES, SEED_CORPUS, EvalCase
from .harness import (
    DEFAULT_THRESHOLDS,
    EvalReport,
    EvalThresholds,
    build_eval_retriever,
    evaluate,
)
from .judge import (
    JudgeSummary,
    JudgeVerdict,
    judge_answer,
    judge_enabled,
    summarize_verdicts,
)
from .metrics import AggregateScore, CaseScore, MetricConfig, aggregate, score_case
from .regression import (
    DEFAULT_METRICS,
    ROUTER_EVAL_CASES,
    GateCaseResult,
    Metric,
    MetricResult,
    RegressionReport,
    RouterEvalCase,
    run_regression_gate,
    run_tool_selection_eval,
)

__all__ = [
    "DEFAULT_METRICS",
    "DEFAULT_THRESHOLDS",
    "ROUTER_EVAL_CASES",
    "SEED_CASES",
    "SEED_CORPUS",
    "AggregateScore",
    "CaseScore",
    "EvalCase",
    "EvalReport",
    "EvalThresholds",
    "GateCaseResult",
    "JudgeSummary",
    "JudgeVerdict",
    "Metric",
    "MetricConfig",
    "MetricResult",
    "RegressionReport",
    "RouterEvalCase",
    "aggregate",
    "build_eval_retriever",
    "evaluate",
    "judge_answer",
    "judge_enabled",
    "run_regression_gate",
    "run_tool_selection_eval",
    "score_case",
    "summarize_verdicts",
]
