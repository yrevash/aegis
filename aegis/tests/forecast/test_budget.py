"""Tests for the budget burn-down projection (pure arithmetic, no model fitting)."""

from __future__ import annotations

from datetime import datetime, timedelta

from aegis.forecast import project_burndown
from aegis.forecast.types import BacktestReport, ForecastResult, HorizonPoint

_ORIGIN = datetime(2026, 6, 1)


def _forecast(increments: list[float], *, width: float = 1.0) -> ForecastResult:
    """Build a ForecastResult with hand-written steps, bypassing any fitting."""
    return ForecastResult(
        series_id="tenant:1:spend",
        label="Daily spend (USD)",
        unit="USD",
        data_source="test",
        freq="D",
        season_length=7,
        history_points=140,
        horizon=len(increments),
        points=[
            HorizonPoint(
                ts=_ORIGIN + timedelta(days=i),
                point=v,
                lo=v - width,
                hi=v + width,
                step=i + 1,
            )
            for i, v in enumerate(increments)
        ],
        model="AutoETS",
        interval_method="conformal",
        interval_method_detail="ConformalIntervals(n_windows=3, h=5)",
        requested_level=0.9,
        backtest=BacktestReport(
            windows=3,
            horizon=len(increments),
            n_points=3 * len(increments),
            smape=4.0,
            mae=1.0,
            requested_coverage=0.9,
            empirical_coverage=0.8,
            coverage_meets_request=False,
            interval_method="conformal",
        ),
        generated_at=_ORIGIN,
    )


def test_cumulative_starts_from_spend_already_incurred():
    burn = project_burndown(
        _forecast([10.0, 10.0, 10.0]),
        scope="tenant",
        scope_id=1,
        window="month",
        limit_usd=None,
        spent_usd=100.0,
    )
    assert [p.cumulative for p in burn.points] == [110.0, 120.0, 130.0]
    assert burn.projected_total_usd == 130.0
    assert burn.spent_usd == 100.0


def test_the_exhaustion_point_is_the_first_step_that_crosses_the_cap():
    burn = project_burndown(
        _forecast([10.0, 10.0, 10.0, 10.0]),
        scope="tenant",
        scope_id=1,
        window="month",
        limit_usd=115.0,
        spent_usd=100.0,
    )
    assert burn.exhausted_within_horizon is True
    assert burn.exhaustion_step == 2
    assert burn.exhaustion_ts == _ORIGIN + timedelta(days=1)
    assert [p.over_budget for p in burn.points] == [False, True, True, True]
    assert burn.headroom_usd == 115.0 - 140.0


def test_a_cap_that_is_not_reached_reports_positive_headroom_and_no_date():
    burn = project_burndown(
        _forecast([1.0, 1.0, 1.0]),
        scope="tenant",
        scope_id=1,
        window="month",
        limit_usd=500.0,
        spent_usd=10.0,
    )
    assert burn.exhausted_within_horizon is False
    assert burn.exhaustion_ts is None
    assert burn.exhaustion_step is None
    assert burn.headroom_usd == 487.0


def test_no_cap_still_projects_but_never_crosses():
    burn = project_burndown(
        _forecast([5.0, 5.0]),
        scope="tenant",
        scope_id=None,
        window="day",
        limit_usd=None,
        spent_usd=0.0,
    )
    assert burn.limit_usd is None
    assert burn.headroom_usd is None
    assert burn.exhausted_within_horizon is False
    assert all(p.over_budget is False for p in burn.points)


def test_the_cumulative_envelope_is_flagged_as_not_calibrated():
    # Summing marginal conformal bounds gives an envelope, not a calibrated interval
    # on the total. The projection must say so rather than let a reader assume 90%.
    burn = project_burndown(
        _forecast([10.0, 10.0], width=2.0),
        scope="tenant",
        scope_id=1,
        window="month",
        limit_usd=None,
        spent_usd=0.0,
    )
    assert burn.cumulative_bounds_are_calibrated is False
    assert burn.projected_total_lo == 16.0
    assert burn.projected_total_hi == 24.0


def test_the_interval_method_is_carried_through_from_the_forecast():
    burn = project_burndown(
        _forecast([1.0]),
        scope="user",
        scope_id=42,
        window="day",
        limit_usd=1.0,
        spent_usd=0.0,
    )
    assert burn.interval_method == "conformal"
    assert burn.scope == "user"
    assert burn.scope_id == 42
