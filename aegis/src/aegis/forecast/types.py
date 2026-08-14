"""Pydantic-only result types for the time-series forecasting module.

Deliberately imports nothing but ``pydantic`` and the stdlib — no statsforecast,
statsmodels, pandas or numpy — so any module (including the light backend API
schema layer and the frontend type generator) can depend on these shapes without
pulling the forecasting stack transitively. See
``tests/forecast/test_types_is_dep_free.py``.

Why these types exist at all: :class:`aegis.ml.types.MLExplainResponse` is
**scalar** — one prediction, one interval. A forecast is horizon-indexed: a
*sequence* of ``(timestamp, point, lo, hi)`` rows whose uncertainty widens with
distance. There is no honest way to fold that into the ML response, so the
forecast module carries its own contract.

The central honesty rule of this module is encoded in the field names:
:attr:`BacktestReport.requested_coverage` is what the caller *asked for* and
:attr:`BacktestReport.empirical_coverage` is what was *measured* on held-out
data. They are never the same field and the second is never inferred from the
first.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

__all__ = [
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
]

IntervalMethod = Literal["conformal", "parametric"]
"""Which kind of band the ``lo``/``hi`` bounds are.

``"conformal"``
    Distribution-free bounds calibrated from **out-of-sample** forecast errors on
    chronologically earlier windows (statsforecast ``ConformalIntervals``). This is
    the only kind whose coverage claim is a calibration rather than a model
    assumption — and even then the *achieved* rate must be measured, never assumed.

``"parametric"``
    The fitted model's own predictive distribution (ARIMA/ETS closed-form standard
    errors). Cheap and often narrow, but its coverage holds only insofar as the
    model's residual assumptions hold. Never present one of these as a calibrated
    interval: they are labelled distinctly for exactly that reason.
