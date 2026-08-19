"""``python -m app.seed`` — the two-tenant starting state, proved against Postgres (§3.8).

Everything here runs on the scratch database bound by the ``db`` fixture, served by the
``NOSUPERUSER NOBYPASSRLS`` role, so the isolation assertions are made against live
row-security policies rather than against the app-level ``WHERE tenant_id`` filter alone.

What is proved:

* the seed writes the shape it documents — two tenants, each with a tenant admin and two
  users, budgets and documents;
* running it a second time creates nothing and changes nothing (idempotency, checked by
  comparing the rows themselves, not only their count);
* each tenant sees **exactly** its own documents under its own bound scope, and a scope
  belonging to neither sees none — the isolation the seed exists to make testable;
* the accounts it writes are ones a browser can actually log in with, at the tier their
  tenancy implies;
* the approvals inbox is **not empty** on a fresh database (§7.1) — each tenant starts
  with a parked gate its own admin may decide, Aegis starts with one of its own, and
  each is attributed to the user whose run raised it.
"""

from __future__ import annotations

import pytest
from aegis.governance.types import Role
from aegis.jobs.models import Document, JobStatus
from sqlalchemy import func, select

from app.core.security import PLATFORM_ADMIN, TENANT_ADMIN
from app.data import (
    Approval,
    ApprovalStatus,
    Budget,
    BudgetScope,
    Tenant,
    User,
    get_sessionmaker,
    set_tenant_scope,
)
from app.seed import (
    PLATFORM_APPROVAL,
    PLATFORM_PRINCIPALS,
    TENANTS,
    seed,
    seed_password,
)

pytestmark = pytest.mark.asyncio


async def _count(model) -> int:  # noqa: ANN001 - any mapped class
    async with get_sessionmaker()() as session:
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def _users() -> list[tuple[str, int, str | None, str]]:
    """Return ``(username, id, password_hash, role)`` for every seeded account."""
    async with get_sessionmaker()() as session:
        rows = (await session.execute(select(User).order_by(User.id))).scalars().all()
    return [(u.username, u.id, u.password_hash, u.role.value) for u in rows]


async def _documents_visible_to(tenant_id: int) -> list[tuple[str, int | None]]:
    """Return the documents a request scoped to ``tenant_id`` can read."""
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, tenant_id)
        rows = (
            await session.execute(
                select(Document.filename, Document.tenant_id).order_by(Document.id)
            )
        ).all()
    return [(name, owner) for name, owner in rows]


async def test_seed_writes_two_tenants_with_admins_users_budgets_and_documents(db):
    summary = await seed()

    expected_users = len(PLATFORM_PRINCIPALS) + sum(1 + len(t.users) for t in TENANTS)
    assert summary.created == {
        "tenants": len(TENANTS),
        "users": expected_users,
        # Only the user-scope budgets are created *by the seed*: a tenant's own budget
        # row is written by ``create_tenant`` in the same transaction as the tenant, so
        # by the time the seed looks it is already there. The row count below is still 4.
        "budgets": len(TENANTS),
        "documents": sum(len(t.documents) for t in TENANTS),
        # One parked gate per tenant, plus Aegis's own un-tenanted one.
        "approvals": len(TENANTS) + 1,
    }
    assert await _count(Tenant) == 2
    assert await _count(User) == expected_users
    assert await _count(Budget) == 4
    assert await _count(Document) == 6

    async with get_sessionmaker()() as session:
        tenants = (await session.execute(select(Tenant).order_by(Tenant.id))).scalars().all()
        by_name = {t.name: t.id for t in tenants}
        assert set(by_name) == {t.name for t in TENANTS}
        for spec in TENANTS:
            tenant_id = by_name[spec.name]
            members = (
                await session.execute(select(User).where(User.tenant_id == tenant_id))
            ).scalars().all()
            # One tenant admin (an ``admin`` row *with* a tenant is the tenant_admin
            # tier) and two other members.
            assert {u.username for u in members} == {
                spec.admin.username,
                *(m.username for m in spec.users),
            }
            assert sum(1 for u in members if u.role.value == "admin") == 1
            # Nothing was parsed, so nothing claims a page count.
            documents = (
                await session.execute(
                    select(Document).where(Document.tenant_id == tenant_id)
                )
            ).scalars().all()
            assert len(documents) == len(spec.documents)
            assert all(d.status is JobStatus.PENDING for d in documents)
            assert all(d.page_count is None and d.chunk_count is None for d in documents)


