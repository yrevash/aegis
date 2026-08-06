"""Four-role RBAC: demo logins, per-role guards, role assignment, and /health.

Expands the coarse role model from {admin, user} to the four real roles
{admin, ai_team, devops, client} with honest backend enforcement (§3.3). The coarse
role is carried on the JWT's ``coarse_role`` claim, so the API reads the true role
directly rather than re-deriving a lossy admin/user pair.
"""

from __future__ import annotations

import pytest

from app.api import routes as api_routes
from app.api.routes import (
    AuthContext,
    require_ai_team,
    require_client,
    require_devops,
    require_roles,
)
from app.api.schemas import Role
from app.core.security import (
    PLATFORM_ADMIN,
    TENANT_ADMIN,
    create_access_token,
    decode_access_token,
)
from app.data import Tenant, User, get_sessionmaker
from app.main import app

pytestmark = pytest.mark.asyncio


def _headers(role: str, *, tenant_id=None, user_id=None, username="x") -> dict[str, str]:
    """Auth header for a principal minted from a *fine* role (coarse derived)."""
    token = create_access_token(
        user_id=user_id, username=username, role=role, tenant_id=tenant_id
    )
    return {"Authorization": f"Bearer {token}"}


def _ctx(role: Role) -> AuthContext:
    return AuthContext(username="u", role=role, persona="operations_lead")


# ── /health (public boot probe) ──────────────────────────────────────────────


async def test_health_is_public_and_reports_identity(client):
    # No auth, no DB fixture — the probe must answer even when auth/DB are down.
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["product"] and body["version"]


# ── Demo logins mint a correct four-valued-role JWT ──────────────────────────