"""


class ForecastError(RuntimeError):
    """Base class for every explicit forecasting refusal.

    Every failure mode in this module raises one of these rather than returning a
    plausible-looking line: a caller must be able to tell "we could not forecast
    this" apart from "here is a forecast".
    """


class InsufficientHistoryError(ForecastError):
    """The series is too short to forecast (or to calibrate) honestly.

    Carries the machine-readable arithmetic so a UI can render *why* instead of a
    generic error — and so a tenant with two weeks of ledger is told it has two
    weeks of ledger, not shown a straight line drawn through noise.
    """

    def __init__(self, *, have: int, need: int, reason: str) -> None:
        """Record the shortfall.

        Args:
            have: Observations actually supplied.
            need: Observations the requested horizon/calibration needs.
            reason: Human-readable explanation of where ``need`` comes from.
        """
        self.have = have
        self.need = need
        self.reason = reason
        super().__init__(f"{reason} (have {have} observations, need {need})")


class DegenerateSeriesError(ForecastError):
    """The history carries no variation, so there is nothing to forecast.

    A perfectly flat series — most often a tenant whose ledger is all zeros because
    nobody called a model — will happily fit, produce a tidy flat line and report
    **100% empirical coverage** from a zero-width interval. Every one of those numbers
    is arithmetically true and all of them are misleading: they describe the absence
    of data, not a calibrated prediction. Saying "no usage recorded" is the honest
    answer, so this is raised instead.
    """


class ForecastFitError(ForecastError):
    """Every candidate model failed to fit or to calibrate on this series.

    Raised instead of degrading to a naive line: a series that defeats AutoARIMA,
    AutoETS *and* the seasonal-naive baseline has no forecast worth presenting.
    """


class SeriesPoint(BaseModel):
    """One observed point of the input history."""

    ts: datetime
    value: float


class HorizonPoint(BaseModel):
    """One forecast step: the point prediction and its interval bounds.

    ``lo``/``hi`` are only as meaningful as the enclosing
    :attr:`ForecastResult.interval_method` says they are — read that field before
    quoting these numbers.
    """

    ts: datetime
    point: float
    lo: float
    hi: float
    #: 1-based step index into the horizon (uncertainty normally grows with it).
    step: int


class BacktestReport(BaseModel):
    """Accuracy and interval coverage MEASURED on held-out data.

    Produced by rolling-origin cross-validation: for each cutoff the model is fitted
    on data strictly *before* the cutoff and scored on the ``horizon`` observations
    after it, so nothing the score is computed on was ever seen in training or
    calibration. This is the time-series-correct analogue of a held-out test split —
    a random split would leak the future into calibration and void the guarantee.
    """

    windows: int = Field(description="Rolling-origin cutoffs evaluated.")
    horizon: int = Field(description="Steps forecast ahead from each cutoff.")
    n_points: int = Field(description="Held-out (cutoff, step) pairs actually scored.")
    smape: float = Field(description="MEASURED symmetric MAPE (%) on held-out points.")
    mape: float | None = Field(
        default=None,
        description=(
            "MEASURED MAPE (%) on held-out points; None when any actual is ~0, where "
            "MAPE is undefined rather than merely large."
        ),
    )
    mae: float = Field(description="MEASURED mean absolute error on held-out points.")
    requested_coverage: float = Field(
        description="The coverage level ASKED FOR, e.g. 0.9. Not a measurement."
    )
    empirical_coverage: float = Field(
        description=(
            "The coverage rate ACHIEVED: the fraction of held-out actuals that fell "
            "inside the interval. This is the only coverage number that is evidence."
        )
    )
    coverage_meets_request: bool = Field(
        description="Whether empirical_coverage reached requested_coverage (no rounding up)."
    )
    interval_method: IntervalMethod = Field(
        description="Which kind of band the measured coverage refers to."
    )


class CandidateScore(BaseModel):
    """One candidate model's backtest score, kept even when it was not selected.

    Publishing the losers is what makes the selection auditable: a reader can see
    that the seasonal-naive baseline was actually beaten rather than assumed away.
    """

    model: str
    smape: float
    mape: float | None = None
    mae: float
    empirical_coverage: float
    selected: bool = False


class ExcludedModel(BaseModel):
    """A candidate that could not be scored, with the real reason it was dropped."""

    model: str
    reason: str


class ForecastResult(BaseModel):
    """A horizon-indexed forecast plus everything needed to discount it.

    Nothing here is a claim the module did not measure. ``interval_method`` says
    what kind of band was drawn, ``backtest`` says how well that band and that
    model actually performed on data held out chronologically, and
    ``excluded_models`` says what could not be fitted at all.
    """

    series_id: str = Field(description="Caller's identifier for the series.")
    label: str = Field(description="Human label, e.g. 'Daily spend (USD)'.")
    unit: str | None = Field(default=None, description="Unit of `value`, e.g. 'USD'.")
    data_source: str = Field(
        description=(
            "Where the history came from, e.g. 'usage_ledger' | 'adapter' — the "
            "provenance signal, so a demo series is never mistaken for live data."
        )
    )
    freq: str = Field(description="Inferred/declared pandas frequency alias, e.g. 'D'.")
    season_length: int = Field(description="Seasonal period assumed for the freq.")
    history_points: int = Field(description="Observations the fit and calibration saw.")
    history: list[SeriesPoint] = Field(
        default_factory=list, description="The observed history, oldest first."
    )
    horizon: int = Field(description="Steps forecast beyond the last observation.")
    points: list[HorizonPoint] = Field(
        default_factory=list, description="The forecast, one row per horizon step."
    )
    model: str = Field(description="The selected model, e.g. 'AutoETS'.")
    selection_metric: str = Field(
        default="smape", description="Metric the model was selected on (lower is better)."
    )
    candidates: list[CandidateScore] = Field(
        default_factory=list, description="Every scored candidate, selected one flagged."
    )
    excluded_models: list[ExcludedModel] = Field(
        default_factory=list, description="Candidates that could not be scored, and why."
    )
    interval_method: IntervalMethod = Field(
        description="'conformal' (calibrated on out-of-sample errors) or 'parametric'."
    )
    interval_method_detail: str = Field(
        description="Exact provenance of the band, e.g. 'ConformalIntervals(n_windows=8)'."
    )
    requested_level: float = Field(description="Coverage level ASKED FOR, e.g. 0.9.")
    backtest: BacktestReport = Field(description="MEASURED accuracy and coverage.")
    model_selected_on_backtest_windows: bool = Field(
        default=True,
        description=(
            "True when the winning model was chosen using the same rolling-origin "
            "windows the reported metrics come from, which makes those metrics a "
            "mildly optimistic in-selection estimate. Stated rather than hidden."
        ),
    )
    generated_at: datetime = Field(description="UTC time this forecast was produced.")


class BurndownPoint(BaseModel):
    """One step of a projected budget burn-down.

    ``cumulative`` is spend-to-date **plus** the forecast points up to and including
    this step, so it is directly comparable with the cap.
    """

    ts: datetime
    step: int
    #: The step's own forecast increment (the per-period spend).
    increment: float
    #: Spend already incurred in the window plus every increment through this step.
    cumulative: float
    #: Envelope bounds on ``cumulative``; see
    #: :attr:`BudgetBurndown.cumulative_bounds_are_calibrated` before quoting them.
    cumulative_lo: float
    cumulative_hi: float
    #: True once ``cumulative`` has reached or passed the cap.
    over_budget: bool


class BudgetBurndown(BaseModel):
    """A cap, the spend against it so far, and where the forecast says it lands.

    The honesty knife-edge here is :attr:`cumulative_bounds_are_calibrated`. The
    per-step ``lo``/``hi`` of a conformal forecast are calibrated **marginally, one
    step at a time**. Adding them up does *not* produce a calibrated interval on the
    cumulative total — the steps are correlated and the sum of marginal quantiles is
    not the quantile of the sum. The envelope is still the useful thing to draw, so
    it is drawn and then explicitly flagged as an envelope, not a guarantee.
    """

    scope: Literal["tenant", "user"] = Field(description="Which cap this burns down.")
    scope_id: int | None = Field(description="The tenant/user id the cap belongs to.")
    window: str = Field(description="Budget window the cap resets on: 'day' | 'month'.")
    limit_usd: float | None = Field(
        description="The configured cap, or None when no cap is set for this scope."
    )
    spent_usd: float = Field(description="Actual spend already incurred in this window.")
    projected_total_usd: float = Field(
        description="Spend-to-date plus every forecast step in the horizon."
    )
    projected_total_lo: float = Field(description="Lower envelope of the projected total.")
    projected_total_hi: float = Field(description="Upper envelope of the projected total.")
    cumulative_bounds_are_calibrated: bool = Field(
        default=False,
        description=(
            "Always False: summed marginal conformal bounds are an envelope, not a "
            "calibrated interval on the cumulative total. Stated, never quietly implied."
        ),
    )
    exhaustion_ts: datetime | None = Field(
        default=None,
        description="First forecast timestamp at which the cap is projected to be hit.",
    )
    exhaustion_step: int | None = Field(
        default=None, description="1-based horizon step of `exhaustion_ts`."
    )
    exhausted_within_horizon: bool = Field(
        description="Whether the cap is projected to be reached inside the horizon."
    )
    headroom_usd: float | None = Field(
        default=None,
        description="limit_usd − projected_total_usd; negative means a projected overrun.",
    )
    interval_method: IntervalMethod = Field(
        description="The kind of band the envelope was built from."
    )
    points: list[BurndownPoint] = Field(
        default_factory=list, description="The burn-down curve, one row per horizon step."
    )
