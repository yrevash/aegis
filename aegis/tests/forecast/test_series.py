"""Unit tests for the dependency-free series-preparation layer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aegis.forecast.series import (
    bucket_events,
    infer_freq,
    minimum_history,
    normalise_points,
    season_length_for,
    step_delta,
)
from aegis.forecast.types import SeriesPoint


def test_normalise_sums_duplicate_timestamps_rather_than_overwriting():
    ts = datetime(2026, 3, 1, 12)
    out = normalise_points([(ts, 1.5), (ts, 2.5), (ts - timedelta(days=1), 4.0)])
    assert [p.value for p in out] == [4.0, 4.0]
    assert out[0].ts < out[1].ts


def test_normalise_converts_aware_timestamps_to_naive_utc():
    aware = datetime(2026, 3, 1, 12, tzinfo=UTC)
    (point,) = normalise_points([(aware, 1.0)])
    assert point.ts.tzinfo is None
    assert point.ts == datetime(2026, 3, 1, 12)


def test_infer_freq_uses_the_modal_gap_so_one_outage_cannot_reclassify():
    base = datetime(2026, 1, 1)
    points = [SeriesPoint(ts=base + timedelta(hours=i), value=1.0) for i in range(10)]
    points.append(SeriesPoint(ts=base + timedelta(days=30), value=1.0))
    assert infer_freq(points) == "h"


def test_infer_freq_needs_two_points():
    with pytest.raises(ValueError, match="at least two"):
        infer_freq([SeriesPoint(ts=datetime(2026, 1, 1), value=1.0)])


def test_bucket_events_fills_empty_buckets_with_a_real_zero():
    base = datetime(2026, 1, 1, 3)
    out = bucket_events([(base, 2.0), (base + timedelta(days=3), 5.0)], "D")
    assert [p.value for p in out] == [2.0, 0.0, 0.0, 5.0]
    assert out[0].ts == datetime(2026, 1, 1)


def test_bucket_events_can_keep_gaps_unknown():
    base = datetime(2026, 1, 1)
    out = bucket_events([(base, 2.0), (base + timedelta(days=3), 5.0)], "D", fill_gaps=False)
    assert [p.value for p in out] == [2.0, 5.0]


def test_bucket_events_floors_weeks_to_monday():
    # 2026-03-04 is a Wednesday; its week starts Monday 2026-03-02.
    (point,) = bucket_events([(datetime(2026, 3, 4, 17), 1.0)], "W")
    assert point.ts == datetime(2026, 3, 2)


def test_bucket_events_advances_months_calendar_correctly():
    out = bucket_events([(datetime(2026, 1, 15), 1.0), (datetime(2026, 4, 2), 3.0)], "MS")
    assert [p.ts.month for p in out] == [1, 2, 3, 4]
    assert [p.value for p in out] == [1.0, 0.0, 0.0, 3.0]


def test_bucket_events_on_no_events_is_empty_not_zero_padded():
    assert bucket_events([], "D") == []


@pytest.mark.parametrize(("freq", "season"), [("h", 24), ("D", 7), ("W", 52), ("MS", 12)])
def test_season_length_per_frequency(freq, season):
    assert season_length_for(freq) == season
    assert step_delta(freq).total_seconds() > 0


def test_unsupported_frequency_is_rejected_by_name():
    with pytest.raises(ValueError, match="unsupported frequency"):
        season_length_for("Q")


def test_minimum_history_grows_with_horizon_and_season():
    # Every held-out window costs `horizon` points, and enough must remain before the
    # earliest cutoff to fit two seasonal cycles and calibrate the conformal band.
    assert minimum_history(14, 7, backtest_windows=3, conformal_windows=3) == 3 * 14 + 29
    assert minimum_history(1, 7, backtest_windows=3, conformal_windows=3) == 3 + 15
    assert minimum_history(14, 24, backtest_windows=3, conformal_windows=3) == 3 * 14 + 49
    assert minimum_history(28, 7) > minimum_history(14, 7)
