"""Tests for the real per-node / per-run latency aggregation.

These assert that (a) OTel spans record real wall-clock duration, and (b)
``latency_summary`` computes exact percentile math over real recorded samples —
per-node p50/p95/max/count, run-duration percentiles, the slowest node — with a
bounded, per-process rolling window and an honest empty state. No fabricated p95.
"""

from __future__ import annotations

import time
from collections import deque

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

import aegis.observability.latency as latency_mod
import aegis.observability.otel as otel_mod
from aegis.observability import (
    latency_summary,
    record_run_latency,
    reset_latency_window,
    span,
)
from aegis.observability.latency import (
    DEFAULT_WINDOW_CAPACITY,
    percentile,
)
from aegis.observability.semconv import SpanKind


@pytest.fixture(autouse=True)
def _clean_window():
    """Isolate the per-process rolling window around every test."""
    reset_latency_window()
    yield
    reset_latency_window()


@pytest.fixture
def memory_provider(monkeypatch):
    """Bind an in-memory tracer provider so span timings are captured."""
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(otel_mod, "_provider", provider)
    return exporter


# ── Span duration is real ─────────────────────────────────────────────────────


def test_span_over_a_sleep_records_positive_wallclock_duration(memory_provider):
    """A span wrapping a real sleep records a strictly positive duration."""
    with span(SpanKind.CHAIN, "work"):
        time.sleep(0.01)

    finished = memory_provider.get_finished_spans()
    assert len(finished) == 1
    s = finished[0]
    duration_ms = (s.end_time - s.start_time) / 1_000_000  # ns → ms
    assert duration_ms > 0
    # Sanity: at least most of the 10 ms sleep is captured (allow scheduler slack).
    assert duration_ms >= 5


# ── Exact percentile math ─────────────────────────────────────────────────────


def test_percentile_exact_on_known_set():
    values = [10, 20, 30, 40, 50]
    assert percentile(values, 50) == 30
    assert percentile(values, 95) == pytest.approx(48.0)
    assert percentile(values, 100) == 50
    assert percentile(values, 0) == 10


def test_percentile_unsorted_input_is_sorted_first():
    assert percentile([50, 10, 30, 40, 20], 50) == 30


def test_percentile_two_values_interpolates():
    assert percentile([100, 300], 50) == pytest.approx(200.0)
    assert percentile([100, 300], 95) == pytest.approx(290.0)


def test_percentile_single_value():
    assert percentile([42], 95) == 42.0


def test_percentile_empty_raises():
    with pytest.raises(ValueError):
        percentile([], 95)


# ── latency_summary over supplied runs (pure, deterministic) ──────────────────


def _sample_runs():
    """Two runs; plan+generate node timings chosen for exact percentile math."""
    return [
        [
            {"node": "plan", "duration_ms": 100},
            {"node": "generate", "duration_ms": 200},
        ],
        [
            {"node": "plan", "duration_ms": 300},
            {"node": "generate", "duration_ms": 400},
        ],
    ]


def test_summary_empty_state_is_honest():
    s = latency_summary(runs=[])
    assert s.empty is True
    assert s.run_count == 0
    assert s.per_node == []
    assert s.run_p50_ms is None
    assert s.run_p95_ms is None
    assert s.run_max_ms is None
    assert s.slowest_node is None


def test_summary_per_node_percentiles_exact():
    s = latency_summary(runs=_sample_runs())
    by_node = {n.node: n for n in s.per_node}
    plan = by_node["plan"]
    assert plan.count == 2
    assert plan.p50_ms == pytest.approx(200.0)
    assert plan.p95_ms == pytest.approx(290.0)
    assert plan.max_ms == 300.0
    gen = by_node["generate"]
    assert gen.p50_ms == pytest.approx(300.0)
    assert gen.p95_ms == pytest.approx(390.0)
    assert gen.max_ms == 400.0


def test_summary_per_node_total_equals_sum_of_samples():
    s = latency_summary(runs=_sample_runs())
    by_node = {n.node: n for n in s.per_node}
    assert by_node["plan"].total_ms == 100 + 300
    assert by_node["generate"].total_ms == 200 + 400


def test_summary_run_percentiles_from_summed_node_durations():
    # Run totals: 300 and 700.
    s = latency_summary(runs=_sample_runs())
    assert s.run_count == 2
    assert s.run_p50_ms == pytest.approx(500.0)
    assert s.run_p95_ms == pytest.approx(680.0)
    assert s.run_max_ms == 700.0


