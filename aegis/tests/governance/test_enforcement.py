"""Budget/rate enforcement, the usage ledger, and the admin rollups.

These exercise ``aegis.governance.enforcement`` directly against the in-memory
aiosqlite database bound by the ``db`` fixture, so the budget reads, ledger writes,
role updates and admin queries all round-trip with no host and no network.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from aegis.gateway.types import BudgetExceededError
from aegis.governance import (
    Budget,
    BudgetScope,
    BudgetWindow,
    LastPlatformAdminError,
    Role,
    Tenant,
    UsageLedger,
    User,
    effective_limits,
    enforce_governance,
    list_budgets,
    list_tenants,
    list_users,
    record_usage,
    update_user_role,
    upsert_budget,
    usage_rollup,
    user_tenant_id,
)


async def _seed(db, *rows):
    async with db() as session:
        for row in rows:
            session.add(row)
        await session.commit()


# ── enforcement ──────────────────────────────────────────────────────────────


async def test_over_token_budget_raises(db):
    await _seed(
        db,
        Budget(
            scope_type=BudgetScope.TENANT,
            scope_id=1,
            window=BudgetWindow.DAY,
            token_cap=100,
        ),
        UsageLedger(tenant_id=1, prompt_tokens=100, completion_tokens=50, cost_usd=0.1),
    )
    with pytest.raises(BudgetExceededError) as ei:
        await enforce_governance(tenant_id=1, user_id=2)
    assert ei.value.scope == "tenant"
    assert ei.value.limit_type == "token_cap"
    assert ei.value.limit == 100


async def test_user_cap_binds_before_tenant(db):
    # Both caps tripped; the user cap is checked first and attributed to the user.
    await _seed(
        db,
        Budget(scope_type=BudgetScope.TENANT, scope_id=1, token_cap=100),
        Budget(scope_type=BudgetScope.USER, scope_id=2, token_cap=10),
        UsageLedger(tenant_id=1, user_id=2, prompt_tokens=20, completion_tokens=0),
    )
    with pytest.raises(BudgetExceededError) as ei:
        await enforce_governance(tenant_id=1, user_id=2)
    assert ei.value.scope == "user"


async def test_under_budget_passes(db):
    await _seed(db, Budget(scope_type=BudgetScope.TENANT, scope_id=1, token_cap=100_000))
    # No breach → returns cleanly.
    await enforce_governance(tenant_id=1, user_id=2)


async def test_rpm_cap_raises_on_recent_calls(db):
    await _seed(
        db,
        Budget(scope_type=BudgetScope.TENANT, scope_id=1, rpm=1),
        UsageLedger(tenant_id=1, prompt_tokens=1, completion_tokens=1),
    )
    with pytest.raises(BudgetExceededError) as ei:
        await enforce_governance(tenant_id=1, user_id=2)
    assert ei.value.limit_type == "rpm"


async def test_ungoverned_call_is_a_noop(db):
    # No tenant bound → enforcement is a full no-op (never touches the DB path).
    await enforce_governance(tenant_id=None, user_id=None)


async def test_record_usage_writes_ledger_row(db):
    await record_usage(
        tenant_id=1,
        user_id=2,
        model="m",
        prompt_tokens=11,
        completion_tokens=7,
        cost_usd=0.0002,
        trace_id="t-1",
    )
    async with db() as session:
        rows = (
            await session.execute(select(UsageLedger).where(UsageLedger.tenant_id == 1))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].user_id == 2
    assert rows[0].prompt_tokens == 11
    assert rows[0].completion_tokens == 7


# ── effective limits (inward clamp) ─────────────────────────────────────────


async def test_effective_limits_clamp_user_inward_to_tenant(db):
    await _seed(
        db,
        Budget(scope_type=BudgetScope.TENANT, scope_id=1, token_cap=1000, rpm=100),
        Budget(scope_type=BudgetScope.USER, scope_id=2, token_cap=10),
    )
    limits = await effective_limits(1, 2)
    # User cap (10) binds over the looser tenant cap (1000); rpm only set at tenant.
    assert limits.token_cap == 10
    assert limits.rpm == 100


async def test_effective_limits_unscoped_is_uncapped(db):
    limits = await effective_limits(None, None)
    assert limits.token_cap is None and limits.usd_cap is None


# ── admin rollups ───────────────────────────────────────────────────────────


async def test_upsert_budget_is_idempotent_on_the_natural_key(db):
    first = await upsert_budget(scope_type="tenant", scope_id=1, token_cap=100, tenant_id=1)
    second = await upsert_budget(scope_type="tenant", scope_id=1, token_cap=250, tenant_id=1)
    assert first.id == second.id  # re-posting the same scope+window updates in place
    assert second.token_cap == 250
    rows = await list_budgets(tenant_id=1)
    assert len(rows) == 1


async def test_list_tenants_and_users_and_usage_rollup(db):
    async with db() as session:
        session.add(Tenant(name="acme"))
        session.add(User(username="alice", role=Role.CLIENT, tenant_id=1))
        session.add(
            UsageLedger(
                tenant_id=1, model="gpt", prompt_tokens=10, completion_tokens=5, cost_usd=0.5
            )
        )
        await session.commit()

    assert [t.name for t in await list_tenants()] == ["acme"]
    users = await list_users(tenant_id=1)
    assert users[0].username == "alice"
    assert await user_tenant_id(1) == 1
    assert await user_tenant_id(999) is None

    pt, ct, cost, by_model, series = await usage_rollup(tenant_id=1)
    assert (pt, ct) == (10, 5)
    assert round(cost, 6) == 0.5
    assert by_model[0].model == "gpt"
    assert len(series) == 1


# ── last-platform-admin lockout ─────────────────────────────────────────────


async def test_last_platform_admin_cannot_be_demoted(db):
    async with db() as session:
        session.add(User(username="root", role=Role.ADMIN, tenant_id=None))
        await session.commit()
    with pytest.raises(LastPlatformAdminError):
        await update_user_role(1, Role.CLIENT)
    # The role is unchanged after the refusal.
    async with db() as session:
        user = await session.get(User, 1)
        assert user.role is Role.ADMIN


async def test_platform_admin_demotable_when_another_remains(db):
    async with db() as session:
        session.add(User(username="root1", role=Role.ADMIN, tenant_id=None))
        session.add(User(username="root2", role=Role.ADMIN, tenant_id=None))
        await session.commit()
    row = await update_user_role(1, Role.CLIENT)
    assert row is not None and row.role is Role.CLIENT


async def test_update_user_role_scoped_to_tenant_rejects_outsider(db):
    async with db() as session:
        session.add(User(username="u", role=Role.CLIENT, tenant_id=5))
        await session.commit()
    # A tenant-admin caller scoped to tenant 9 cannot touch a tenant-5 user.
    assert await update_user_role(1, Role.DEVOPS, tenant_scope=9) is None
    # …but the owning tenant can.
    row = await update_user_role(1, Role.DEVOPS, tenant_scope=5)
    assert row is not None and row.role is Role.DEVOPS
