"""Admin governance surfaces + RBAC hierarchy + cross-tenant isolation (§3.3).

RLS is Postgres-only; on SQLite these tests exercise the app-level scoping path
(``_scope_tenant`` + ``WHERE tenant_id = :ctx``) that backs it.
"""

from __future__ import annotations

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
        session.add_all(
            [
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
            ]
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


async def test_demo_admin_login_still_works_and_maps_to_platform_admin(client, db):
    # Back-compat: the built-in demo admin still logs in (no users row needed) and
    # its JWT authorises the platform-admin surface.
    login = await client.post(
        "/auth/login", json={"username": "admin", "password": "demo"}
    )
    assert login.status_code == 200
    token = login.json()["token"]
    assert login.json()["tenant_id"] is None
    resp = await client.get(
        "/admin/tenants", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