@pytest.mark.parametrize(
    ("username", "expected_role"),
    [
        ("admin", "admin"),
        ("ai", "ai_team"),
        ("aiteam", "ai_team"),
        ("devops", "devops"),
        ("client", "client"),
    ],
)
async def test_demo_login_maps_username_to_role(client, db, username, expected_role):
    resp = await client.post(
        "/auth/login", json={"username": username, "password": "demo"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == expected_role
    # The token carries the true coarse role on its dedicated claim.
    claims = decode_access_token(body["token"])
    assert claims.coarse_role == expected_role


async def test_demo_login_rejects_wrong_password(client, db):
    bad = await client.post(
        "/auth/login", json={"username": "devops", "password": "nope"}
    )
    assert bad.status_code == 401


# ── Persona scoping: only the client role is barred from the ops persona ─────


async def test_client_cannot_use_operator_persona(client, db, make_deps):
    app.dependency_overrides[api_routes.get_agent_deps] = lambda: make_deps()
    resp = await client.post(
        "/query",
        json={"query": "hello", "persona": "operations_lead"},
        headers=_headers("client", user_id=5, tenant_id=1),
    )
    assert resp.status_code == 403
    app.dependency_overrides.clear()


@pytest.mark.parametrize("role", ["ai_team", "devops"])
async def test_operational_roles_may_use_operator_persona(client, db, make_deps, role):
    # ai_team/devops are full operational roles → the operations_lead persona is allowed.
    app.dependency_overrides[api_routes.get_agent_deps] = lambda: make_deps(
        propose_tool=False
    )
    resp = await client.post(
        "/query",
        json={"query": "what is the refund policy?", "persona": "operations_lead"},
        headers=_headers(role, user_id=7, tenant_id=1),
    )
    assert resp.status_code == 200
    app.dependency_overrides.clear()


# ── Per-role authz guards (unit-level, called directly with a principal) ─────


async def test_require_devops_admits_only_devops():
    assert require_devops(_ctx(Role.DEVOPS)).role is Role.DEVOPS
    for other in (Role.ADMIN, Role.AI_TEAM, Role.CLIENT):
        with pytest.raises(Exception) as ei:
            require_devops(_ctx(other))
        assert ei.value.status_code == 403


async def test_require_ai_team_admits_only_ai_team():
    assert require_ai_team(_ctx(Role.AI_TEAM)).role is Role.AI_TEAM
    for other in (Role.ADMIN, Role.DEVOPS, Role.CLIENT):
        with pytest.raises(Exception) as ei:
            require_ai_team(_ctx(other))
        assert ei.value.status_code == 403


async def test_require_client_admits_only_client():
    assert require_client(_ctx(Role.CLIENT)).role is Role.CLIENT
    for other in (Role.ADMIN, Role.AI_TEAM, Role.DEVOPS):
        with pytest.raises(Exception) as ei:
            require_client(_ctx(other))
        assert ei.value.status_code == 403


async def test_require_auth_rejects_inconsistent_coarse_and_fine(client, db):
    # FIX 4 defense-in-depth: a correctly-SIGNED token whose fine ``role`` (client) is
    # inconsistent with its ``coarse_role`` claim (admin) can only come from tampering or
    # a future mint-path bug — no real mint path produces it. require_auth must reject it
    # as 401 rather than trusting the elevated coarse claim.
    forged = create_access_token(
        user_id=1, username="mallory", role="client", coarse_role="admin", tenant_id=1
    )
    # A consistent client token is accepted (200) on the same auth-only endpoint; the
    # forged one is rejected (401) — proving the rejection is the inconsistency, not the
    # endpoint or the signature.
    ok = create_access_token(user_id=1, username="ok", role="client", tenant_id=1)
    good = await client.get("/metrics", headers={"Authorization": f"Bearer {ok}"})
    assert good.status_code == 200
    bad = await client.get("/metrics", headers={"Authorization": f"Bearer {forged}"})
    assert bad.status_code == 401


async def test_require_roles_admits_any_listed_role():
    dep = require_roles(Role.AI_TEAM, Role.ADMIN)
    assert dep(_ctx(Role.AI_TEAM)).role is Role.AI_TEAM
    assert dep(_ctx(Role.ADMIN)).role is Role.ADMIN
    for other in (Role.DEVOPS, Role.CLIENT):
        with pytest.raises(Exception) as ei:
            dep(_ctx(other))
        assert ei.value.status_code == 403


# ── Admin role-assignment endpoint ───────────────────────────────────────────


async def _seed_two_tenants() -> None:
    async with get_sessionmaker()() as session:
        session.add_all(
            [
                Tenant(id=1, name="Tenant A"),
                Tenant(id=2, name="Tenant B"),
                User(id=11, username="a-user", role=Role.CLIENT, tenant_id=1),
                User(id=22, username="b-user", role=Role.CLIENT, tenant_id=2),
            ]
        )
        await session.commit()


async def _role_of(user_id: int) -> Role:
    async with get_sessionmaker()() as session:
        user = await session.get(User, user_id)
        return user.role


async def test_platform_admin_can_reassign_any_user(client, db):
    await _seed_two_tenants()
    resp = await client.post(
        "/admin/users/11/role",
        json={"role": "devops"},
        headers=_headers(PLATFORM_ADMIN),
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "devops"
    # Change is durably persisted, not just echoed back.
    assert await _role_of(11) is Role.DEVOPS


async def test_tenant_admin_can_reassign_own_tenant_user(client, db):
    await _seed_two_tenants()
    resp = await client.post(
        "/admin/users/11/role",
        json={"role": "ai_team"},
        headers=_headers(TENANT_ADMIN, tenant_id=1),
    )
    assert resp.status_code == 200
    assert await _role_of(11) is Role.AI_TEAM


async def test_non_admin_cannot_reassign_roles(client, db):
    await _seed_two_tenants()
    # A client principal is forbidden the admin-only assignment surface.
    resp = await client.post(
        "/admin/users/11/role",
        json={"role": "admin"},
        headers=_headers("client", user_id=11, tenant_id=1),
    )
    assert resp.status_code == 403
    assert await _role_of(11) is Role.CLIENT  # unchanged


async def test_tenant_admin_cannot_reassign_other_tenant_user(client, db):
    await _seed_two_tenants()
    # Tenant A's admin must not touch a user who belongs to Tenant B.
    resp = await client.post(
        "/admin/users/22/role",
        json={"role": "devops"},
        headers=_headers(TENANT_ADMIN, tenant_id=1),
    )
    assert resp.status_code == 403
    assert await _role_of(22) is Role.CLIENT  # unchanged across the boundary


async def test_reassign_unknown_user_is_404(client, db):
    await _seed_two_tenants()
    resp = await client.post(
        "/admin/users/999/role",
        json={"role": "devops"},
        headers=_headers(PLATFORM_ADMIN),
    )
    assert resp.status_code == 404


async def test_cannot_demote_last_platform_admin(client, db):
    # A single global platform-admin exists; demoting them would lock the platform out.
    async with get_sessionmaker()() as session:
        session.add(User(id=1, username="root", role=Role.ADMIN, tenant_id=None))
        await session.commit()
    resp = await client.post(
        "/admin/users/1/role",
        json={"role": "client"},
        headers=_headers(PLATFORM_ADMIN),
    )
    assert resp.status_code == 409
    assert await _role_of(1) is Role.ADMIN  # still an admin — lockout refused
