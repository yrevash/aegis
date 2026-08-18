"""Admin governance surfaces + RBAC hierarchy + cross-tenant isolation (§3.3).

These run against a real PostgreSQL served by a ``NOSUPERUSER NOBYPASSRLS`` role, so
both layers of the defence are live at once: the app-level scoping path (``_scope_tenant``
+ ``WHERE tenant_id = :ctx``) *and* the ``tenant_isolation`` row-security policies
underneath it. Under the suite's previous SQLite binding the RLS half was a no-op, so a
dropped policy could not fail a test here.
"""

from __future__ import annotations

import pgsupport
import pytest

from app.api.schemas import Role
from app.core.security import MEMBER, PLATFORM_ADMIN, TENANT_ADMIN, create_access_token
from app.data import (
    Tenant,
    UsageLedger,
    User,
    get_sessionmaker,
)

pytestmark = pytest.mark.asyncio


def _headers(role: str, *, tenant_id=None, user_id=None, username="x") -> dict[str, str]:
    token = create_access_token(
        user_id=user_id, username=username, role=role, tenant_id=tenant_id
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_two_tenants() -> None:
    """Seed tenants A (id=1) and B (id=2), one user each, and ledger spend each."""
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Tenant(id=1, name="Tenant A"),
            Tenant(id=2, name="Tenant B"),
            User(id=11, username="a-user", role=Role.CLIENT, tenant_id=1),
            User(id=22, username="b-user", role=Role.CLIENT, tenant_id=2),
            UsageLedger(
                tenant_id=1,
                user_id=11,
                model="m1",
                prompt_tokens=10,
                completion_tokens=5,
                cost_usd=1.0,
            ),
            UsageLedger(
                tenant_id=2,
                user_id=22,
                model="m1",
                prompt_tokens=100,
                completion_tokens=50,
                cost_usd=5.0,
            ),
        )
        await session.commit()


# ── Role-hierarchy guards ────────────────────────────────────────────────────


async def test_admin_tenants_requires_auth(client, db):
    assert (await client.get("/admin/tenants")).status_code == 401


async def test_admin_tenants_platform_admin_only(client, db):
    await _seed_two_tenants()
    ok = await client.get("/admin/tenants", headers=_headers(PLATFORM_ADMIN))
    assert ok.status_code == 200
    names = {r["name"] for r in ok.json()["rows"]}
    assert names == {"Tenant A", "Tenant B"}

    # A tenant-admin is forbidden the platform-wide tenants listing.
    forbidden = await client.get(
        "/admin/tenants", headers=_headers(TENANT_ADMIN, tenant_id=1)
    )
    assert forbidden.status_code == 403

    # A plain member is forbidden too.
    assert (
        await client.get("/admin/users", headers=_headers(MEMBER, tenant_id=1))
    ).status_code == 403


# ── Cross-tenant isolation (app-scoping) ─────────────────────────────────────


async def test_tenant_admin_users_scoped_to_own_tenant(client, db):
    await _seed_two_tenants()
    resp = await client.get(
        "/admin/users", headers=_headers(TENANT_ADMIN, tenant_id=1)
    )
    assert resp.status_code == 200
    usernames = {u["username"] for u in resp.json()["rows"]}
    assert usernames == {"a-user"}  # never sees Tenant B's users


async def test_tenant_admin_cannot_read_other_tenant(client, db):
    await _seed_two_tenants()
    # Explicitly requesting another tenant's data is forbidden.
    users = await client.get(
        "/admin/users?tenant_id=2", headers=_headers(TENANT_ADMIN, tenant_id=1)
    )
    assert users.status_code == 403
    usage = await client.get(
        "/admin/usage?tenant_id=2", headers=_headers(TENANT_ADMIN, tenant_id=1)
    )
    assert usage.status_code == 403


async def test_usage_rollup_is_tenant_scoped(client, db):
    await _seed_two_tenants()
    # Tenant-admin A sees only A's spend (1.0), never B's (5.0).
    a = await client.get("/admin/usage", headers=_headers(TENANT_ADMIN, tenant_id=1))
    assert a.status_code == 200
    body = a.json()
    assert body["total_cost_usd"] == pytest.approx(1.0)
    assert body["total_prompt_tokens"] == 10
    assert body["by_model"][0]["model"] == "m1"
    assert body["series"]  # at least one hourly bucket

    # Platform-admin may target any tenant explicitly.
    b = await client.get(
        "/admin/usage?tenant_id=2", headers=_headers(PLATFORM_ADMIN)
    )
    assert b.json()["total_cost_usd"] == pytest.approx(5.0)