async def test_a_tenants_budget_carries_every_cap_its_spec_declares(db):
    """``create_tenant`` writes the USD cap; the seed must still fill the rest.

    The tenant's ``budgets`` row now exists before the seed reaches it, so a
    get-or-create that returned early on "already present" would leave the row with a
    USD cap and no token, rpm or tpm cap — a budget that looks set on the screen and
    binds on one dimension out of four.
    """
    await seed()
    async with get_sessionmaker()() as session:
        tenants = {
            t.name: t.id
            for t in (await session.execute(select(Tenant))).scalars().all()
        }
        for spec in TENANTS:
            row = (
                await session.execute(
                    select(Budget).where(
                        Budget.scope_type == BudgetScope.TENANT,
                        Budget.scope_id == tenants[spec.name],
                    )
                )
            ).scalars().one()
            assert row.usd_cap == pytest.approx(spec.usd_cap)
            assert row.token_cap == spec.token_cap
            assert row.rpm == spec.rpm
            assert row.tpm == spec.tpm


async def test_running_the_seed_twice_creates_nothing_and_changes_nothing(db):
    await seed()
    before = await _users()
    counts_before = [await _count(m) for m in (Tenant, User, Budget, Document)]

    second = await seed()

    assert second.created == {}, second.created
    assert second.total_created == 0
    assert second.existing == {
        "tenants": 2,
        "users": len(before),
        "budgets": 4,
        "documents": 6,
        "approvals": 3,
    }
    assert [await _count(m) for m in (Tenant, User, Budget, Document)] == counts_before
    # Same rows, same ids, same hashes: a re-hashed password would silently revert a
    # rotation, and a re-created row would break every foreign key pointing at the old id.
    assert await _users() == before


async def test_each_tenant_sees_only_its_own_seeded_documents(db):
    await seed()
    async with get_sessionmaker()() as session:
        tenants = (await session.execute(select(Tenant).order_by(Tenant.id))).scalars().all()
    assert len(tenants) == 2
    first, second = tenants

    visible_first = await _documents_visible_to(first.id)
    visible_second = await _documents_visible_to(second.id)

    assert len(visible_first) == 3
    assert len(visible_second) == 3
    assert all(owner == first.id for _, owner in visible_first)
    assert all(owner == second.id for _, owner in visible_second)
    # Not merely "each sees three": the two sets must be disjoint, or a policy that
    # returned everything to everyone would still satisfy the counts above.
    assert not {name for name, _ in visible_first} & {name for name, _ in visible_second}

    # A scope belonging to neither tenant reads nothing. The unscoped case is
    # deliberately fail-open (see ``_TENANT_ISOLATION_PREDICATE``), so the bound-scope
    # case is the one that proves the policy is doing the filtering.
    assert await _documents_visible_to(max(first.id, second.id) + 1000) == []


async def test_the_seed_parks_a_real_gate_in_every_inbox_scope(db):
    """A queue with nothing in it demos as a blank page (§7.1).

    Three scopes, three rows, and the attribution is what makes each scope non-empty:
    the tenant gates carry their tenant so the tenant admin can decide them, Aegis's
    own carries none so the platform operator can, and every one names the user whose
    run raised it so that user can see what became of it.
    """
    await seed()

    async with get_sessionmaker()() as session:
        rows = (await session.execute(select(Approval))).scalars().all()
        tenants = {t.name: t.id for t in (await session.execute(select(Tenant))).scalars().all()}
        clients = {
            u.tenant_id: u.id
            for u in (await session.execute(select(User).where(User.role == Role.CLIENT)))
            .scalars()
            .all()
        }

    assert len(rows) == len(TENANTS) + 1
    assert all(row.status is ApprovalStatus.PENDING for row in rows)
    # Every gate lists what approving would run — not only the representative.
    assert all(row.actions for row in rows)

    by_id = {row.id: row for row in rows}
    aegis_gate = by_id[PLATFORM_APPROVAL.approval_id]
    assert aegis_gate.tenant_id is None
    assert aegis_gate.requested_by == clients[None]

    for spec in TENANTS:
        gate = by_id[spec.approval.approval_id]
        assert gate.tenant_id == tenants[spec.name]
        assert gate.requested_by == clients[tenants[spec.name]]


async def test_seeded_accounts_log_in_at_the_tier_their_tenancy_implies(client, db):
    await seed()
    password = seed_password()

    platform = await client.post(
        "/auth/login", json={"username": "admin", "password": password}
    )
    assert platform.status_code == 200, platform.text
    assert platform.json()["tenant_id"] is None
    assert platform.json()["fine_role"] == PLATFORM_ADMIN

    tenant_admin = TENANTS[0].admin.username
    tenanted = await client.post(
        "/auth/login", json={"username": tenant_admin, "password": password}
    )
    assert tenanted.status_code == 200, tenanted.text
    body = tenanted.json()
    assert body["role"] == "admin"
    assert body["fine_role"] == TENANT_ADMIN
    assert body["tenant_id"] is not None
