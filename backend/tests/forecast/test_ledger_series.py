"""The usage-ledger reader: bucketing, gap-filling and tenant isolation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from aegis.governance.models import UsageLedger

from app.forecast.ledger import ledger_series, window_spend

pytestmark = pytest.mark.asyncio


def _naive_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _midday_days_ago(days: int) -> datetime:
    """A naive-UTC timestamp at noon, ``days`` before today.

    Tests that assert on **calendar-day buckets** must not anchor on "now": offsetting
    a few hours either side of the current time silently crosses midnight whenever the
    suite runs near a day boundary, and two rows meant for one day land in two buckets.
    Noon leaves twelve hours of slack in both directions, and ``days >= 1`` keeps the
    timestamp safely in the past regardless of the hour the suite runs at.
    """
    return (_naive_now() - timedelta(days=days)).replace(
        hour=12, minute=0, second=0, microsecond=0
    )


async def _seed(sessionmaker, rows: list[tuple[int, datetime, float]]) -> None:
    """Insert ``(tenant_id, ts, cost_usd)`` ledger rows."""
    async with sessionmaker() as session:
        for tenant_id, ts, cost in rows:
            session.add(
                UsageLedger(tenant_id=tenant_id, ts=ts, cost_usd=cost, model="m")
            )
        await session.commit()


async def test_an_empty_ledger_yields_an_empty_series_not_a_flat_line(db):
    assert await ledger_series(tenant_id=1) == []


async def test_rows_are_bucketed_daily_with_empty_days_filled_as_real_zeros(db):
    await _seed(
        db,
        [
            (1, _midday_days_ago(3) + timedelta(hours=1), 2.0),
            (1, _midday_days_ago(3) - timedelta(hours=5), 3.0),
            (1, _midday_days_ago(1), 7.0),
        ],
    )
    series = await ledger_series(tenant_id=1)
    # Three calendar days spanned: 5.00 on the first, nothing on the second, 7.00 on
    # the third. The middle bucket is a real zero — nobody called a model that day.
    assert [p.value for p in series] == [5.0, 0.0, 7.0]
    assert series[0].ts < series[1].ts < series[2].ts


async def test_the_series_never_crosses_a_tenant_boundary(db):
    now = _naive_now()
    await _seed(db, [(1, now - timedelta(days=1), 5.0), (2, now - timedelta(days=1), 99.0)])
    assert [p.value for p in await ledger_series(tenant_id=1)] == [5.0]
    assert [p.value for p in await ledger_series(tenant_id=2)] == [99.0]
    # No tenant filter aggregates the platform, which is the platform-admin view.
    assert [p.value for p in await ledger_series(tenant_id=None)] == [104.0]


async def test_the_calls_metric_counts_rows_rather_than_summing_cost(db):
    now = _naive_now()
    await _seed(db, [(1, now - timedelta(days=1), 5.0), (1, now - timedelta(days=1), 6.0)])
    assert [p.value for p in await ledger_series(tenant_id=1, metric="calls")] == [2.0]


async def test_rows_older_than_the_lookback_are_not_read(db):
    now = _naive_now()
    await _seed(db, [(1, now - timedelta(days=400), 100.0), (1, now - timedelta(days=1), 4.0)])
    series = await ledger_series(tenant_id=1, lookback_days=30)
    assert [p.value for p in series] == [4.0]


async def test_window_spend_sums_only_the_current_budget_window(db):
    now = _naive_now()
    await _seed(
        db,
        [
            (1, now - timedelta(hours=2), 1.5),
            (1, now - timedelta(days=5), 10.0),
            (1, now - timedelta(days=45), 99.0),
        ],
    )
    assert await window_spend(tenant_id=1, window="day") == pytest.approx(1.5)
    assert await window_spend(tenant_id=1, window="month") == pytest.approx(11.5)


async def test_an_unknown_window_is_rejected_by_name(db):
    with pytest.raises(ValueError, match="unknown budget window"):
        await window_spend(tenant_id=1, window="year")