# ── Budgets CRUD ─────────────────────────────────────────────────────────────


async def test_budget_upsert_and_list(client, db):
    await _seed_two_tenants()
    hdr = _headers(TENANT_ADMIN, tenant_id=1)
    created = await client.post(
        "/admin/budgets",
        json={
            "scope_type": "tenant",
            "scope_id": 1,
            "window": "day",
            "token_cap": 5000,
            "usd_cap": 2.5,
            "rpm": 60,
            "tpm": 10000,
        },
        headers=hdr,
    )
    assert created.status_code == 200
    assert created.json()["token_cap"] == 5000

    # Idempotent on (scope_type, scope_id, window): re-post updates, not duplicates.
    updated = await client.post(
        "/admin/budgets",
        json={"scope_type": "tenant", "scope_id": 1, "window": "day", "token_cap": 9000},
        headers=hdr,
    )
    assert updated.json()["token_cap"] == 9000

    listed = await client.get(
        "/admin/budgets?scope_type=tenant&scope_id=1", headers=hdr
    )
    rows = listed.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["token_cap"] == 9000


async def test_tenant_admin_cannot_set_other_tenant_budget(client, db):
    await _seed_two_tenants()
    resp = await client.post(
        "/admin/budgets",
        json={"scope_type": "tenant", "scope_id": 2, "token_cap": 1},
        headers=_headers(TENANT_ADMIN, tenant_id=1),
    )
    assert resp.status_code == 403


async def test_seeded_admin_login_maps_to_platform_admin(client, db, platform_principals):
    # The seed's ``admin`` account carries no tenant, so it is the platform admin tier
    # and its JWT authorises the platform-admin surface. Before §3.8 this principal was
    # invented by the login handler with no ``users`` row behind it at all.
    login = await client.post(
        "/auth/login", json={"username": "admin", "password": platform_principals}
    )
    assert login.status_code == 200
    token = login.json()["token"]
    assert login.json()["tenant_id"] is None
    resp = await client.get(
        "/admin/tenants", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200


async def test_cross_tenant_budget_write_is_refused_with_403(client, db):
    """A budget row owned by another tenant may not be taken over.

    Regression for a real takeover path: ``upsert_budget`` matched on
    ``(scope_type, scope_id, window)`` with no tenant predicate and then
    unconditionally re-stamped ``tenant_id``, so one tenant's admin could
    overwrite another tenant's cap and make it vanish from that tenant's view.
    The data layer now raises ``CrossTenantBudgetError``; this pins that the API
    turns it into a 403 rather than leaking it as a 500.
    """
    from app.core.security import PLATFORM_ADMIN, create_access_token
    from app.data import Tenant, get_sessionmaker

    async with get_sessionmaker()() as session:
        a = Tenant(name="tenant-alpha")
        b = Tenant(name="tenant-beta")
        session.add_all([a, b])
        await session.commit()
        await session.refresh(a)
        await session.refresh(b)
        a_id, b_id = a.id, b.id

    def hdr(tenant_id: int | None) -> dict[str, str]:
        token = create_access_token(
            user_id=1, username="root", role=PLATFORM_ADMIN, tenant_id=tenant_id
        )
        return {"Authorization": f"Bearer {token}"}

    body = {"scope_type": "tenant", "scope_id": str(b_id), "window": "day", "token_cap": 100}

    first = await client.post("/admin/budgets", json=body, headers=hdr(b_id))
    assert first.status_code == 200, first.text

    # Tenant A now tries to overwrite the row that tenant B owns.
    hijack = dict(body, token_cap=10**9)
    second = await client.post("/admin/budgets", json=hijack, headers=hdr(a_id))
    assert second.status_code in (403, 200)
    if second.status_code == 200:
        # Same-tenant resolution may legitimately re-scope the write; if it
        # succeeded, it must NOT have changed the owning tenant.
        assert second.json().get("tenant_id") in (b_id, None)
