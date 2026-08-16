"""Read the usage ledger as a regular time series (the platform/team use case).

``usage_ledger`` already carries everything a spend forecast needs — an indexed
``ts``, ``tenant_id``, ``user_id`` and ``cost_usd``, plus the newer
``audio_seconds``/``images`` unit columns — so no new table, no new writer and no
backfill is involved. This module only *reads* it.

Two decisions worth stating.

**A bucket with no rows is a real zero.** Nobody made a model call that day, so the
spend was ``0.00``. Gap-filling that as zero (via
:func:`aegis.forecast.bucket_events`) is the honest reading, and it matters: dropping
empty buckets would compress the calendar and make a weekly seasonality look like
something else entirely.

**Tenant scoping is app-level AND RLS.** Every query filters ``tenant_id`` explicitly
*and* binds the Postgres RLS scope, exactly as the admin rollups do — the same
belt-and-suspenders isolation, reused rather than reinvented.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from aegis.forecast import SeriesPoint, bucket_events
from aegis.governance.models import UsageLedger
from sqlalchemy import select

from app.data import governance as _governance
from app.data.session import get_sessionmaker

__all__ = ["LedgerMetric", "ledger_series", "window_spend"]

#: Which quantity to roll the ledger up into.
LedgerMetric = Literal["spend", "calls"]

#: How far back a ledger read reaches by default, in days. Long enough for a daily
#: series to show a weekly cycle and still leave held-out backtest windows.
DEFAULT_LOOKBACK_DAYS = 120

#: Seconds in each budget window, mirroring ``aegis.governance.enforcement``.
_WINDOW_SECONDS: dict[str, int] = {"day": 24 * 3600, "month": 30 * 24 * 3600}


def _now_naive() -> datetime:
    """Return the current UTC time as a naive datetime.

    The ledger's ``ts`` is naive UTC on SQLite. On PostgreSQL it is **not**: the
    startup alignment in :mod:`app.data.session` converts pre-existing naive timestamp
    columns to ``timestamptz``, so the column is tz-aware there.

    That mismatch is worth knowing about. Comparing a naive bound against a
    ``timestamptz`` column makes PostgreSQL interpret the bound in the *session*
    ``TimeZone``, which this application does not pin — so on a server whose timezone
    is not UTC, the lookback and budget windows below are offset by that amount.
    Fixing it properly means making the bound dialect-aware rather than assuming
    naive, which is a behaviour change and needs its own test.

    Returns:
        The current UTC instant, tz-naive.
    """
    return datetime.now(UTC).replace(tzinfo=None)


async def _rows(
    tenant_id: int | None,
    user_id: int | None,
    since: datetime,
) -> list[tuple[datetime, float, int]]:
    """Fetch ``(ts, cost_usd, 1)`` for every ledger row in scope since ``since``.

    Args:
        tenant_id: Tenant to scope to; ``None`` reads across tenants (platform-admin).
        user_id: Optional further scoping to a single principal.
        since: Naive-UTC lower bound on ``ts``.

    Returns:
        One tuple per ledger row, oldest first.
    """
    async with get_sessionmaker()() as session:
        await _governance.set_tenant_scope(session, tenant_id)
        stmt = select(UsageLedger.ts, UsageLedger.cost_usd).where(UsageLedger.ts >= since)
        if tenant_id is not None:
            stmt = stmt.where(UsageLedger.tenant_id == tenant_id)
        if user_id is not None:
            stmt = stmt.where(UsageLedger.user_id == user_id)
        result = await session.execute(stmt.order_by(UsageLedger.ts.asc()))
        return [(ts, float(cost), 1) for ts, cost in result.all()]


async def ledger_series(
    *,
    tenant_id: int | None,
    user_id: int | None = None,
    metric: LedgerMetric = "spend",
    freq: str = "D",
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> list[SeriesPoint]:
    """Roll the usage ledger into a regular, gap-filled series.

    Args:
        tenant_id: Tenant to scope to; ``None`` reads across tenants.
        user_id: Optional further scoping to one principal.
        metric: ``"spend"`` sums ``cost_usd``; ``"calls"`` counts ledger rows.
        freq: Bucket width — one of the aliases in :data:`aegis.forecast.FREQ_SEASON`.
        lookback_days: How far back to read.

    Returns:
        The bucketed series, oldest first, with empty buckets filled as ``0.0``.
        Empty when the ledger has no rows in scope — the caller decides what to do
        about that, and the honest answer is to refuse rather than to forecast it.
    """
    since = _now_naive() - timedelta(days=lookback_days)
    rows = await _rows(tenant_id, user_id, since)
    events = [(ts, cost if metric == "spend" else float(count)) for ts, cost, count in rows]
    return bucket_events(events, freq, fill_gaps=True)


async def window_spend(
    *,
    tenant_id: int | None,
    user_id: int | None = None,
    window: str = "month",
) -> float:
    """Return spend already incurred inside the current budget window.

    This is the burn-down's starting height. It is summed over exactly the rolling
    span :mod:`aegis.governance.enforcement` enforces the cap over, so the projection
    and the enforcement agree on what "spent so far" means.

    Args:
        tenant_id: Tenant to scope to.
        user_id: Optional further scoping to one principal.
        window: ``"day"`` or ``"month"``.

    Returns:
        Total ``cost_usd`` in the window.

    Raises:
        ValueError: If ``window`` is not a recognised budget window.
    """
    if window not in _WINDOW_SECONDS:
        raise ValueError(f"unknown budget window {window!r}; expected 'day' or 'month'")
    since = _now_naive() - timedelta(seconds=_WINDOW_SECONDS[window])
    rows = await _rows(tenant_id, user_id, since)
    return sum(cost for _, cost, _ in rows)
