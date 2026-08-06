"""The DeepEval-pattern CI regression gate: pytest-native, per-metric thresholds.

This is the native DeepEval-*pattern* gate (``app.eval.regression``) that sits beside the
RAGAS-style aggregate gate (``test_eval_gate.py``). It drives the real hybrid retriever
over the seed corpus and the real supervisor router, evaluates declarative metrics with
per-metric thresholds, and **fails the build** on a regression. It is fully offline — no
network, no databases, no keys — and computes every score from real retrieval / real
router decisions (nothing hardcoded). Real ``deepeval`` can later slot in as a metric
backend without changing this gate's shape.
"""

from __future__ import annotations

import pytest

from app.eval.regression import (
    DEFAULT_METRICS,
    ROUTER_EVAL_CASES,
    Metric,
    run_regression_gate,
    run_tool_selection_eval,
)

pytestmark = pytest.mark.asyncio


async def test_regression_gate_passes_on_seed_corpus():
    """The gate PASSES on the seed corpus with the default per-metric thresholds."""
    report = await run_regression_gate()
    assert report.passed, f"Regression gate failed: {[c.name for c in report.failures()]}"
    assert not report.failures()
    # It covers every seed case plus the corpus-precision case plus the agentic case.
    assert len(report.cases) >= 3
    assert any(c.name.startswith("agentic:") for c in report.cases)
    assert any("context_precision" in c.name for c in report.cases)


async def test_every_metric_result_is_well_formed():
    """Each metric result carries a real measured value in [0, 1] and a pass verdict."""
    report = await run_regression_gate()
    seen: set[str] = set()
    for case in report.cases:
        assert case.metrics, f"case {case.name} has no metrics"
        for m in case.metrics:
            seen.add(m.name)
            assert 0.0 <= m.value <= 1.0
            assert m.passed == (m.value >= m.threshold)
    # All four declarative metrics were actually exercised.
    assert seen == {metric.name for metric in DEFAULT_METRICS}


async def test_gate_trips_when_thresholds_are_impossible():
    """Impossibly high thresholds make the gate FAIL — proving it actually gates."""
    impossible = tuple(
        Metric(name=m.name, threshold=1.01, higher_is_better=m.higher_is_better)
        for m in DEFAULT_METRICS
    )
    report = await run_regression_gate(metrics=impossible)
    assert not report.passed
    # Every case trips because no measured value can reach 1.01.
    assert report.failures()
    assert len(report.failures()) == len(report.cases)


async def test_precision_regression_alone_trips_the_gate():
    """Raising only the precision floor above the observed corpus rate trips the gate.

    Recall/groundedness/tool stay at their defaults and pass, so this proves the
    precision metric is genuinely wired into the pass/fail decision (not decorative).
    """
    strict_precision = (Metric(name="context_precision@1", threshold=0.95),)
    report = await run_regression_gate(metrics=strict_precision)
    assert not report.passed
    failed = report.failures()
    assert len(failed) == 1
    assert "context_precision" in failed[0].name


async def test_tool_selection_metric_scores_router_choices():
    """The agentic metric correctly scores the router's deterministic role choices."""
    accuracy, details = await run_tool_selection_eval()
    # The deterministic router picks the expected role for every representative query.
    assert accuracy == pytest.approx(1.0)
    assert len(details) == len(ROUTER_EVAL_CASES)
    for query, expected, actual, ok in details:
        assert ok, f"router chose {actual!r}, expected {expected!r} for {query!r}"
        assert actual == expected
    # Both roles are genuinely exercised (memory-recall vs factual), not one bucket.
    assert {expected for _, expected, _, _ in details} == {"memory", "qa"}


async def test_failures_lists_only_failing_cases():
    """``failures()`` returns exactly the failing cases and nothing else."""
    # Fail only the agentic case by demanding perfect+ tool selection while leaving the
    # retrieval metrics at their (passing) defaults.
    only_tool_impossible = (Metric(name="tool_selection_accuracy", threshold=1.01),)
    report = await run_regression_gate(metrics=only_tool_impossible)
    failed = report.failures()
    assert len(failed) == 1
    assert failed[0].name.startswith("agentic:")
    # The passing cases are absent from failures() but present in cases.
    assert failed[0] not in [c for c in report.cases if c.passed]
    assert all(c.passed for c in report.cases if c not in failed)


async def test_regression_gate_is_deterministic():
    """Two offline runs produce identical pass/fail verdicts (deterministic gate)."""
    a = await run_regression_gate()
    b = await run_regression_gate()
    assert a.passed == b.passed
    assert [(c.name, c.passed) for c in a.cases] == [(c.name, c.passed) for c in b.cases]
