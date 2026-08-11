"""The CI quality gate: the offline eval must clear its thresholds on the seed corpus.

Drives the real hybrid retrieval pipeline (vector + graph + BM25 → RRF → rerank) over the
fixed seed corpus, computes deterministic metrics, and **fails** if retrieval quality
regresses below the bar. Fully offline — no network, no databases, no keys.

The optional LLM-as-judge pass is exercised only with an injected fake ``complete`` (so it
stays offline); it is now **inject-only** (no lazy host fallback).
"""

from __future__ import annotations

import pytest

from aegis.evals import DEFAULT_THRESHOLDS, SEED_CASES, evaluate
from aegis.evals.harness import EvalThresholds, build_eval_retriever
from aegis.evals.judge import JUDGE_ENV_FLAG, judge_answer, judge_enabled
from aegis.evals.metrics import score_case
from aegis.gateway import LLMResult

pytestmark = pytest.mark.asyncio


async def test_seed_corpus_clears_quality_gate():
    """The deterministic eval must PASS on the seed corpus (the CI gate)."""
    report = await evaluate()
    assert report.passed, f"Eval quality gate failed: {report.failures()}"


async def test_gate_metrics_are_above_thresholds():
    """Each aggregate metric is at or above its configured floor (explicit bounds)."""
    report = await evaluate()
    agg, thr = report.aggregate, report.thresholds
    assert agg.cases == len(SEED_CASES)
    assert agg.context_precision >= thr.min_context_precision
    assert agg.context_recall >= thr.min_context_recall
    assert agg.groundedness >= thr.min_groundedness


async def test_eval_is_deterministic():
    """Two runs of the offline eval produce byte-identical aggregate scores."""
    a = (await evaluate()).aggregate
    b = (await evaluate()).aggregate
    assert (a.context_precision, a.context_recall, a.groundedness) == (
        b.context_precision,
        b.context_recall,
        b.groundedness,
    )


async def test_gate_trips_on_a_regression():
    """An impossibly high bar makes the gate FAIL — proving it can catch a regression."""
    strict = EvalThresholds(
        min_context_precision=1.0,
        min_context_recall=1.0,
        min_groundedness=1.0,
        precision_k=1,
    )
    report = await evaluate(thresholds=strict)
    # Precision@1 is < 1.0 on the seed corpus (one query mis-ranks), so the gate trips.
    assert not report.passed
    assert report.failures()


async def test_real_pipeline_produces_rrf_multi_origin_provenance():
    """The eval drives the genuine hybrid pipeline: RRF fusion over multiple origins."""
    retriever = build_eval_retriever()
    result = await retriever.retrieve(SEED_CASES[0].query)
    assert result.provenance.fusion.value == "rrf"
    assert len(result.provenance.origins) >= 2  # e.g. vector + graph (+ bm25)
    # A per-case score is well-formed and grounded on the top-ranked refund doc.
    score = score_case(SEED_CASES[0], result, precision_k=1)
    assert score.context_recall == 1.0
    assert score.retrieved_docs[0] == "kb-refunds"


async def test_default_thresholds_are_sane():
    """The shipped thresholds are within (0, 1] so the gate is meaningfully strict."""
    thr = DEFAULT_THRESHOLDS
    for value in (
        thr.min_context_precision,
        thr.min_context_recall,
        thr.min_groundedness,
    ):
        assert 0.0 < value <= 1.0


# ── Optional LLM-as-judge (offline via an injected fake; inject-only) ──


async def test_llm_judge_parses_scores_offline():
    """The judge parses a reasoning-model verdict — exercised with a fake ``complete``."""

    async def fake_complete(role, messages, **kwargs):  # noqa: ANN001, ANN003
        return LLMResult(content='{"groundedness": 0.9, "relevance": 1.0}')

    verdict = await judge_answer(
        "How long does a refund take?",
        "Refunds are issued within 5 to 7 business days.",
        "Refunds take 5 to 7 business days.",
        complete=fake_complete,
    )
    assert verdict.groundedness == pytest.approx(0.9)
    assert verdict.relevance == pytest.approx(1.0)


async def test_judge_answer_is_inject_only():
    """With no injected ``complete`` the judge is disabled and raises (no host fallback)."""
    with pytest.raises(ValueError, match="inject-only"):
        await judge_answer("q", "ctx", "a", complete=None)


async def test_evaluate_wires_llm_judge_when_complete_is_injected():
    """``evaluate`` actually CALLS the judge when a ``complete`` is injected, and the
    judge's verdict flows into the report — proven offline with a fake ``complete``."""
    calls: list[list[dict]] = []

    async def fake_complete(role, messages, **kwargs):  # noqa: ANN001, ANN003
        calls.append(messages)
        return LLMResult(content='{"groundedness": 0.8, "relevance": 0.7}')

    report = await evaluate(complete=fake_complete)

    # The judge ran once per seed case (i.e. it was genuinely wired into the run) ...
    assert len(calls) == len(SEED_CASES)
    # ... and its model-graded summary is surfaced on the report.
    assert report.judge is not None
    assert report.judge.cases == len(SEED_CASES)
    assert report.judge.groundedness == pytest.approx(0.8)
    assert report.judge.relevance == pytest.approx(0.7)
    # The deterministic gate is unaffected by the (optional) judge pass.
    assert report.passed


async def test_evaluate_skips_judge_offline_by_default():
    """With no ``complete`` the judge never runs and the report degrades gracefully."""
    report = await evaluate()
    assert report.judge is None
    assert report.passed


@pytest.mark.skipif(
    not judge_enabled(),
    reason=f"LLM-as-judge disabled; set {JUDGE_ENV_FLAG}=1 to run the graded pass.",
)
async def test_judge_env_flag_is_readable():
    """When opted in, :func:`judge_enabled` reports the env flag (smoke)."""
    assert judge_enabled() is True
