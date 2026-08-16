"""Compose series + forecaster + burn-down for the ``/forecast`` API surface.

Three responsibilities, all of them plumbing:

1. **Get the series** — from :mod:`app.forecast.ledger` (platform/team) or
   :mod:`app.forecast.domain` (client, through the adapter seam).
2. **Keep the event loop alive** — fitting AutoARIMA/AutoETS is a few seconds of
   pure CPU inside numba-compiled code, which would block every other request on the
   worker. It runs in a worker thread via :func:`asyncio.to_thread`.
3. **Memoise** — the same series and horizon give a deterministic answer, and the
   console polls. A small keyed cache stops a dashboard refresh from re-fitting.

No decision about honesty is made here: the refusals
(:class:`~aegis.forecast.types.InsufficientHistoryError`,
:class:`~aegis.forecast.types.ForecastFitError`) propagate untouched to the route,
which turns them into a typed refusal — **HTTP 200 carrying ``available=False`` and a
``ForecastRefusal`` code/reason**, not an HTTP error status. The console renders that
as a stated reason rather than a failed request, which is the point: "we will not
forecast this, and here is why" is an answer, not an outage.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from aegis.forecast import (
    BudgetBurndown,
    ForecastResult,
    SeriesPoint,
    forecast_series,
    project_burndown,
)

from app.forecast.domain import DOMAIN_SERIES_LABEL, domain_series
from app.forecast.ledger import LedgerMetric, ledger_series, window_spend

__all__ = [
    "MAX_HORIZON",
    "clear_cache",
    "domain_forecast",
    "ledger_burndown",
    "ledger_forecast",
]

#: Upper bound on a requested horizon. Beyond this the minimum-history requirement
#: exceeds anything the ledger plausibly holds, so the request would only ever be
#: refused — bounding it turns a slow refusal into an immediate, clear one.
MAX_HORIZON = 60

_CACHE: dict[tuple[object, ...], ForecastResult] = {}
#: Cap on the memo table so a hostile spread of horizons cannot grow it without limit.
_CACHE_MAX = 32


def clear_cache() -> None:
    """Drop every memoised forecast (used by tests and after a data reload)."""
    _CACHE.clear()


def _fingerprint(points: list[SeriesPoint]) -> tuple[object, ...]:
    """Return a cheap identity for a series, safe to use as a cache key.

    Args:
        points: The series, oldest first.

    Returns:
        A hashable summary — length, endpoints and total — which changes whenever the
        underlying data does, without hashing every observation.
    """
    if not points:
        return (0,)
    return (
        len(points),
        points[0].ts.isoformat(),
        points[-1].ts.isoformat(),
        round(sum(p.value for p in points), 6),
    )


async def _forecast(
    points: list[SeriesPoint],
    *,
    series_id: str,
    label: str,
    unit: str | None,
    data_source: str,
    horizon: int,
    level: float,
) -> ForecastResult:
    """Forecast off the event loop, memoised on the series' fingerprint.

    Args:
        points: The observed series, oldest first.
        series_id: Stable identifier for the series.
        label: Human label for the series.
        unit: Unit of the values, e.g. ``"USD"``.
        data_source: Provenance tag recorded on the result.
        horizon: Steps to forecast.
        level: Coverage level to request.

    Returns:
        The :class:`~aegis.forecast.types.ForecastResult`.
    """
    key = (series_id, horizon, level, _fingerprint(points))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    result = await asyncio.to_thread(
        forecast_series,
        points,
        series_id=series_id,
        label=label,
        unit=unit,
        data_source=data_source,
        horizon=horizon,
        level=level,
        interval="conformal",
    )
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[key] = result
    return result


async def ledger_forecast(
    *,
    tenant_id: int | None,
    metric: LedgerMetric = "spend",
    horizon: int = 14,
    level: float = 0.9,
) -> ForecastResult:
    """Forecast a tenant's daily spend or call volume from the usage ledger.

    Args:
        tenant_id: Tenant to scope to; ``None`` forecasts the whole platform.
        metric: ``"spend"`` (USD) or ``"calls"`` (model calls).
        horizon: Days to forecast ahead.
        level: Coverage level to request.

    Returns:
        The :class:`~aegis.forecast.types.ForecastResult`.

    Raises:
        InsufficientHistoryError: If the ledger holds too little history.
        ForecastFitError: If no candidate model could be fitted.
    """
    points = await ledger_series(tenant_id=tenant_id, metric=metric)
    scope = "platform" if tenant_id is None else f"tenant:{tenant_id}"
    label, unit = (
        ("Daily spend", "USD") if metric == "spend" else ("Daily model calls", "calls")
    )
    return await _forecast(
        points,
        series_id=f"{scope}:{metric}",
        label=label,
        unit=unit,
        data_source="usage_ledger",
        horizon=horizon,
        level=level,
    )


async def ledger_burndown(
    *,
    tenant_id: int | None,
    scope: Literal["tenant", "user"] = "tenant",
    window: str = "month",
    limit_usd: float | None,
    horizon: int = 14,
    level: float = 0.9,
) -> tuple[ForecastResult, BudgetBurndown]:
    """Forecast spend and project it against the tenant's cap.

    Args:
        tenant_id: Tenant to scope to.
        scope: Which cap the projection burns down.
        window: Budget window the cap resets on, ``"day"`` or ``"month"``.
        limit_usd: The configured cap, or ``None`` when none is set.
        horizon: Days to project ahead.
        level: Coverage level to request on the underlying forecast.

    Returns:
        ``(forecast, burndown)`` — the spend forecast and the projection against the cap.

    Raises:
        InsufficientHistoryError: If the ledger holds too little history.
        ForecastFitError: If no candidate model could be fitted.
    """
    forecast = await ledger_forecast(
        tenant_id=tenant_id, metric="spend", horizon=horizon, level=level
    )
    spent = await window_spend(tenant_id=tenant_id, window=window)
    burndown = project_burndown(
        forecast,
        scope=scope,
        scope_id=tenant_id,
        window=window,
        limit_usd=limit_usd,
        spent_usd=spent,
    )
    return forecast, burndown


async def domain_forecast(*, horizon: int = 14, level: float = 0.9) -> ForecastResult:
    """Forecast the client's domain demand series through the adapter seam.

    Args:
        horizon: Days to forecast ahead.
        level: Coverage level to request.

    Returns:
        The :class:`~aegis.forecast.types.ForecastResult`.

    Raises:
        InsufficientHistoryError: If the domain series is too short.
        ForecastFitError: If no candidate model could be fitted.
    """
    points = await asyncio.to_thread(domain_series)
    return await _forecast(
        points,
        series_id="domain:demand",
        label=DOMAIN_SERIES_LABEL,
        unit="requests",
        data_source="adapter",
        horizon=horizon,
        level=level,
    )
