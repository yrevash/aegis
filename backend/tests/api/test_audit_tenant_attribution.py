"""The audit trail belongs to a tenant, and its admin can read it — audit C, C4.

``_safe_audit`` took an actor string and no tenant, and ``record_audit``'s fallback reads
a per-request governance context these HTTP paths never set. So every row this API wrote
landed with ``audit_log.tenant_id = NULL``. Measured on the cold demo path: eleven events
recorded, every one NULL; ``GET /audit`` — which scopes a tenant-admin's read with
``WHERE tenant_id = :tenant`` — returned **zero rows** for both tenant admins, two lines
below a payload that carried ``"tenant_id": 1``.

Governance is a scored area, and a trail its own subject cannot see is not one.
"""

from __future__ import annotations

import pgsupport
import pytest
from aegis.governance.models import AuditLog, Budget, BudgetScope, BudgetWindow
from sqlalchemy import select

from app.api.schemas import Role
from app.core.security import PLATFORM_ADMIN, TENANT_ADMIN, create_access_token
from app.data import Tenant, User, get_sessionmaker

pytestmark = pytest.mark.asyncio

_TENANT = 9301
_OTHER = 9302
_ADMIN = 93011
_OTHER_ADMIN = 93021
_PLATFORM = 93099


async def _seed() -> None:
    """Two tenants, an admin each, and an un-tenanted platform admin."""
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Tenant(id=_TENANT, name="Audit tenant A"),
            Tenant(id=_OTHER, name="Audit tenant B"),
            User(id=_ADMIN, username="audit-a-admin", role=Role.ADMIN, tenant_id=_TENANT),
            User(id=_OTHER_ADMIN, username="audit-b-admin", role=Role.ADMIN, tenant_id=_OTHER),
            User(id=_PLATFORM, username="audit-platform", role=Role.ADMIN, tenant_id=None),
            Budget(
                tenant_id=_TENANT,
                scope_type=BudgetScope.TENANT,
                scope_id=_TENANT,
                window=BudgetWindow.DAY,
                usd_cap=100.0,
            ),
        )
        await session.commit()


def _headers(*, user_id: int, username: str, fine_role: str, tenant_id: int | None):
    return {
        "Authorization": "Bearer "
        + create_access_token(
            user_id=user_id, username=username, role=fine_role, tenant_id=tenant_id
        )
    }


def _tenant_a_admin():
    return _headers(
        user_id=_ADMIN, username="audit-a-admin", fine_role=TENANT_ADMIN, tenant_id=_TENANT
    )


def _tenant_b_admin():
    return _headers(
        user_id=_OTHER_ADMIN, username="audit-b-admin", fine_role=TENANT_ADMIN, tenant_id=_OTHER
    )


def _platform_admin():
    return _headers(
        user_id=_PLATFORM, username="audit-platform", fine_role=PLATFORM_ADMIN, tenant_id=None
    )


async def test_a_tenants_action_is_attributed_to_that_tenant(db, client):
    """An action by a tenant admin writes a row that tenant admin can read back.

    The end-to-end shape of the defect: the write happened, the read was empty.
    """
    await _seed()

    posted = await client.post(
        "/ml/explain",
        json={"features": {"amount": 12.0}},
        headers=_tenant_a_admin(),
    )
    assert posted.status_code in (200, 503), posted.text  # the audit is written either way

    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(select(AuditLog).where(AuditLog.action == "ml.explain"))
        ).scalars().all()
    assert rows, "the action must be recorded at all"
    assert all(row.tenant_id == _TENANT for row in rows), (
        "every recorded event landed with tenant_id NULL, so no tenant owned its own trail"
    )

    seen = await client.get("/audit", headers=_tenant_a_admin())
    assert seen.status_code == 200
    actions = [row["action"] for row in seen.json()["rows"]]
    assert "ml.explain" in actions, "a tenant admin must see its own tenant's trail"


async def test_the_trail_is_not_shared_across_tenants(db, client):
    """Attributing the row must not make it readable by the wrong tenant."""
    await _seed()

    await client.post(
        "/ml/explain", json={"features": {"amount": 12.0}}, headers=_tenant_a_admin()
    )

    other = await client.get("/audit", headers=_tenant_b_admin())
    assert other.status_code == 200
    assert [r for r in other.json()["rows"] if r["action"] == "ml.explain"] == [], (
        "tenant B must not see tenant A's audit events"
    )


async def test_platform_admin_still_sees_everything(db, client):
    """The platform-wide view must keep working — scoping is not a new blind spot."""
    await _seed()

    await client.post(
        "/ml/explain", json={"features": {"amount": 12.0}}, headers=_tenant_a_admin()
    )

    whole = await client.get("/audit", headers=_platform_admin())
    assert whole.status_code == 200
    assert "ml.explain" in [row["action"] for row in whole.json()["rows"]]


async def test_an_action_on_another_tenant_belongs_to_that_tenant(db, client):
    """A platform admin provisioning *into* a tenant writes that tenant's row.

    The row belongs to whoever has to be able to read it. A platform admin creating a
    user inside tenant A is an event tenant A's own admin is answerable for, and filing
    it under the un-tenanted actor would hide it from the only person it concerns.
    """
    await _seed()

    created = await client.post(
        "/admin/users",
        json={
            "username": "provisioned-by-platform",
            "password": "correct-horse-battery-staple",
            "role": "client",
            "tenant_id": _TENANT,
        },
        headers=_platform_admin(),
    )
    assert created.status_code == 201, created.text

    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "admin.user.create")
            )
        ).scalars().all()
    assert rows and all(row.tenant_id == _TENANT for row in rows)

    seen = await client.get("/audit", headers=_tenant_a_admin())
    assert "admin.user.create" in [row["action"] for row in seen.json()["rows"]]
