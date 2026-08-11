"""Unit tests for the ``MetricsStore`` quality proxy (M3).

``quality_score`` is a deterministic grounding proxy: the fraction of finished runs
that both completed cleanly and retrieved backing context (touched graph nodes).
"""

from __future__ import annotations

import aegis.gateway.llm as llm_mod
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
