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

from .ablation import (
    ABLATION_ARMS,
    AblationRun,
    Arm,
    ArmResult,
    CaseOutcome,
    Chunking,
    Comparison,
    Signal,
    compare,
    markdown_table,
)
from .corpus import SEED_CASES, SEED_CORPUS, EvalCase
from .goldset import (
    FIXTURE_GOLD_SET_PATH,
    MAX_SPAN_WORDS,
    GoldCase,
    GoldKind,
    dump_gold_set,
    gold_set_hash,
    hit_ranks,
    is_hit,
    load_gold_set,
)
from .harness import (
    DEFAULT_THRESHOLDS,
    EvalReport,
    EvalThresholds,
    build_eval_retriever,
    evaluate,
)
from .ir_metrics import (
    BOOTSTRAP_ITERATIONS,
    Interval,
    mcnemar_exact,
    ndcg_at_k,
    paired_bootstrap,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    wilson_interval,
)
from .judge import (
    JudgeSummary,
    JudgeUnavailableError,
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
    "ABLATION_ARMS",
    "BOOTSTRAP_ITERATIONS",
    "DEFAULT_METRICS",
    "DEFAULT_THRESHOLDS",
    "FIXTURE_GOLD_SET_PATH",
    "MAX_SPAN_WORDS",
    "ROUTER_EVAL_CASES",
    "SEED_CASES",
    "SEED_CORPUS",
    "AblationRun",
    "AggregateScore",
    "Arm",
    "ArmResult",
    "CaseOutcome",
    "CaseScore",
    "Chunking",
    "Comparison",
    "EvalCase",
    "EvalReport",
    "EvalThresholds",
    "GateCaseResult",
    "GoldCase",
    "GoldKind",
    "Interval",
    "JudgeSummary",
    "JudgeUnavailableError",
    "JudgeVerdict",
    "Metric",
    "MetricConfig",
    "MetricResult",
    "RegressionReport",
    "RouterEvalCase",
    "Signal",
    "aggregate",
    "build_eval_retriever",
    "compare",
    "dump_gold_set",
    "evaluate",
    "gold_set_hash",
    "hit_ranks",
    "is_hit",
    "judge_answer",
    "judge_enabled",
    "load_gold_set",
    "markdown_table",
    "mcnemar_exact",
    "ndcg_at_k",
    "paired_bootstrap",
    "precision_at_k",
    "recall_at_k",
    "reciprocal_rank",
    "run_regression_gate",
    "run_tool_selection_eval",
    "score_case",
    "summarize_verdicts",
    "wilson_interval",
]
