"""Admin CRUD write-surfaces — create tenant + create user (Phase-3 · functional admin).

These assert the two new admin mutations end-to-end over the real data layer:

- ``POST /admin/tenants`` creates a client (platform-admin only; duplicate → 409);
- ``POST /admin/users`` provisions a user with a hashed password (duplicate → 409;
  a tenant-admin cannot create outside its own tenant), and — the end-to-end proof —
  the created user can then **log in** with the password it was given, so the
  Argon2 hashing path is real, not a stub.
"""

from __future__ import annotations

import pytest

from app.core.security import PLATFORM_ADMIN, TENANT_ADMIN, create_access_token

pytestmark = pytest.mark.asyncio


def _headers(role: str, *, tenant_id=None, user_id=1, username="op") -> dict[str, str]:
    token = create_access_token(
        user_id=user_id, username=username, role=role, tenant_id=tenant_id
    )
    return {"Authorization": f"Bearer {token}"}


# ── Tenants ──────────────────────────────────────────────────────────────────


async def test_create_tenant_then_lists(client, db, admin_headers):
    r = await client.post("/admin/tenants", json={"name": "Initech"}, headers=admin_headers)
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Initech"
    assert body["status"] == "active"
    # It now shows up in the tenant listing.
    listing = await client.get("/admin/tenants", headers=admin_headers)
    assert "Initech" in {t["name"] for t in listing.json()["rows"]}


async def test_create_tenant_duplicate_conflicts(client, db, admin_headers):
    await client.post("/admin/tenants", json={"name": "Umbrella"}, headers=admin_headers)
    dupe = await client.post("/admin/tenants", json={"name": "Umbrella"}, headers=admin_headers)
    assert dupe.status_code == 409


async def test_create_tenant_rejects_non_platform_admin(client, db):
    # A tenant-admin is not a platform-admin — tenant creation is platform-only.
    r = await client.post(
        "/admin/tenants", json={"name": "Nope"}, headers=_headers(TENANT_ADMIN, tenant_id=1)
    )
    assert r.status_code == 403


# ── Users ────────────────────────────────────────────────────────────────────


async def test_create_user_then_lists(client, db, admin_headers):
    # Create the owning tenant first, then a user inside it.
    t = await client.post("/admin/tenants", json={"name": "Acme"}, headers=admin_headers)
    tid = t.json()["id"]
    r = await client.post(
        "/admin/users",
        json={"username": "acme.dev", "role": "devops", "tenant_id": tid, "password": "s3cret-pw"},
        headers=admin_headers,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["username"] == "acme.dev"
    assert body["role"] == "devops"
    assert body["tenant_id"] == tid
    listing = await client.get("/admin/users", headers=admin_headers)
    assert "acme.dev" in {u["username"] for u in listing.json()["rows"]}


async def test_created_user_can_log_in(client, db, admin_headers):
    """The end-to-end proof: a created user authenticates with its real password."""
    await client.post(
        "/admin/users",
        json={"username": "real.person", "role": "client", "tenant_id": None, "password": "hunter2-strong"},
        headers=admin_headers,
    )
    login = await client.post(
        "/auth/login", json={"username": "real.person", "password": "hunter2-strong"}
    )
    assert login.status_code == 200
    assert login.json()["role"] == "client"
    # A wrong password is rejected (the hash is verified, not bypassed).
    bad = await client.post(
        "/auth/login", json={"username": "real.person", "password": "wrong"}
    )
    assert bad.status_code == 401


async def test_create_user_duplicate_conflicts(client, db, admin_headers):
    body = {"username": "dupe.user", "role": "client", "tenant_id": None, "password": "pw-abcdefgh"}
    await client.post("/admin/users", json=body, headers=admin_headers)
    dupe = await client.post("/admin/users", json=body, headers=admin_headers)
    assert dupe.status_code == 409


async def test_create_user_tenant_admin_cross_tenant_forbidden(client, db):
    # A tenant-admin scoped to tenant 1 cannot create a user in tenant 2.
    r = await client.post(
        "/admin/users",
        json={"username": "sneaky", "role": "client", "tenant_id": 2, "password": "pw-abcdefgh"},
        headers=_headers(TENANT_ADMIN, tenant_id=1),
    )
    assert r.status_code == 403


async def test_create_user_rejects_client_role(client, db):
    # A plain client principal has no admin surface at all.
    r = await client.post(
        "/admin/users",
        json={"username": "x", "role": "client", "password": "pw-abcdefgh"},
        headers=_headers(PLATFORM_ADMIN),  # sanity: platform-admin allowed…
    )
    assert r.status_code == 201
    r2 = await client.post(
        "/admin/users",
        json={"username": "y", "role": "client", "password": "pw-abcdefgh"},
        headers=_headers("client", tenant_id=1),  # …a client is refused.
    )
    assert r2.status_code == 403
