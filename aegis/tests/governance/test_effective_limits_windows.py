"""``effective_limits`` never compares a month cap with a day cap.

``_budgets_for`` returns every budget row for a principal across every window, and
``effective_limits`` took the *first* tenant row and the *first* user row regardless of
window — so a tenant's **month** cap could clamp a user's **day** cap, and the figure it
returned described no cap that exists anywhere.

This was a **display** defect, not an enforcement one, and the second test says so in
executable form: ``enforce_governance`` reads the same rows and sums the ledger over each
row's *own* window, so what actually bound a call was always right. What was wrong was
the number an operator reads — which is the exact failure mode Phase 7 exists to remove.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aegis.gateway.types import BudgetExceededError
from aegis.governance import (
    Budget,
    BudgetScope,
    BudgetWindow,
    UsageLedger,
    effective_limits,
    enforce_governance,
)

from .._seed import seed

pytestmark = pytest.mark.asyncio

_TENANT = 1
_USER = 2


def _tenant_month(**caps) -> Budget:  # noqa: ANN003
    return Budget(
        tenant_id=_TENANT,
        scope_type=BudgetScope.TENANT,
        scope_id=_TENANT,
        window=BudgetWindow.MONTH,
        **caps,
    )


def _user_day(**caps) -> Budget:  # noqa: ANN003
    return Budget(
        tenant_id=_TENANT,
        scope_type=BudgetScope.USER,
        scope_id=_USER,
        window=BudgetWindow.DAY,
        **caps,
    )


async def test_a_month_cap_does_not_clamp_a_day_cap(db):
    """$200/month must not be reported as clamping $50/day down to itself.

    Pre-fix this returned ``usd_cap=50.0`` by luck (the min happened to be the day
    figure) but ``token_cap=400_000`` — the tenant's *monthly* token allowance presented
    as the user's *daily* one, because it was the smaller of two numbers that measure
    different things. The window is now reported alongside, so the figure and its
    denominator cannot be separated.
    """
    await seed(
        db,
        _tenant_month(token_cap=400_000, usd_cap=200.0),
        _user_day(token_cap=1_000_000, usd_cap=50.0),
    )

    limits = await effective_limits(_TENANT, _USER)

    assert limits.window == "day"
    assert limits.token_cap == 1_000_000, (
        "the tenant's MONTH token cap was reported as the user's DAY cap"
    )
    assert limits.usd_cap == 50.0

    # And the month is still readable — as the month, when asked for by name.
    monthly = await effective_limits(_TENANT, _USER, window=BudgetWindow.MONTH)
    assert monthly.window == "month"
    assert monthly.token_cap == 400_000 and monthly.usd_cap == 200.0


async def test_the_same_rows_were_always_enforced_window_by_window(db):
    """The defect never let a call through, or blocked one: it was display only.

    Same two rows, and $210 spent three days ago — inside the tenant's *month* window,
    outside the user's *day* one. The enforcer blocks on the month row and not the day
    row, which it can only do by measuring each row over that row's own window. That is
    the half that was always right; the resolver above was the half reporting a mixture.
    """
    three_days_ago = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=3)
    await seed(
        db,
        _tenant_month(usd_cap=200.0),
        _user_day(usd_cap=50.0),
        UsageLedger(
            tenant_id=_TENANT, user_id=_USER, cost_usd=210.0, ts=three_days_ago
        ),
    )

    with pytest.raises(BudgetExceededError) as excinfo:
        await enforce_governance(tenant_id=_TENANT, user_id=_USER)

    assert excinfo.value.scope == "tenant", (
        "the user's DAY cap must not see three-day-old spend"
    )
    assert excinfo.value.limit == 200.0, "the month cap is what bound, and it bound correctly"


async def test_a_month_only_principal_is_reported_as_month_not_as_uncapped(db):
    """Reporting a fixed window would call a month-capped tenant uncapped for the day.

    That would be a new lie in place of the old one, so the reported window is the
    narrowest one that actually governs — here, the only one.
    """
    await seed(db, _tenant_month(usd_cap=75.0))

    limits = await effective_limits(_TENANT, None)

    assert limits.window == "month"
    assert limits.usd_cap == 75.0


async def test_rate_caps_are_per_minute_whatever_window_the_row_carries(db):
    """``rpm``/``tpm`` are not windowed, and the enforcer checks them on every row.

    So the tightest across *all* rows binds, and the resolver must say the same — a
    ``month`` row carrying ``rpm=10`` is a real per-minute limit, not a monthly one.
    """
    await seed(db, _tenant_month(rpm=10), _user_day(rpm=60))

    limits = await effective_limits(_TENANT, _USER)

    assert limits.window == "day"
    assert limits.rpm == 10
