"""The DeepEval-pattern CI regression gate: pytest-native, per-metric thresholds.

Drives the real hybrid retriever over the seed corpus and evaluates declarative metrics
with per-metric thresholds. The agentic tool-selection case is now **inject-only**: with a
``route_fn`` + ``roster`` injected it exercises the router; with none injected it is
skipped and the RAG-path metrics stand alone. Fully offline — every score comes from real
retrieval or a real (injected) router decision (nothing hardcoded).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from aegis.evals.regression import (
    DEFAULT_METRICS,
    ROUTER_EVAL_CASES,
    Metric,
    run_regression_gate,
    run_tool_selection_eval,
)

pytestmark = pytest.mark.asyncio

_ROSTER = object()  # opaque sentinel — the fake router ignores it


@dataclass
class _Decision:
    """Minimal stand-in for a router decision (only ``.role`` is read)."""

    role: str


async def _fake_route(query, roster, *, complete=None):  # noqa: ANN001
    """A deterministic keyword router: memory-recall phrasings → ``memory``, else ``qa``.

    Not hardcoded to the eval cases — it decides from the query text, mirroring the real
    supervisor's keyword classifier so the agentic metric is a genuine behavior check.
    """
    q = query.lower()
    role = "memory" if ("know about me" in q or "remember" in q) else "qa"
    return _Decision(role=role)


async def test_regression_gate_passes_with_injected_router():
    """The gate PASSES on the seed corpus + the injected agentic case."""
    report = await run_regression_gate(route_fn=_fake_route, roster=_ROSTER)
    assert report.passed, f"Regression gate failed: {[c.name for c in report.failures()]}"
    assert not report.failures()
    # Covers every seed case + the corpus-precision case + the agentic case.
    assert len(report.cases) >= 3
    assert any(c.name.startswith("agentic:") for c in report.cases)
    assert any("context_precision" in c.name for c in report.cases)


async def test_agentic_case_skipped_without_router():
    """With no injected router the agentic case is skipped; RAG-path metrics preserved."""
    report = await run_regression_gate()
    assert report.passed
    assert not any(c.name.startswith("agentic:") for c in report.cases)
    # 6 seed retrieval cases + 1 corpus-precision case, no agentic case.
    assert len(report.cases) == 7


async def test_every_metric_result_is_well_formed():
    """Each metric result carries a real measured value in [0, 1] and a pass verdict."""
    report = await run_regression_gate(route_fn=_fake_route, roster=_ROSTER)
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
    report = await run_regression_gate(
        metrics=impossible, route_fn=_fake_route, roster=_ROSTER
    )
    assert not report.passed
    assert report.failures()
    assert len(report.failures()) == len(report.cases)


async def test_precision_regression_alone_trips_the_gate():
    """Raising only the precision floor above the observed corpus rate trips the gate."""
    strict_precision = (Metric(name="context_precision@1", threshold=0.95),)
    report = await run_regression_gate(
        metrics=strict_precision, route_fn=_fake_route, roster=_ROSTER
    )
    assert not report.passed
    failed = report.failures()
    assert len(failed) == 1
    assert "context_precision" in failed[0].name


async def test_tool_selection_metric_scores_router_choices():
    """The agentic metric correctly scores the injected router's role choices."""
    accuracy, details = await run_tool_selection_eval(
        route_fn=_fake_route, roster=_ROSTER
    )
    assert accuracy == pytest.approx(1.0)
    assert len(details) == len(ROUTER_EVAL_CASES)
    for query, expected, actual, ok in details:
        assert ok, f"router chose {actual!r}, expected {expected!r} for {query!r}"
        assert actual == expected
    # Both roles are genuinely exercised (memory-recall vs factual), not one bucket.
    assert {expected for _, expected, _, _ in details} == {"memory", "qa"}


async def test_failures_lists_only_failing_cases():
    """``failures()`` returns exactly the failing cases and nothing else."""
    only_tool_impossible = (Metric(name="tool_selection_accuracy", threshold=1.01),)
    report = await run_regression_gate(
        metrics=only_tool_impossible, route_fn=_fake_route, roster=_ROSTER
    )
    failed = report.failures()
    assert len(failed) == 1
    assert failed[0].name.startswith("agentic:")
    assert all(c.passed for c in report.cases if c not in failed)


async def test_regression_gate_is_deterministic():
    """Two offline runs produce identical pass/fail verdicts (deterministic gate)."""
    a = await run_regression_gate(route_fn=_fake_route, roster=_ROSTER)
    b = await run_regression_gate(route_fn=_fake_route, roster=_ROSTER)
    assert a.passed == b.passed
    assert [(c.name, c.passed) for c in a.cases] == [(c.name, c.passed) for c in b.cases]
