"""The tenancy/governance tables create on PostgreSQL and round-trip rows.

The governance tables (``tenants``, ``users``, ``budgets``, ``usage_ledger``,
``audit_log``) register on ``aegis.data.AegisBase`` and must materialise on the real
cluster the suite runs against, including the intra-package foreign keys
(``users.tenant_id`` → ``tenants.id``, ``usage_ledger.tenant_id`` → ``tenants.id``).

Those foreign keys are the reason this file moved. SQLite does not enforce them unless
``PRAGMA foreign_keys=ON`` is issued per connection, which the old fixture never did, so
the ledger round-trip below happily wrote a row pointing at a tenant that did not exist —
a shape the production database rejects outright.
"""

from __future__ import annotations

from sqlalchemy import select

from aegis.governance import (
    Budget,
    BudgetScope,
    BudgetWindow,
    Role,
    Tenant,
    TenantStatus,
    UsageLedger,
    User,
)


async def test_tenant_and_extended_user_roundtrip(db):
    async with db() as session:
        tenant = Tenant(name="acme")
        session.add(tenant)
        await session.flush()
        session.add(
            User(
                username="alice",
                role=Role.ADMIN,
                tenant_id=tenant.id,
                email="alice@acme.test",
                password_hash="argon2$fake",
            )
        )
        await session.commit()

    async with db() as session:
        tenant = (await session.execute(select(Tenant))).scalar_one()
        user = (await session.execute(select(User))).scalar_one()
        assert tenant.status is TenantStatus.ACTIVE
        assert tenant.created_at is not None
        assert user.tenant_id == tenant.id
        assert user.email == "alice@acme.test"
        assert user.password_hash == "argon2$fake"
        # New auth field defaults to active without being set explicitly.
        assert user.is_active is True


async def test_user_defaults_to_least_privileged_client(db):
    async with db() as session:
        session.add(User(username="bob"))
        await session.commit()
    async with db() as session:
        user = (await session.execute(select(User))).scalar_one()
        assert user.role is Role.CLIENT
        assert user.tenant_id is None


async def test_budget_and_usage_ledger_roundtrip(db):
    async with db() as session:
        # The ledger's tenant/user references are real foreign keys, so the rows they
        # point at have to exist. On the old SQLite fixture they did not and nothing
        # complained — spend was being attributed to a tenant the database had no row for.
        tenant = Tenant(name="ledger-tenant")
        session.add(tenant)
        await session.flush()
        user = User(username="ledger-user", tenant_id=tenant.id)
        session.add(user)
        await session.flush()
        session.add(
            Budget(
                scope_type=BudgetScope.TENANT,
                scope_id=tenant.id,
                window=BudgetWindow.MONTH,
                token_cap=1_000_000,
                usd_cap=250.0,
                rpm=60,
                tpm=40_000,
            )
        )
        session.add(
            UsageLedger(
                tenant_id=tenant.id,
                user_id=user.id,
                model="fake-generation",
                prompt_tokens=12,
                completion_tokens=8,
                cost_usd=0.0009,
                trace_id="trace-1",
            )
        )
        await session.commit()

    async with db() as session:
        budget = (await session.execute(select(Budget))).scalar_one()
        ledger = (await session.execute(select(UsageLedger))).scalar_one()
        assert budget.scope_type is BudgetScope.TENANT
        assert budget.window is BudgetWindow.MONTH
        assert budget.token_cap == 1_000_000
        assert ledger.prompt_tokens == 12
        assert ledger.cost_usd == 0.0009
        assert ledger.ts is not None
