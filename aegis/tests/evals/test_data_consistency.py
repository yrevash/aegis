"""Data-consistency: one authoritative number per metric across every surface.

The value **computed** by the gate == the value **streamed** (`EVAL_RESULT`) == the value
**persisted** (an :class:`~aegis.ops.models.EvalResult` row) == the value an **accessor**
returns for the dashboard. These tests pin that no surface recomputes or rounds a metric
differently, that thresholds are tunable (and a change flips the gate outcome *and* the
accessor), and that RAGAS answer-relevancy is reported honestly as not-computed.

Everything is offline/deterministic: the gate drives the real hybrid retriever with a local
hash embedding + pass-through reranker, so the numbers are stable.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from aegis.evals import evaluate, run_regression_gate
from aegis.evals.metrics import MetricConfig
from aegis.evals.regression import DEFAULT_METRICS, Metric
from aegis.evals.stream import stream_regression_report
from aegis.ops.models import EvalResult

pytestmark = pytest.mark.asyncio


# ── A minimal emitter that captures the single EVAL_RESULT payload ────────────
class _FakeStepScope:
    async def __aenter__(self) -> _FakeStepScope:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _CapturingEmitter:
    """Captures the one custom-event payload the stream helper emits."""

    def __init__(self) -> None:
        self.payload: dict | None = None

    def step(self, name: str, kind: object) -> _FakeStepScope:
        return _FakeStepScope()

    async def custom(self, name: str, value: dict) -> None:
        self.payload = value


_ROSTER = object()


@dataclass
class _Decision:
    role: str


async def _fake_route(query, roster, *, complete=None):  # noqa: ANN001
    q = query.lower()
    return _Decision(role="memory" if ("know about me" in q or "remember" in q) else "qa")


# ── The core proof: computed == streamed == persisted == accessor ─────────────
async def test_regression_computed_equals_streamed_equals_persisted_equals_accessor():
    """Every surface reports the *identical* float for each metric — no drift, no rounding."""
    report = await run_regression_gate(route_fn=_fake_route, roster=_ROSTER)

    # 1. COMPUTED — the raw per-case mean, computed here independently of the accessor.
    buckets: dict[str, list[float]] = {}
    for case in report.cases:
        for m in case.metrics:
            buckets.setdefault(m.name, []).append(m.value)
    computed = {name: sum(vals) / len(vals) for name, vals in buckets.items()}

    # 2. ACCESSOR — the dashboard-facing single source.
    accessor = {c.name: c.value for c in report.metric_configs()}

    # 3. STREAMED — the EVAL_RESULT payload.
    emitter = _CapturingEmitter()
    await stream_regression_report(emitter, report)
    assert emitter.payload is not None
    streamed = emitter.payload["metrics"]

    # 4. PERSISTED — real EvalResult ORM rows built from the report's projection.
    rows = [EvalResult(**row) for row in report.to_eval_rows(run_id="consistency")]
    persisted = {r.metric: r.score for r in rows}

    # All four maps are byte-identical (exact float equality, no tolerance).
    assert computed == accessor == streamed == persisted
    # And the overall is one shared definition across accessor + stream.
    assert emitter.payload["overall"] == report.overall()
    # The persisted pass verdicts equal the accessor's, too.
    assert {r.metric: r.passed for r in rows} == {
        c.name: c.passed for c in report.metric_configs()
    }


async def test_stream_payload_metric_configs_match_accessor_exactly():
    """The streamed `metricConfigs` list is the accessor's `as_dict()`, field for field."""
    report = await run_regression_gate(route_fn=_fake_route, roster=_ROSTER)
    emitter = _CapturingEmitter()
    await stream_regression_report(emitter, report)
    assert emitter.payload is not None
    assert emitter.payload["metricConfigs"] == [
        c.as_dict() for c in report.metric_configs()
    ]


async def test_as_dict_is_lossless_projection_of_the_report():
    """`RegressionReport.as_dict()` carries the exact per-case + per-metric numbers."""
    report = await run_regression_gate(route_fn=_fake_route, roster=_ROSTER)
    data = report.as_dict()
    assert data["passed"] is report.passed
    assert data["overall"] == report.overall()
    # Per-metric block equals the accessor.
    assert data["metrics"] == [c.as_dict() for c in report.metric_configs()]
    # Per-case values round-trip exactly (no rounding).
    for case_dict, case in zip(data["cases"], report.cases, strict=True):
        assert case_dict["name"] == case.name
        assert [m["value"] for m in case_dict["metrics"]] == [
            m.value for m in case.metrics
        ]


