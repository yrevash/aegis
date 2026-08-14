"""The statsforecast-backed forecaster: fit, calibrate, backtest, select.

This is the only module in :mod:`aegis.forecast` that touches the forecasting
stack, and it reaches it exclusively through :func:`aegis.core.lazy.require`, so a
deployment without the ``forecast`` extra gets an :class:`ImportError` naming the
exact install command rather than a missing-feature mystery.

Three design decisions carry the module's trust story.

**Chronological calibration, never a random split.**
    :mod:`aegis.ml.model` calibrates its conformal predictor on a random
    ``train_test_split``. On a time series that is a leak: calibration rows drawn
    from *after* the rows the model trained on make the residual distribution
    optimistic and void the coverage guarantee. Everything here is split by time —
    statsforecast's ``ConformalIntervals`` calibrates on rolling windows strictly
    inside the training slice, and the rolling-origin backtest only ever scores
    points that lie *after* the cutoff whose model produced them.

**Conformal by default; parametric only when asked, and labelled.**
    statsforecast's plain ``level=[90]`` columns are the fitted model's own
    predictive distribution — a *model assumption*, not a calibration. Calling those
    "90% coverage" is precisely the overclaim this platform refuses to make, so the
    default is ``ConformalIntervals`` and :attr:`ForecastResult.interval_method`
    always says which kind of band the caller is holding.

**Requested coverage and achieved coverage are different numbers.**
    The backtest reports ``empirical_coverage`` measured by counting held-out actuals
    that landed inside the band. It is routinely *below* the requested level on real
    data; that gap is the finding, not a bug to be papered over.

Every failure is explicit: too little history raises
:class:`~aegis.forecast.types.InsufficientHistoryError` with the arithmetic, and a
series that defeats every candidate raises
:class:`~aegis.forecast.types.ForecastFitError`. There is no naive-line fallback.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from aegis.core.lazy import require
from aegis.forecast.series import (
    DEFAULT_BACKTEST_WINDOWS,
    DEFAULT_CONFORMAL_WINDOWS,
    infer_freq,
    minimum_history,
    normalise_points,
    season_length_for,
)
from aegis.forecast.types import (
    BacktestReport,
    CandidateScore,
    DegenerateSeriesError,
    ExcludedModel,
    ForecastFitError,
    ForecastResult,
    HorizonPoint,
    InsufficientHistoryError,
    IntervalMethod,
    SeriesPoint,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd

__all__ = ["DEFAULT_LEVEL", "FORECAST_EXTRA", "forecast_series"]

#: The pip extra that carries the forecasting stack.
FORECAST_EXTRA = "aegis[forecast]"

#: Coverage level requested by default. What is *achieved* is measured, not assumed.
DEFAULT_LEVEL = 0.9

#: Our frequency aliases mapped to the exact pandas offset the bucketer produces.
#: ``series.bucket_events`` floors weekly buckets to Monday, so the pandas alias must
#: be ``W-MON`` — plain ``W`` is week-ending-Sunday and would silently shift every
#: forecast timestamp by six days.
_PANDAS_FREQ: dict[str, str] = {"h": "h", "D": "D", "W": "W-MON", "MS": "MS"}

#: Values below this magnitude are treated as zero when deciding whether MAPE exists.
_EPS = 1e-9


def _level_pct(level: float) -> int:
    """Convert a coverage level to the integer percent statsforecast expects.

    Args:
        level: Requested coverage, e.g. ``0.9``.

    Returns:
        The level as an integer percentage, e.g. ``90``.

    Raises:
        ValueError: If ``level`` is outside ``(0, 1)`` or is not a whole percent.
    """
    if not 0.0 < level < 1.0:
        raise ValueError(f"level must be strictly between 0 and 1, got {level!r}")
    pct = level * 100.0
    if abs(pct - round(pct)) > 1e-6:
        raise ValueError(f"level must be a whole percent (e.g. 0.9, 0.95), got {level!r}")
    return int(round(pct))


def _smape(actual: Sequence[float], pred: Sequence[float]) -> float:
    """Return symmetric MAPE (%) over paired actual/predicted values.

    A pair where both sides are ~0 contributes 0 rather than a divide-by-zero: the
    forecast was exactly right about nothing happening.

    Args:
        actual: Held-out observed values.
        pred: The forecasts for the same points.

    Returns:
        Mean symmetric absolute percentage error, in percent.
    """
    total = 0.0
    for a, p in zip(actual, pred, strict=True):
        denom = abs(a) + abs(p)
        total += 0.0 if denom < _EPS else 200.0 * abs(p - a) / denom
    return total / len(actual)


def _mape(actual: Sequence[float], pred: Sequence[float]) -> float | None:
    """Return MAPE (%), or ``None`` when it is undefined.

    MAPE divides by the actual, so a single zero actual makes it undefined — not
    merely large. Returning ``None`` says that; returning a huge number would not.

    Args:
        actual: Held-out observed values.
        pred: The forecasts for the same points.

    Returns:
        Mean absolute percentage error in percent, or ``None`` if any actual is ~0.
    """
    if any(abs(a) < _EPS for a in actual):
        return None
    return sum(abs(p - a) / abs(a) for a, p in zip(actual, pred, strict=True)) / len(actual) * 100.0


def _mae(actual: Sequence[float], pred: Sequence[float]) -> float:
    """Return the mean absolute error over paired actual/predicted values.

    Args:
        actual: Held-out observed values.
        pred: The forecasts for the same points.

    Returns:
        The mean absolute error.
    """
    return sum(abs(p - a) for a, p in zip(actual, pred, strict=True)) / len(actual)


def _build_models(
    season_length: int,
    *,
    conformal: object | None,
) -> list[Any]:
    """Instantiate the candidate model roster.

    ``SeasonalNaive`` is deliberately in the roster and deliberately reported even
    when it loses: a selection is only auditable if the baseline it beat is visible.

    Args:
        season_length: Seasonal period for the series' frequency.
        conformal: A ``ConformalIntervals`` instance, or ``None`` for parametric bands.

    Returns:
        The candidate models, in a stable order.
    """
    models_mod = require(FORECAST_EXTRA, "statsforecast.models")
    kwargs: dict[str, Any] = {"prediction_intervals": conformal} if conformal is not None else {}
    return [
        # ``approximation=True`` scores candidate orders by an approximated
        # (conditional-sum-of-squares) likelihood during the stepwise search and only
        # fits the winner exactly. Measured on the module's own fixtures it cuts the
        # backtest from ~6.5s to ~2.6s at identical sMAPE — the search is a heuristic
        # either way, and this endpoint is fitted per request.
        models_mod.AutoARIMA(season_length=season_length, approximation=True, **kwargs),
        models_mod.AutoETS(season_length=season_length, **kwargs),
        models_mod.SeasonalNaive(season_length=season_length, **kwargs),
    ]


def _frame(points: Sequence[SeriesPoint], series_id: str) -> pd.DataFrame:
    """Render normalised points as the long frame statsforecast consumes.

    Args:
        points: Normalised history, oldest first.
        series_id: Value for the ``unique_id`` column.

    Returns:
        A ``unique_id``/``ds``/``y`` :class:`pandas.DataFrame`.
    """
    pd_mod = require(FORECAST_EXTRA, "pandas")
    return pd_mod.DataFrame(
        {
            "unique_id": [series_id] * len(points),
            "ds": [p.ts for p in points],
            "y": [float(p.value) for p in points],
        }
    )


def _cross_validate(
    models: list[Any],
    frame: pd.DataFrame,
    *,
    freq: str,
    horizon: int,
    windows: int,
    level_pct: int,
    conformal: object | None,
) -> tuple[pd.DataFrame | None, list[ExcludedModel]]:
    """Run the rolling-origin backtest, isolating models that blow up.

    A single unfittable candidate must not cost the caller its forecast, so a failed
    batch is retried model-by-model and each casualty is recorded with the real
    exception text. Nothing is silently dropped.

    Args:
        models: Candidate models to score.
        frame: The full history frame.
        freq: The pandas frequency alias.
        horizon: Steps forecast from each cutoff.
        windows: Number of rolling-origin cutoffs.
        level_pct: Coverage level as an integer percent.
        conformal: A ``ConformalIntervals`` instance, or ``None``.

    Returns:
        ``(cv_frame, excluded)`` — ``cv_frame`` is ``None`` when every candidate failed.
    """
    sf_mod = require(FORECAST_EXTRA, "statsforecast")
    kwargs: dict[str, Any] = {
        "h": horizon,
        "df": frame,
        "n_windows": windows,
        "step_size": horizon,
        "level": [level_pct],
        "refit": False,
    }
    if conformal is not None:
        kwargs["prediction_intervals"] = conformal

    try:
        engine = sf_mod.StatsForecast(models=list(models), freq=freq, n_jobs=1)
        return engine.cross_validation(**kwargs), []
    except Exception:  # noqa: BLE001 - retried per-model below to attribute the failure
        pass

    excluded: list[ExcludedModel] = []
    frames: list[pd.DataFrame] = []
    for model in models:
        name = repr(model)
        try:
            engine = sf_mod.StatsForecast(models=[model], freq=freq, n_jobs=1)
            frames.append(engine.cross_validation(**kwargs))
        except Exception as exc:  # noqa: BLE001 - the reason is the product here
            excluded.append(ExcludedModel(model=name, reason=f"{type(exc).__name__}: {exc}"))
    if not frames:
        return None, excluded

    merged = frames[0]
    for extra in frames[1:]:
        cols = [c for c in extra.columns if c not in {"unique_id", "ds", "cutoff", "y"}]
        merged = merged.join(extra[cols])
    return merged, excluded


def _score(
    cv: pd.DataFrame,
    name: str,
    level_pct: int,
) -> tuple[float, float | None, float, float, int] | None:
    """Score one candidate's backtest columns, or return ``None`` if unusable.

    Args:
        cv: The cross-validation frame.
        name: The model's column name.
        level_pct: Coverage level as an integer percent.

    Returns:
        ``(smape, mape, mae, empirical_coverage, n_points)``, or ``None`` when the
        model produced no finite predictions/bounds to score.
    """
    lo_col, hi_col = f"{name}-lo-{level_pct}", f"{name}-hi-{level_pct}"
    if not {name, lo_col, hi_col} <= set(cv.columns):
        return None
    usable = cv[["y", name, lo_col, hi_col]].dropna()
    if usable.empty:
        return None
    actual = [float(v) for v in usable["y"]]
    pred = [float(v) for v in usable[name]]
    inside = sum(
        1
        for a, lo, hi in zip(actual, usable[lo_col], usable[hi_col], strict=True)
        if float(lo) <= a <= float(hi)
    )
    return (
        _smape(actual, pred),
        _mape(actual, pred),
        _mae(actual, pred),
        inside / len(actual),
        len(actual),
    )


def forecast_series(
    points: Iterable[tuple[datetime, float] | SeriesPoint],
    *,
    series_id: str,
    label: str,
    horizon: int,
    data_source: str,
    freq: str | None = None,
    unit: str | None = None,
    level: float = DEFAULT_LEVEL,
    interval: IntervalMethod = "conformal",
    backtest_windows: int = DEFAULT_BACKTEST_WINDOWS,
    conformal_windows: int = DEFAULT_CONFORMAL_WINDOWS,
    include_history: bool = True,
) -> ForecastResult:
    """Forecast a series, measure the result, and refuse when it cannot be measured.

    The sequence is: normalise → refuse-if-short → rolling-origin backtest of every
    candidate → select on measured sMAPE → refit the winner on the full history →
    forecast. The reported coverage comes from step three, never from ``level``.

    Args:
        points: The observed history as ``(timestamp, value)`` pairs or
            :class:`~aegis.forecast.types.SeriesPoint`s. Order is irrelevant;
            duplicate timestamps are summed.
        series_id: Stable identifier for the series.
        label: Human label, e.g. ``"Daily spend (USD)"``.
        horizon: Steps to forecast beyond the last observation.
        data_source: Provenance tag, e.g. ``"usage_ledger"`` or ``"adapter"``.
        freq: Frequency alias; inferred from the observed spacing when ``None``.
        unit: Unit of the values, e.g. ``"USD"``.
        level: Coverage level to REQUEST, e.g. ``0.9``.
        interval: ``"conformal"`` (calibrated on out-of-sample errors) or
            ``"parametric"`` (the model's own predictive distribution). The choice is
            recorded on the result so a reader can discount it correctly.
        backtest_windows: Rolling-origin cutoffs to measure on.
        conformal_windows: Windows the conformal band calibrates on.
        include_history: Whether to echo the observed history on the result.

    Returns:
        A :class:`~aegis.forecast.types.ForecastResult` whose every claim was measured.

    Raises:
        ValueError: If ``horizon`` or ``level`` is invalid, or ``freq`` is unsupported.
        InsufficientHistoryError: If the series is too short to fit, calibrate and
            backtest at this horizon.
        DegenerateSeriesError: If the history is perfectly flat, where any interval
            would be vacuous.
        ForecastFitError: If no candidate model could be fitted and scored.
        ImportError: If the ``forecast`` extra is not installed.
    """
    if horizon < 1:
        raise ValueError(f"horizon must be >= 1, got {horizon!r}")
    if backtest_windows < 2:
        raise ValueError(
            f"backtest_windows must be >= 2 to measure coverage, got {backtest_windows!r}"
        )
    level_pct = _level_pct(level)

    history = normalise_points(points)
    if len(history) < 2:
        raise InsufficientHistoryError(
            have=len(history),
            need=2,
            reason="A series needs at least two observations before a frequency can be inferred",
        )
    resolved_freq = freq or infer_freq(history)
    season = season_length_for(resolved_freq)
    if resolved_freq not in _PANDAS_FREQ:
        raise ValueError(f"unsupported frequency {resolved_freq!r}")

    need = minimum_history(
        horizon,
        season,
        backtest_windows=backtest_windows,
        conformal_windows=conformal_windows,
    )
    if len(history) < need:
        raise InsufficientHistoryError(
            have=len(history),
            need=need,
            reason=(
                f"Forecasting {horizon} step(s) of {resolved_freq!r} data needs "
                f"{backtest_windows} held-out backtest window(s) plus enough history "
                f"before the earliest cutoff to fit two seasonal cycles "
                f"(season={season}) and calibrate {conformal_windows} conformal window(s)"
            ),
        )

    values = [p.value for p in history]
    spread = max(values) - min(values)
    scale = max(abs(v) for v in values)
    if spread <= 1e-12 * max(scale, 1.0):
        raise DegenerateSeriesError(
            f"The series is constant at {values[0]:.6g} across all {len(values)} "
            "observations. A flat series fits perfectly, forecasts a flat line and "
            "reports 100% coverage from a zero-width interval — all true, all "
            "meaningless. Refusing rather than presenting the absence of data as a "
            "confident prediction."
        )

    conformal_obj: object | None = None
    detail = f"parametric predictive distribution at level={level_pct}"
    if interval == "conformal":
        utils = require(FORECAST_EXTRA, "statsforecast.utils")
        conformal_obj = utils.ConformalIntervals(n_windows=conformal_windows, h=horizon)
        detail = (
            f"ConformalIntervals(n_windows={conformal_windows}, h={horizon}) — "
            "calibrated on out-of-sample residuals from chronologically earlier windows"
        )

    pandas_freq = _PANDAS_FREQ[resolved_freq]
    frame = _frame(history, series_id)
    models = _build_models(season, conformal=conformal_obj)
    cv, excluded = _cross_validate(
        models,
        frame,
        freq=pandas_freq,
        horizon=horizon,
        windows=backtest_windows,
        level_pct=level_pct,
        conformal=conformal_obj,
    )
    if cv is None:
        raise ForecastFitError(
            "No candidate model could be backtested on this series: "
            + "; ".join(f"{e.model}: {e.reason}" for e in excluded)
        )

    names = [m.__class__.__name__ if not hasattr(m, "alias") else str(m.alias) for m in models]
    scores: list[CandidateScore] = []
    n_points_by_name: dict[str, int] = {}
    for name in names:
        scored = _score(cv, name, level_pct)
        if scored is None:
            if not any(e.model.startswith(name) for e in excluded):
                excluded.append(
                    ExcludedModel(
                        model=name,
                        reason="produced no finite predictions or interval bounds on the backtest",
                    )
                )
            continue
        smape, mape, mae, coverage, n_points = scored
        scores.append(
            CandidateScore(
                model=name, smape=smape, mape=mape, mae=mae, empirical_coverage=coverage
            )
        )
        n_points_by_name[name] = n_points

    if not scores:
        raise ForecastFitError(
            "Every candidate model failed to produce a scoreable backtest: "
            + "; ".join(f"{e.model}: {e.reason}" for e in excluded)
        )

    winner = min(scores, key=lambda s: s.smape)
    winner.selected = True

    chosen = models[names.index(winner.model)]
    sf_mod = require(FORECAST_EXTRA, "statsforecast")
    fit_kwargs: dict[str, Any] = {
        "h": horizon,
        "df": frame,
        "level": [level_pct],
    }
    if conformal_obj is not None:
        fit_kwargs["prediction_intervals"] = conformal_obj
    try:
        engine = sf_mod.StatsForecast(models=[chosen], freq=pandas_freq, n_jobs=1)
        forecast = engine.forecast(**fit_kwargs)
    except Exception as exc:  # noqa: BLE001 - refuse rather than degrade to a naive line
        raise ForecastFitError(
            f"{winner.model} backtested but failed to fit the full history: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    lo_col, hi_col = f"{winner.model}-lo-{level_pct}", f"{winner.model}-hi-{level_pct}"
    horizon_points: list[HorizonPoint] = []
    for step, (_, row) in enumerate(forecast.iterrows(), start=1):
        point, lo, hi = float(row[winner.model]), float(row[lo_col]), float(row[hi_col])
        if not all(math.isfinite(v) for v in (point, lo, hi)):
            raise ForecastFitError(
                f"{winner.model} produced a non-finite value at step {step}; refusing "
                "to serve a partially undefined forecast."
            )
        horizon_points.append(
            HorizonPoint(ts=row["ds"].to_pydatetime(), point=point, lo=lo, hi=hi, step=step)
        )

    backtest = BacktestReport(
        windows=backtest_windows,
        horizon=horizon,
        n_points=n_points_by_name[winner.model],
        smape=winner.smape,
        mape=winner.mape,
        mae=winner.mae,
        requested_coverage=level,
        empirical_coverage=winner.empirical_coverage,
        coverage_meets_request=winner.empirical_coverage >= level,
        interval_method=interval,
    )

    return ForecastResult(
        series_id=series_id,
        label=label,
        unit=unit,
        data_source=data_source,
        freq=resolved_freq,
        season_length=season,
        history_points=len(history),
        history=list(history) if include_history else [],
        horizon=horizon,
        points=horizon_points,
        model=winner.model,
        selection_metric="smape",
        candidates=scores,
        excluded_models=excluded,
        interval_method=interval,
        interval_method_detail=detail,
        requested_level=level,
        backtest=backtest,
        model_selected_on_backtest_windows=True,
        generated_at=datetime.now(UTC).replace(tzinfo=None),
    )
