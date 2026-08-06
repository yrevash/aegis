"""E2E (e): cross-tenant isolation via the app-scoping path (§3.3).

RLS is the enforced boundary on Postgres; on the SQLite test database these tests
exercise the belt-and-suspenders **app-level scoping** (``_scope_tenant`` + a
``WHERE tenant_id = :ctx`` filter) that backs it end-to-end through the HTTP surface:
a tenant-admin bound to tenant A can never read or write tenant B's users, usage, or
budgets, while a platform-admin may target any tenant.
"""

from __future__ import annotations

import pytest

from app.api.schemas import Role
from app.core.security import PLATFORM_ADMIN, TENANT_ADMIN, create_access_token
from app.data import Tenant, UsageLedger, User, get_sessionmaker

pytestmark = pytest.mark.asyncio


def _headers(role: str, *, tenant_id=None, user_id=None, username="admin") -> dict[str, str]:
    token = create_access_token(
        user_id=user_id, username=username, role=role, tenant_id=tenant_id
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_two_tenants() -> None:
    async with get_sessionmaker()() as session:
        session.add_all(
            [
                Tenant(id=1, name="Tenant A"),
                Tenant(id=2, name="Tenant B"),
                User(id=11, username="a-user", role=Role.CLIENT, tenant_id=1),
                User(id=22, username="b-user", role=Role.CLIENT, tenant_id=2),
                UsageLedger(tenant_id=1, user_id=11, model="m", cost_usd=1.0),
                UsageLedger(tenant_id=2, user_id=22, model="m", cost_usd=9.0),
            ]
        )
        await session.commit()


async def test_tenant_admin_blocked_from_other_tenant(client, db):
    await _seed_two_tenants()
    a_admin = _headers(TENANT_ADMIN, tenant_id=1)

    # Own tenant: scoped, allowed — sees only Tenant A's user.
    own = await client.get("/admin/users", headers=a_admin)
    assert own.status_code == 200
    assert {u["username"] for u in own.json()["rows"]} == {"a-user"}

    # Another tenant, explicitly requested: forbidden across every scoped surface.
    assert (await client.get("/admin/users?tenant_id=2", headers=a_admin)).status_code == 403
    assert (await client.get("/admin/usage?tenant_id=2", headers=a_admin)).status_code == 403
    cross_budget = await client.post(
        "/admin/budgets",
        json={"scope_type": "tenant", "scope_id": 2, "token_cap": 1},
        headers=a_admin,
    )
    assert cross_budget.status_code == 403

    # The tenant-admin is also denied the platform-wide tenants listing.
    assert (await client.get("/admin/tenants", headers=a_admin)).status_code == 403


async def test_usage_rollup_never_leaks_other_tenant_spend(client, db):
    await _seed_two_tenants()
    # Tenant-admin A's own rollup shows A's 1.0 only, never B's 9.0.
    a = await client.get("/admin/usage", headers=_headers(TENANT_ADMIN, tenant_id=1))
    assert a.status_code == 200
    assert a.json()["total_cost_usd"] == pytest.approx(1.0)

    # A platform-admin may target either tenant explicitly.
    b = await client.get("/admin/usage?tenant_id=2", headers=_headers(PLATFORM_ADMIN))
    assert b.json()["total_cost_usd"] == pytest.approx(9.0)