def test_summary_slowest_node_is_highest_p95():
    s = latency_summary(runs=_sample_runs())
    assert s.slowest_node == "generate"


def test_summary_supplied_source_label_and_no_capacity():
    s = latency_summary(runs=_sample_runs())
    assert s.source == "supplied_runs"
    assert s.window_capacity is None
    assert s.empty is False


def test_node_appearing_twice_in_a_run_counts_as_two_samples():
    runs = [
        [
            {"node": "plan", "duration_ms": 100},
            {"node": "plan", "duration_ms": 200},  # self-repair re-plan
        ]
    ]
    s = latency_summary(runs=runs)
    plan = next(n for n in s.per_node if n.node == "plan")
    assert plan.count == 2
    assert plan.total_ms == 300


def test_as_dict_round_trips():
    s = latency_summary(runs=_sample_runs())
    d = s.as_dict()
    assert d["run_count"] == 2
    assert d["source"] == "supplied_runs"
    assert d["slowest_node"] == "generate"
    assert isinstance(d["per_node"], list)
    assert d["per_node"][0]["node"] in {"plan", "generate"}


# ── record_run_latency + rolling window ───────────────────────────────────────


def test_record_then_summary_reflects_samples():
    for run in _sample_runs():
        record_run_latency(run)
    s = latency_summary()  # reads the in-process window
    assert s.source == "in_process_rolling_window"
    assert s.run_count == 2
    assert s.window_capacity == DEFAULT_WINDOW_CAPACITY
    # p95 is a REAL percentile of the REAL recorded run durations, not a constant.
    assert s.run_p95_ms == pytest.approx(680.0)


def test_recorded_p95_equals_real_percentile_of_samples():
    runs = [
        [{"node": "generate", "duration_ms": v}] for v in (10, 20, 30, 40, 50)
    ]
    for run in runs:
        record_run_latency(run)
    s = latency_summary()
    gen = next(n for n in s.per_node if n.node == "generate")
    assert gen.p95_ms == pytest.approx(percentile([10, 20, 30, 40, 50], 95))
    assert gen.p95_ms == pytest.approx(48.0)


def test_record_returns_count_of_timed_nodes():
    assert record_run_latency(_sample_runs()[0]) == 2


def test_record_skips_nodes_with_no_duration():
    n = record_run_latency(
        [
            {"node": "plan", "duration_ms": 100},
            {"node": "approval", "duration_ms": None},  # paused gate, never finished
            {"node": "generate"},  # no duration key
        ]
    )
    assert n == 1
    s = latency_summary()
    assert {node.node for node in s.per_node} == {"plan"}


def test_record_run_with_no_timed_nodes_records_nothing():
    assert record_run_latency([{"node": "approval", "duration_ms": None}]) == 0
    assert latency_summary().empty is True


def test_record_non_numeric_and_nonfinite_durations_skipped():
    assert record_run_latency([{"node": "x", "duration_ms": "abc"}]) == 0
    assert record_run_latency([{"node": "x", "duration_ms": float("nan")}]) == 0
    assert record_run_latency([{"node": "x", "duration_ms": float("inf")}]) == 0
    assert latency_summary().empty is True


def test_record_accepts_tuple_pairs_and_attr_models():
    class _Node:
        def __init__(self, node, duration_ms):
            self.node = node
            self.duration_ms = duration_ms

    assert record_run_latency([("plan", 100), ("generate", 200)]) == 2
    assert record_run_latency([_Node("plan", 300)]) == 1
    s = latency_summary()
    assert s.run_count == 2
    by_node = {n.node: n for n in s.per_node}
    assert by_node["plan"].count == 2


def test_window_is_bounded(monkeypatch):
    monkeypatch.setattr(latency_mod, "_window", deque(maxlen=2))
    for v in (100, 200, 300):
        record_run_latency([{"node": "plan", "duration_ms": v}])
    s = latency_summary()
    # Only the last two runs survive the bound.
    assert s.run_count == 2
    assert s.window_capacity == 2
    plan = next(n for n in s.per_node if n.node == "plan")
    assert sorted([100, 200, 300])[-2:] == [200, 300]
    assert plan.max_ms == 300
    assert plan.count == 2


def test_window_is_per_process_and_resets():
    record_run_latency([{"node": "plan", "duration_ms": 100}])
    assert latency_summary().run_count == 1
    reset_latency_window()
    assert latency_summary().empty is True


def test_default_window_capacity_reported():
    record_run_latency([{"node": "plan", "duration_ms": 1}])
    assert latency_summary().window_capacity == DEFAULT_WINDOW_CAPACITY
