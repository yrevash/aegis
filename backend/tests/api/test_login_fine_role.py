"""``LoginResponse.fine_role`` — the admin sub-tier reaching the browser (§3.9).

``role`` is ``admin`` for both admin tiers, so before this the browser could not
tell a **platform** admin (global operator, every tenant) from a **tenant** admin
(pinned to one). Every per-tenant control in later phases turns on that
distinction, and the governance surfaces caption their own scope with it.

These are round trips over the real data layer, not shape checks: a user is
provisioned through ``POST /admin/users`` with an Argon2-hashed password and then
**logs in**, so the value asserted is the one a browser would actually receive.
The two cases differ only in tenancy — which is precisely what
:func:`aegis.governance.security.principal_role` derives the tier from.
"""

from __future__ import annotations

import pytest

from app.core.security import PLATFORM_ADMIN, TENANT_ADMIN, decode_access_token

pytestmark = pytest.mark.asyncio


async def _create_and_login(client, headers, *, username, role, tenant_id, password):
    """Provision a user, log in as them, and return the login response body."""
    created = await client.post(
        "/admin/users",
        json={
            "username": username,
            "role": role,
            "tenant_id": tenant_id,
            "password": password,
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    login = await client.post("/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()


async def test_login_of_a_platform_admin_carries_platform_admin(client, db, admin_headers):
    """An admin with no tenant is the global operator — ``platform_admin``."""
    body = await _create_and_login(
        client,
        admin_headers,
        username="global.operator",
        role="admin",
        tenant_id=None,
        password="platform-pw-1234",
    )
    assert body["role"] == "admin"
    assert body["tenant_id"] is None
    assert body["fine_role"] == PLATFORM_ADMIN == "platform_admin"


async def test_login_of_a_tenant_admin_carries_tenant_admin(client, db, admin_headers):
    """The same coarse role, scoped to a tenant, is a ``tenant_admin``.

    The pair is the whole point: identical ``role``, different authority. If the
    two responses agreed the browser would have nothing to branch on.
    """
    tenant = await client.post(
        "/admin/tenants", json={"name": "Northwind", "usd_cap": 50.0}, headers=admin_headers
    )
    assert tenant.status_code == 201, tenant.text
    tenant_id = tenant.json()["id"]

    body = await _create_and_login(
        client,
        admin_headers,
        username="northwind.admin",
        role="admin",
        tenant_id=tenant_id,
        password="tenant-pw-1234",
    )
    assert body["role"] == "admin"
    assert body["tenant_id"] == tenant_id
    assert body["fine_role"] == TENANT_ADMIN == "tenant_admin"


async def test_fine_role_on_the_wire_matches_the_token_it_was_issued_with(
    client, db, admin_headers
):
    """The response echoes the JWT's tier rather than deriving a second one.

    Two derivations are two chances to disagree, and the one the browser branches
    on would then not be the one the backend enforces.
    """
    body = await _create_and_login(
        client,
        admin_headers,
        username="echo.check",
        role="admin",
        tenant_id=None,
        password="echo-pw-12345678",
    )
    claims = decode_access_token(body["token"])
    assert claims.role == body["fine_role"]
    assert claims.coarse_role == body["role"]


async def test_login_of_a_non_admin_carries_its_own_role_as_the_tier(
    client, db, platform_principals
):
    """A non-admin has no sub-tier: its fine role is its own coarse role.

    Asserted so the field is never mistaken for admin-only and left unset (or set
    to an admin tier) for the roles that make up most of the userbase.
    """
    login = await client.post(
        "/auth/login", json={"username": "client", "password": platform_principals}
    )
    assert login.status_code == 200
    assert login.json()["fine_role"] == "client"

    devops = await client.post(
        "/auth/login", json={"username": "devops", "password": platform_principals}
    )
    assert devops.status_code == 200
    assert devops.json()["fine_role"] == "devops"


async def test_login_carries_the_user_id_the_memory_subject_is_authorised_against(
    client, db, admin_headers
):
    """``user_id`` is on the response, and it is the id the ``/memory/*`` check uses.

    Without it the browser's only route to its own identity was decoding the bearer's
    ``sub`` claim client-side, and a client-side re-derivation of a server-side fact
    disagrees eventually. When it does, the console sends ``user:<wrong>`` and the
    memory endpoints answer 403 — a refusal that reads as a broken rail rather than as
    the mismatch it is. Asserting it end to end (provision → log in → read own memory
    with the subject built from this field) is what pins the two together.
    """
    tenant = await client.post(
        "/admin/tenants", json={"name": "Subject Co", "usd_cap": 25.0}, headers=admin_headers
    )
    assert tenant.status_code == 201, tenant.text
    body = await _create_and_login(
        client,
        admin_headers,
        username="subject.owner",
        role="client",
        tenant_id=tenant.json()["id"],
        password="subject-pw-12345678",
    )
    assert isinstance(body["user_id"], int)

    own = await client.get(
        "/memory/facts",
        params={"subject": f"user:{body['user_id']}"},
        headers={"Authorization": f"Bearer {body['token']}"},
    )
    assert own.status_code == 200, own.text


async def test_the_login_user_id_is_the_tokens_subject(client, db, admin_headers):
    """The wire field and the JWT ``sub`` are one value, not two derivations of one.

    ``tenant_id`` is deliberately asserted alongside it: a platform principal carries
    **no tenant and a real user id**, and reading "no tenant" as "no user" is the exact
    conflation the sealed ``TenantScope`` type exists to make impossible. The two fields
    are independent and this pins that they stay so.
    """
    body = await _create_and_login(
        client,
        admin_headers,
        username="sub.echo",
        role="admin",
        tenant_id=None,
        password="sub-echo-pw-1234",
    )
    claims = decode_access_token(body["token"])
    assert body["tenant_id"] is None
    assert body["user_id"] is not None
    assert claims.user_id == body["user_id"]