async def test_metric_configs_aggregate_repeated_metrics_not_last_write():
    """A metric present on many cases folds into ONE entry (mean), not the last case's value.

    This is the drift bug a naive ``{m.name: m.value}`` flatten would cause: same-named
    metrics across 6 cases collapsing to whichever case streamed last.
    """
    report = await run_regression_gate()  # 6 retrieval cases, each with recall+grounded
    configs = {c.name: c for c in report.metric_configs()}
    recall = configs["context_recall"]
    # Folded across every contributing case, not a single case's reading.
    assert recall.cases == 6
    manual = [m.value for case in report.cases for m in case.metrics if m.name == "context_recall"]
    assert recall.value == pytest.approx(sum(manual) / len(manual))
    # Names are unique in the accessor even though cases repeat them.
    names = [c.name for c in report.metric_configs()]
    assert len(names) == len(set(names))


# ── Tunable thresholds: a change flips the gate outcome AND the accessor ──────
async def test_threshold_change_flips_gate_and_accessor():
    """Raising a metric's threshold above its observed value flips passed on gate + accessor."""
    lax = await run_regression_gate(route_fn=_fake_route, roster=_ROSTER)
    lax_precision = next(c for c in lax.metric_configs() if "context_precision" in c.name)
    assert lax.passed and lax_precision.passed

    # Same run, only the precision floor raised above the observed corpus rate.
    strict_metrics = (Metric(name="context_precision@1", threshold=0.95),)
    strict = await run_regression_gate(
        metrics=strict_metrics, route_fn=_fake_route, roster=_ROSTER
    )
    strict_precision = next(
        c for c in strict.metric_configs() if "context_precision" in c.name
    )
    # The measured VALUE is unchanged (same retrieval); only the verdict flipped.
    assert strict_precision.value == lax_precision.value
    assert strict_precision.threshold == 0.95
    assert not strict_precision.passed
    assert not strict.passed


async def test_default_metric_thresholds_are_the_effective_config():
    """With no override the accessor's thresholds are exactly the shipped DEFAULT_METRICS."""
    report = await run_regression_gate(route_fn=_fake_route, roster=_ROSTER)
    by_name = {m.name: m for m in DEFAULT_METRICS}
    for c in report.metric_configs():
        assert c.threshold == by_name[c.name].threshold
        assert c.higher_is_better == by_name[c.name].higher_is_better


async def test_metric_result_carries_higher_is_better_through():
    """`Metric.evaluate` threads `higher_is_better` onto the result (for the accessor)."""
    lower_better = Metric(name="latency", threshold=0.5, higher_is_better=False)
    ok = lower_better.evaluate(0.4)
    assert ok.higher_is_better is False and ok.passed is True
    bad = lower_better.evaluate(0.6)
    assert bad.higher_is_better is False and bad.passed is False


# ── EvalReport (RAGAS-style) surface: same accessor guarantees ────────────────
async def test_eval_report_accessor_matches_aggregate_and_persisted():
    """`EvalReport` accessor == its `AggregateScore` == the rows it projects."""
    report = await evaluate()
    configs = {c.name: c for c in report.metric_configs()}
    agg = report.aggregate
    assert configs[f"context_precision@{report.thresholds.precision_k}"].value == (
        agg.context_precision
    )
    assert configs["context_recall"].value == agg.context_recall
    assert configs["groundedness"].value == agg.groundedness

    rows = [EvalResult(**row) for row in report.to_eval_rows(run_id="eval")]
    persisted = {r.metric: r.score for r in rows}
    for name, cfg in configs.items():
        if cfg.computed and cfg.value is not None:
            assert persisted[name] == cfg.value


async def test_answer_relevancy_reported_honestly_as_not_computed():
    """RAGAS answer-relevancy is surfaced as not-computed (value=None) — never faked."""
    report = await evaluate()
    relevancy = next(c for c in report.metric_configs() if c.name == "answer_relevancy")
    assert relevancy.computed is False
    assert relevancy.value is None
    # And it is excluded from persisted rows (a row must carry a real score).
    assert "answer_relevancy" not in {row["metric"] for row in report.to_eval_rows()}
    # It still appears in the dashboard projection, flagged honestly.
    metrics = report.as_dict()["metrics"]
    ar = next(m for m in metrics if m["name"] == "answer_relevancy")
    assert ar["computed"] is False and ar["value"] is None


async def test_eval_report_as_dict_judge_none_offline():
    """Offline (no injected complete) the report's judge block is honestly null."""
    report = await evaluate()
    assert report.as_dict()["judge"] is None


async def test_metric_config_as_dict_shape():
    """`MetricConfig.as_dict` exposes the tunable definition + reading for the dashboard."""
    cfg = MetricConfig(
        name="groundedness",
        threshold=0.85,
        higher_is_better=True,
        value=0.9,
        passed=True,
        cases=6,
    )
    assert cfg.as_dict() == {
        "name": "groundedness",
        "threshold": 0.85,
        "higherIsBetter": True,
        "value": 0.9,
        "passed": True,
        "cases": 6,
        "computed": True,
    }
