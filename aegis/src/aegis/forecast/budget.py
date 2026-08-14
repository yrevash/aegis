"""Project a spend forecast against a budget cap — the burn-down.

Pure Python: it consumes an already-produced
:class:`~aegis.forecast.types.ForecastResult` and never imports the forecasting
stack itself, so the projection arithmetic is testable with hand-written points.

The one thing this module must not do is imply more certainty than it has. Summing
the per-step conformal bounds gives a useful envelope on cumulative spend, but the
sum of marginal quantiles is not the quantile of the sum — consecutive forecast
errors are correlated, so the envelope is neither conservative nor calibrated in
any provable sense. It is therefore returned with
:attr:`~aegis.forecast.types.BudgetBurndown.cumulative_bounds_are_calibrated` pinned
to ``False`` rather than presented as a coverage claim.
"""

from __future__ import annotations

from typing import Literal

from aegis.forecast.types import BudgetBurndown, BurndownPoint, ForecastResult

__all__ = ["project_burndown"]


def project_burndown(
    forecast: ForecastResult,
    *,
    scope: Literal["tenant", "user"],
    scope_id: int | None,
    window: str,
    limit_usd: float | None,
    spent_usd: float,
) -> BudgetBurndown:
    """Accumulate a spend forecast onto spend-to-date and find the exhaustion point.

    Args:
        forecast: A per-period **spend** forecast (its ``unit`` should be currency).
        scope: Which cap is being burnt down — ``"tenant"`` or ``"user"``.
        scope_id: The id of that tenant/user, or ``None`` for an unscoped principal.
        window: The budget window the cap resets on, ``"day"`` or ``"month"``.
        limit_usd: The configured cap, or ``None`` when no cap is set. With no cap
            the curve is still projected; it simply never crosses anything.
        spent_usd: Spend already incurred inside the current window.

    Returns:
        A :class:`~aegis.forecast.types.BudgetBurndown` carrying the projected curve,
        the projected total with its (explicitly uncalibrated) envelope, and the first
        step at which the cap is projected to be reached.
    """
    running = float(spent_usd)
    running_lo = float(spent_usd)
    running_hi = float(spent_usd)
    exhaustion_ts = None
    exhaustion_step = None
    rows: list[BurndownPoint] = []

    for p in forecast.points:
        running += p.point
        running_lo += p.lo
        running_hi += p.hi
        over = limit_usd is not None and running >= limit_usd
        if over and exhaustion_ts is None:
            exhaustion_ts, exhaustion_step = p.ts, p.step
        rows.append(
            BurndownPoint(
                ts=p.ts,
                step=p.step,
                increment=p.point,
                cumulative=running,
                cumulative_lo=running_lo,
                cumulative_hi=running_hi,
                over_budget=over,
            )
        )

    return BudgetBurndown(
        scope=scope,
        scope_id=scope_id,
        window=window,
        limit_usd=limit_usd,
        spent_usd=float(spent_usd),
        projected_total_usd=running,
        projected_total_lo=running_lo,
        projected_total_hi=running_hi,
        cumulative_bounds_are_calibrated=False,
        exhaustion_ts=exhaustion_ts,
        exhaustion_step=exhaustion_step,
        exhausted_within_horizon=exhaustion_ts is not None,
        headroom_usd=None if limit_usd is None else limit_usd - running,
        interval_method=forecast.interval_method,
        points=rows,
    )
