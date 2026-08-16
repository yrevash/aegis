"""Phase 0 contract tests: the tenancy/governance tables materialise and round-trip.

The four tables (``tenants``, ``budgets``, ``usage_ledger``, ``approvals``) and the
extended ``users`` columns must come out of ``bootstrap`` and round-trip rows against the
schema a deployment actually gets — which is why these now run on the shared scratch
PostgreSQL database (``db`` in ``tests/conftest.py``) instead of the temp-file aiosqlite
one they used to bind.

The change is not cosmetic. On SQLite the JSONB columns degraded to JSON, the native enum
types (``tenant_status``, ``budget_scope``, ``budget_window``, ``approval_status``)
degraded to VARCHAR, and the foreign keys were not enforced at all — so a green run here
was consistent with a schema no production cluster would accept.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pgsupport
from sqlalchemy import select

from app.api.schemas import RiskLevel, Role
from app.data import (
    Approval,
    ApprovalStatus,
    Budget,
    BudgetScope,
    BudgetWindow,
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


async def test_budget_and_usage_ledger_roundtrip(db):
    async with db() as session:
        # The tenant and user are the parents ``budgets.tenant_id`` and ``usage_ledger``'s
        # two foreign keys point at. They were always implied by the ids below; SQLite
        # simply never enforced the references, so the ledger row used to describe spend
        # by a user who did not exist.
        await pgsupport.seed(
            session,
            Tenant(id=1, name="acme"),
            User(id=1, username="ledger-user", tenant_id=1),
            Budget(
                tenant_id=1,
                scope_type=BudgetScope.TENANT,
                scope_id=1,
                window=BudgetWindow.MONTH,
                token_cap=1_000_000,
                usd_cap=250.0,
                rpm=60,
                tpm=40_000,
            ),
            UsageLedger(
                tenant_id=1,
                user_id=1,
                model="fake-generation",
                prompt_tokens=12,
                completion_tokens=8,
                cost_usd=0.0009,
                trace_id="trace-1",
            ),
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


async def test_approval_inbox_row_roundtrip(db):
    deadline = datetime(2026, 8, 5, 12, 0, 0) + timedelta(hours=1)
    async with db() as session:
        # ``approvals`` lives in the backend's own declarative registry and its
        # ``tenant_id`` is a plain column, not a foreign key — so unlike the ledger above
        # this row stands alone and needs no parent seeded.
        session.add(
            Approval(
                id="apr-1",
                run_id="run-1",
                thread_id="run-1",
                tenant_id=1,
                persona="operations_lead",
                action="update_request_status",
                args={"request_id": "R1", "status": "resolved"},
                risk=RiskLevel.HIGH,
                rationale="wide conformal interval",
                ml_snapshot={"prediction": 12.0},
                trace_id="trace-1",
                assignee_tier="tier-1",
                sla_deadline=deadline,
            ),
        )
        await session.commit()

    async with db() as session:
        row = (await session.execute(select(Approval))).scalar_one()
        # Defaults hold: a freshly-inserted inbox row is PENDING and undecided.
        assert row.status is ApprovalStatus.PENDING
        assert row.risk is RiskLevel.HIGH
        assert row.thread_id == row.run_id == "run-1"
        assert row.args == {"request_id": "R1", "status": "resolved"}
        assert row.ml_snapshot == {"prediction": 12.0}
        assert row.decided_at is None
        assert row.decided_by is None
        assert row.created_at is not None
