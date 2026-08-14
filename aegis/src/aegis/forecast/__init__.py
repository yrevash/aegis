"""Aegis Forecast — honest time-series forecasting with measured intervals.

The module's contract in one line: **nothing it reports was assumed**. A forecast
comes with the accuracy and the interval coverage that were *measured* on
chronologically held-out data, plus the name of the model that won and the names of
the ones that lost.

Why this exists next to :mod:`aegis.ml`
---------------------------------------
:class:`aegis.ml.types.MLExplainResponse` is scalar — one prediction, one interval.
A forecast is horizon-indexed, and its uncertainty widens with distance, so it needs
its own contract: :class:`~aegis.forecast.types.ForecastResult` carries a *sequence*
of ``(ts, point, lo, hi)`` rows. Just as importantly, :mod:`aegis.ml` calibrates on a
**random** train/test split. That is correct for i.i.d. tabular rows and wrong for a
time series, where it leaks the future into calibration. Everything here splits by
time instead.

Typical use::

    from aegis.forecast import forecast_series, project_burndown

    result = forecast_series(
        daily_spend_points,          # [(datetime, float), ...]
        series_id="tenant:7:spend",
        label="Daily spend (USD)",
        unit="USD",
        data_source="usage_ledger",
        horizon=14,
        level=0.9,
    )
    result.interval_method            # 'conformal' — a calibrated band, not a model SE
    result.backtest.requested_coverage   # 0.9   — what was asked for
    result.backtest.empirical_coverage   # 0.79  — what was actually achieved
    burn = project_burndown(result, scope="tenant", scope_id=7,
                            window="month", limit_usd=500.0, spent_usd=310.0)

Failure is always explicit. A series too short to fit, calibrate *and* backtest
raises :class:`~aegis.forecast.types.InsufficientHistoryError` with the exact
arithmetic; a series that defeats every candidate raises
:class:`~aegis.forecast.types.ForecastFitError`; a deployment without the extra
raises :class:`ImportError` naming ``pip install aegis[forecast]``. There is no
naive-line fallback anywhere — a plausible-looking line drawn through noise is the
one output this module refuses to produce.

Nothing heavy is imported at module load: :mod:`aegis.forecast.engine` (statsforecast,
pandas, numpy) is pulled in inside :func:`forecast_series`, so importing
``aegis.forecast.types`` or ``aegis.forecast.series`` stays dependency-free. See
``tests/forecast/test_types_is_dep_free.py``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from aegis.forecast.budget import project_burndown
from aegis.forecast.series import (
    DEFAULT_BACKTEST_WINDOWS,
    DEFAULT_CONFORMAL_WINDOWS,
    FREQ_SEASON,
    bucket_events,
    infer_freq,
    minimum_history,
    normalise_points,
    season_length_for,
    step_delta,
)
from aegis.forecast.types import (
    BacktestReport,
    BudgetBurndown,
    BurndownPoint,
    CandidateScore,
    DegenerateSeriesError,
    ExcludedModel,
    ForecastError,
    ForecastFitError,
    ForecastResult,
    HorizonPoint,
    InsufficientHistoryError,
    IntervalMethod,
    SeriesPoint,
)

__all__ = [
    "DEFAULT_BACKTEST_WINDOWS",
    "DEFAULT_CONFORMAL_WINDOWS",
    "FREQ_SEASON",
    "BacktestReport",
    "BudgetBurndown",
    "BurndownPoint",
    "CandidateScore",
    "DegenerateSeriesError",
    "ExcludedModel",
    "ForecastError",
    "ForecastFitError",
    "ForecastResult",
    "HorizonPoint",
    "InsufficientHistoryError",
    "IntervalMethod",
    "SeriesPoint",
    "bucket_events",
    "forecast_series",
    "infer_freq",
    "minimum_history",
    "normalise_points",
    "project_burndown",
    "season_length_for",
    "step_delta",
]


def forecast_series(
    points: Iterable[tuple[datetime, float] | SeriesPoint],
    **kwargs: object,
) -> ForecastResult:
    """Forecast a series with measured accuracy and measured interval coverage.

    Thin lazy wrapper over :func:`aegis.forecast.engine.forecast_series` — the heavy
    import happens here, on first call, not at package import. See that function for
    the full argument contract.

    Args:
        points: The observed history as ``(timestamp, value)`` pairs or
            :class:`~aegis.forecast.types.SeriesPoint`s.
        **kwargs: Forwarded verbatim to :func:`aegis.forecast.engine.forecast_series`
            (``series_id``, ``label``, ``horizon``, ``data_source``, ``freq``,
            ``unit``, ``level``, ``interval``, ``backtest_windows``,
            ``conformal_windows``, ``include_history``).

    Returns:
        A :class:`~aegis.forecast.types.ForecastResult`.

    Raises:
        ImportError: If the ``forecast`` extra is not installed.
    """
    from aegis.forecast.engine import forecast_series as _forecast_series

    return _forecast_series(points, **kwargs)  # type: ignore[arg-type]
