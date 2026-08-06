"""Offline evaluation harness + quality gate for the hybrid retrieval path.

This is the CI **quality gate** promised by Phase 6 (``docs/ARCHITECTURE_REVIEW.md``
§7): a small, fully-offline eval over the hybrid (vector + graph + BM25 → RRF →
rerank) retrieval and answer path that produces *deterministic* metrics and can
**fail** a build if retrieval quality regresses on a fixed seed corpus.

Design goals:

- **Offline & deterministic.** The default run needs no network, no keys, no
  Postgres/Neo4j/Redis. It builds the databaseless
  :class:`~app.retrieval.memory.InMemoryKnowledgeBackend` over a fixed seed corpus,
  supplies a deterministic embedding + a pass-through reranker, and computes overlap
  metrics — so the same input always yields the same score.
- **Real pipeline.** The eval drives the *actual* :class:`~app.retrieval.pipeline.Retriever`
  (real RRF fusion, real spotlight assembly), not a stub — a regression in fusion or
  assembly moves the numbers.
- **Optional LLM-as-judge (wired).** A reasoning-model judge (DeepSeek-R1 /
  Phi-4-reasoning via the gateway) is actually *called* by :func:`evaluate` whenever a
  chat-completion ``complete`` callable is injected: it grades each case for model-graded
  groundedness + relevance and its :class:`~app.eval.judge.JudgeSummary` is surfaced on
  the report (``report.judge``). With no ``complete`` the judge is skipped so the default
  gate stays fully offline; a maintainer opts into a real gateway run via
  ``TAIF_EVAL_LLM_JUDGE`` (see :func:`app.eval.judge.judge_enabled`).

Note the retrieval-quality metrics are **RAGAS-style deterministic proxies** (lexical
overlap), not the ``ragas`` library — see :mod:`app.eval.metrics`.

Public surface:

- :data:`SEED_CASES` / :data:`SEED_CORPUS` — the fixed eval fixtures.
- :func:`evaluate` — run the deterministic eval (optionally driving the LLM-as-judge when
  given a ``complete`` callable), returning an :class:`EvalReport`.
- :data:`DEFAULT_THRESHOLDS` — the quality bar the CI gate asserts against.
"""

from __future__ import annotations

from .corpus import SEED_CASES, SEED_CORPUS, EvalCase
from .harness import DEFAULT_THRESHOLDS, EvalReport, EvalThresholds, evaluate
from .judge import JudgeSummary, JudgeVerdict, judge_answer, judge_enabled
from .metrics import CaseScore, aggregate, score_case
from .regression import DEFAULT_METRICS, RegressionReport, run_regression_gate

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
