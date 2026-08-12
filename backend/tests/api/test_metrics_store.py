"""Unit tests for the ``MetricsStore`` quality proxy (M3).

``quality_score`` is a deterministic grounding proxy: the fraction of finished runs
that both completed cleanly and retrieved backing context (touched graph nodes).
"""

from __future__ import annotations

import aegis.gateway.llm as llm_mod
import aegis.observability as obs
import pytest

from app.api.routes import MetricsStore
from app.api.schemas import RunStatus


def test_quality_score_none_with_no_runs() -> None:
    assert MetricsStore().snapshot().quality_score is None


def test_completed_and_grounded_scores_one() -> None:
    store = MetricsStore()
    store.note_grounding("run-1")
    store.record_run(
        run_id="run-1", cache_hit=False, cost_usd=0.0, status=RunStatus.COMPLETED
    )
    assert store.snapshot().quality_score == 1.0


def test_completed_but_ungrounded_scores_zero() -> None:
    store = MetricsStore()
    store.record_run(
        run_id="run-1", cache_hit=False, cost_usd=0.0, status=RunStatus.COMPLETED
    )
    assert store.snapshot().quality_score == 0.0


def test_grounded_but_blocked_scores_zero() -> None:
    store = MetricsStore()
    store.note_grounding("run-1")
    store.record_run(
        run_id="run-1", cache_hit=False, cost_usd=0.0, status=RunStatus.BLOCKED
    )
    assert store.snapshot().quality_score == 0.0


def test_snapshot_exposes_cost_saved_and_baseline(monkeypatch) -> None:
    monkeypatch.setattr(llm_mod, "_tally", llm_mod._UsageTally())
    # Before any model call the savings figures default to zero.
    snap = MetricsStore().snapshot()
    assert snap.cost_saved_usd == 0.0
    assert snap.baseline_cost_usd == 0.0

    # A small-model call yields a measured, positive saving vs the baseline.
    llm_mod.record_call(
        "genailab-maas-gpt-4o-mini", 0.0002, prompt_tokens=1000, completion_tokens=1000
    )
    snap = MetricsStore().snapshot()
    assert snap.baseline_cost_usd > 0.0
    assert snap.cost_saved_usd == pytest.approx(
        snap.baseline_cost_usd - llm_mod.usage_tally()["total_cost_usd"]
    )


def test_snapshot_total_calls_and_p95_honest_empty(monkeypatch) -> None:
    """A fresh process reports 0 calls and a null p95 — never fabricated figures."""
    monkeypatch.setattr(llm_mod, "_tally", llm_mod._UsageTally())
    obs.reset_latency_window()
    snap = MetricsStore().snapshot()
    assert snap.total_calls == 0
    assert snap.p95_latency_ms is None
    # actions_approved is folded in by the /metrics handler (async store read); the
    # sync snapshot leaves it at the honest default.
    assert snap.actions_approved == 0


def test_snapshot_total_calls_tracks_gateway_tally(monkeypatch) -> None:
    """total_calls mirrors the measured gateway chat-completion count."""
    monkeypatch.setattr(llm_mod, "_tally", llm_mod._UsageTally())
    llm_mod.record_call(
        "genailab-maas-gpt-4o-mini", 0.0002, prompt_tokens=10, completion_tokens=10
    )
    llm_mod.record_call(
        "genailab-maas-llama-3.3-70b", 0.001, prompt_tokens=10, completion_tokens=10
    )
    assert MetricsStore().snapshot().total_calls == 2


def test_snapshot_p95_from_recorded_runs() -> None:
    """p95_latency_ms comes from real samples in the per-process latency window."""
    obs.reset_latency_window()
    try:
        obs.record_run_latency(
            [
                {"node": "plan", "duration_ms": 100.0},
                {"node": "act", "duration_ms": 200.0},
            ]
        )
        obs.record_run_latency([{"node": "plan", "duration_ms": 50.0}])
        snap = MetricsStore().snapshot()
        assert snap.p95_latency_ms is not None
        assert snap.p95_latency_ms > 0.0
    finally:
        obs.reset_latency_window()


def test_quality_score_is_a_running_average() -> None:
    store = MetricsStore()
    # Two grounded+completed (1.0) and two that fail the proxy (0.0) → 0.5.
    store.note_grounding("a")
    store.record_run(run_id="a", cache_hit=False, cost_usd=0.0, status=RunStatus.COMPLETED)
    store.note_grounding("b")
    store.record_run(run_id="b", cache_hit=False, cost_usd=0.0, status=RunStatus.COMPLETED)
    store.record_run(run_id="c", cache_hit=False, cost_usd=0.0, status=RunStatus.COMPLETED)
    store.note_grounding("d")
    store.record_run(run_id="d", cache_hit=False, cost_usd=0.0, status=RunStatus.ERROR)
    assert store.snapshot().quality_score == 0.5
