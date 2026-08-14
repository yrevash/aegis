"""Tests for the forecaster itself — and above all for what it refuses to claim."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from aegis.forecast import (
    DegenerateSeriesError,
    InsufficientHistoryError,
    forecast_series,
    minimum_history,
)

from .conftest import make_daily

pytest.importorskip("statsforecast", reason="the 'forecast' extra is not installed")


@pytest.fixture(scope="module")
def result():
    """One fitted forecast, reused across assertions (a fit costs seconds, not ms)."""
    return forecast_series(
        make_daily(140),
        series_id="spend",
        label="Daily spend (USD)",
        unit="USD",
        data_source="test",
        horizon=14,
        level=0.9,
    )


def test_forecast_returns_one_row_per_horizon_step_in_order(result):
    assert result.horizon == 14
    assert [p.step for p in result.points] == list(range(1, 15))
    spacing = {
        result.points[i + 1].ts - result.points[i].ts for i in range(len(result.points) - 1)
    }
    assert spacing == {timedelta(days=1)}


def test_the_forecast_starts_after_the_last_observation(result):
    assert result.points[0].ts > result.history[-1].ts


def test_every_interval_brackets_its_own_point(result):
    assert all(p.lo <= p.point <= p.hi for p in result.points)


def test_the_interval_is_labelled_conformal_with_its_provenance(result):
    assert result.interval_method == "conformal"
    assert "ConformalIntervals" in result.interval_method_detail
    assert result.backtest.interval_method == "conformal"


def test_reported_coverage_is_measured_not_the_requested_level(result):
    # The whole point of the module. `requested_coverage` is an input, echoed back;
    # `empirical_coverage` is a count of held-out actuals that landed inside the band.
    bt = result.backtest
    assert bt.requested_coverage == 0.9
    assert 0.0 <= bt.empirical_coverage <= 1.0
    assert bt.n_points == bt.windows * bt.horizon
    # Recomputable from the definition: a whole number of hits over n_points.
    hits = bt.empirical_coverage * bt.n_points
    assert hits == pytest.approx(round(hits), abs=1e-9)
    assert bt.coverage_meets_request is (bt.empirical_coverage >= bt.requested_coverage)


def test_coverage_is_never_rounded_up_to_the_request(result):
    bt = result.backtest
    if bt.empirical_coverage < bt.requested_coverage:
        assert bt.coverage_meets_request is False


def test_accuracy_is_reported_and_finite(result):
    bt = result.backtest
    assert bt.smape >= 0.0
    assert bt.mae >= 0.0
    assert bt.mape is None or bt.mape >= 0.0


def test_the_seasonal_naive_baseline_is_scored_and_published(result):
    names = {c.model for c in result.candidates}
    assert "SeasonalNaive" in names, "the baseline must be visible, won or lost"
    assert sum(c.selected for c in result.candidates) == 1
    winner = next(c for c in result.candidates if c.selected)
    assert winner.model == result.model
    assert winner.smape == min(c.smape for c in result.candidates)


def test_the_optimism_of_selecting_on_the_scoring_windows_is_declared(result):
    assert result.model_selected_on_backtest_windows is True


def test_provenance_and_shape_metadata_are_echoed(result):
    assert result.data_source == "test"
    assert result.freq == "D"
    assert result.season_length == 7
    assert result.history_points == 140
    assert result.unit == "USD"


def test_a_too_short_series_is_refused_with_the_arithmetic(short_series):
    with pytest.raises(InsufficientHistoryError) as exc:
        forecast_series(
            short_series,
            series_id="s",
            label="s",
            data_source="test",
            horizon=14,
        )
    assert exc.value.have == 20
    assert exc.value.need == minimum_history(14, 7)
    assert exc.value.have < exc.value.need
    assert "backtest window" in exc.value.reason


def test_a_series_that_is_long_but_not_long_enough_for_the_horizon_is_refused():
    # 80 points is plenty at h=7 and not enough at h=21 — the refusal is about the
    # requested horizon, not about the series being "small".
    points = make_daily(80)
    forecast_series(points, series_id="s", label="s", data_source="t", horizon=7)
    with pytest.raises(InsufficientHistoryError) as exc:
        forecast_series(points, series_id="s", label="s", data_source="t", horizon=21)
    assert exc.value.need == minimum_history(21, 7)


def test_two_observations_cannot_even_infer_a_frequency():
    with pytest.raises(InsufficientHistoryError) as exc:
        forecast_series(
            [(datetime(2026, 1, 1), 1.0)],
            series_id="s",
            label="s",
            data_source="t",
            horizon=3,
        )
    assert exc.value.need == 2


def test_a_parametric_band_is_labelled_parametric_not_conformal():
    result = forecast_series(
        make_daily(140, seed=3),
        series_id="s",
        label="s",
        data_source="t",
        horizon=14,
        interval="parametric",
    )
    assert result.interval_method == "parametric"
    assert "ConformalIntervals" not in result.interval_method_detail
    assert result.backtest.interval_method == "parametric"


def test_conformal_and_parametric_bands_are_not_the_same_numbers():
    kwargs = {"series_id": "s", "label": "s", "data_source": "t", "horizon": 14}
    points = make_daily(140, seed=5)
    conformal = forecast_series(points, interval="conformal", **kwargs)
    parametric = forecast_series(points, interval="parametric", **kwargs)
    widths_c = [p.hi - p.lo for p in conformal.points]
    widths_p = [p.hi - p.lo for p in parametric.points]
    assert widths_c != widths_p, "a relabelled identical band would be the overclaim"


def test_an_invalid_level_is_rejected_before_any_fitting():
    for bad in (0.0, 1.0, 1.5, 0.905):
        with pytest.raises(ValueError, match="level"):
            forecast_series(
                make_daily(140),
                series_id="s",
                label="s",
                data_source="t",
                horizon=14,
                level=bad,
            )


def test_a_horizon_below_one_is_rejected():
    with pytest.raises(ValueError, match="horizon"):
        forecast_series(
            make_daily(140), series_id="s", label="s", data_source="t", horizon=0
        )


def test_one_backtest_window_is_rejected_because_coverage_would_be_meaningless():
    with pytest.raises(ValueError, match="backtest_windows"):
        forecast_series(
            make_daily(140),
            series_id="s",
            label="s",
            data_source="t",
            horizon=14,
            backtest_windows=1,
        )


def test_a_flat_zero_series_is_refused_rather_than_forecast_as_a_line():
    # An all-zero ledger fits perfectly, forecasts a flat zero line and measures 100%
    # coverage from a zero-width band. Every number is true; together they dress the
    # absence of data as a confident prediction, so the module refuses instead.
    points = [(datetime(2026, 1, 1) + timedelta(days=i), 0.0) for i in range(140)]
    with pytest.raises(DegenerateSeriesError, match="constant"):
        forecast_series(points, series_id="s", label="s", data_source="t", horizon=14)


def test_a_flat_nonzero_series_is_refused_for_the_same_reason():
    points = [(datetime(2026, 1, 1) + timedelta(days=i), 42.0) for i in range(140)]
    with pytest.raises(DegenerateSeriesError):
        forecast_series(points, series_id="s", label="s", data_source="t", horizon=14)


def test_history_can_be_omitted_from_the_payload(result):
    lean = forecast_series(
        make_daily(140),
        series_id="s",
        label="s",
        data_source="t",
        horizon=14,
        include_history=False,
    )
    assert lean.history == []
    assert lean.history_points == 140
    assert result.history != []
