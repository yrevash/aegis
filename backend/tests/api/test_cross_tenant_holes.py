"""Cross-tenant isolation regressions closed by the security audit (§3.3).

Each test here FAILS on the pre-fix code and passes after the fix:

- C1 — approvals inbox + decision paths were only ``require_admin`` and never
  checked the approval's tenant, so a tenant-admin could read and decide on another
  tenant's gates.
- H2 — the audit trail had no tenant column/scoping, so any admin saw every
  tenant's actions.
- H3 — ``POST /admin/budgets`` only guarded the ``tenant`` scope, letting a
  tenant-admin cap a *user* in another tenant.
- M1 — ``GET /admin/budgets`` was unscoped.
- M2 — ``GET /metrics`` was system-wide to any admin.

RLS is the enforced boundary on Postgres; on the SQLite test DB these exercise the
belt-and-suspenders app-level scoping that backs it.
"""

from __future__ import annotations

import pytest

from app.api.schemas import RiskLevel, Role
from app.core.security import PLATFORM_ADMIN, TENANT_ADMIN, create_access_token
from app.data import (
    Tenant,
    User,
    enqueue_approval,
    get_sessionmaker,
    record_audit,
    upsert_budget,
)

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
                User(id=11, username="a-user", role=Role.USER, tenant_id=1),
                User(id=22, username="b-user", role=Role.USER, tenant_id=2),
            ]
        )
        await session.commit()


# ── C1: cross-tenant approvals ───────────────────────────────────────────────


async def _seed_two_approvals() -> None:
    await enqueue_approval(
        approval_id="ap-a", run_id="run-a", action="x", risk=RiskLevel.HIGH, tenant_id=1
    )
    await enqueue_approval(
        approval_id="ap-b", run_id="run-b", action="x", risk=RiskLevel.HIGH, tenant_id=2
    )


async def test_inbox_is_tenant_scoped(client, db):
    await _seed_two_approvals()
    a = await client.get("/approvals", headers=_headers(TENANT_ADMIN, tenant_id=1))
    assert a.status_code == 200
    ids = {r["id"] for r in a.json()["rows"]}
    assert ids == {"ap-a"}  # never sees Tenant B's gate

    # A platform-admin sees every tenant's pending gates.
    p = await client.get("/approvals", headers=_headers(PLATFORM_ADMIN))
    assert {r["id"] for r in p.json()["rows"]} == {"ap-a", "ap-b"}


async def test_tenant_admin_cannot_decide_other_tenant_approval(client, db):
    await _seed_two_approvals()
    a_admin = _headers(TENANT_ADMIN, tenant_id=1)

    # Decision endpoint: another tenant's gate is forbidden BEFORE any resolve.
    forbidden = await client.post(
        "/approvals/ap-b/decision", json={"decision": "approve"}, headers=a_admin
    )
    assert forbidden.status_code == 403

    # Live gate endpoint: same enforcement.
    forbidden_live = await client.post(
        "/approval", json={"approval_id": "ap-b", "decision": "approve"}, headers=a_admin
    )
    assert forbidden_live.status_code == 403

    # The row is untouched (still pending) — the cross-tenant call never resolved it.
    p = await client.get("/approvals", headers=_headers(PLATFORM_ADMIN))
    assert any(r["id"] == "ap-b" and r["status"] == "pending" for r in p.json()["rows"])


async def test_owner_and_platform_admin_can_decide(client, db):
    await _seed_two_approvals()
    # Owning tenant-admin resolves its own gate.
    own = await client.post(
        "/approvals/ap-a/decision",
        json={"decision": "approve"},
        headers=_headers(TENANT_ADMIN, tenant_id=1),
    )
    assert own.status_code == 200 and own.json()["accepted"] is True

    # Platform-admin resolves another tenant's gate.
    cross = await client.post(
        "/approvals/ap-b/decision",
        json={"decision": "reject"},
        headers=_headers(PLATFORM_ADMIN),
    )
    assert cross.status_code == 200 and cross.json()["accepted"] is True


async def test_unknown_approval_still_idempotent_noop(client, db):
    # An unknown id must not 403 — it flows to the idempotent no-op (accepted False).
    resp = await client.post(
        "/approvals/missing/decision",
        json={"decision": "approve"},
        headers=_headers(TENANT_ADMIN, tenant_id=1),
    )
    assert resp.status_code == 200 and resp.json()["accepted"] is False


# ── H2: audit-log tenant scoping ─────────────────────────────────────────────


async def test_audit_is_tenant_scoped(client, db):
    await record_audit(
        action="tool.a", actor="a", model=None, trace_id=None, payload={}, tenant_id=1
    )
    await record_audit(
        action="tool.b", actor="b", model=None, trace_id=None, payload={}, tenant_id=2
    )
    a = await client.get("/audit", headers=_headers(TENANT_ADMIN, tenant_id=1))
    assert a.status_code == 200
    actions = {r["action"] for r in a.json()["rows"]}
    assert actions == {"tool.a"}  # never sees Tenant B's action

    # Platform-admin sees the whole trail.
    p = await client.get("/audit", headers=_headers(PLATFORM_ADMIN))
    p_actions = {r["action"] for r in p.json()["rows"]}
    assert {"tool.a", "tool.b"} <= p_actions


# ── H3: cross-tenant user-budget write ───────────────────────────────────────


async def test_tenant_admin_cannot_cap_user_in_other_tenant(client, db):
    await _seed_two_tenants()
    # user 22 belongs to Tenant B; Tenant A's admin must not cap them.
    forbidden = await client.post(
        "/admin/budgets",
        json={"scope_type": "user", "scope_id": 22, "token_cap": 1},
        headers=_headers(TENANT_ADMIN, tenant_id=1),
    )
    assert forbidden.status_code == 403

    # Own-tenant user: allowed.
    ok = await client.post(
        "/admin/budgets",
        json={"scope_type": "user", "scope_id": 11, "token_cap": 5},
        headers=_headers(TENANT_ADMIN, tenant_id=1),
    )
    assert ok.status_code == 200

    # Unknown user: 404, not a silent cross-tenant write.
    missing = await client.post(
        "/admin/budgets",
        json={"scope_type": "user", "scope_id": 999, "token_cap": 5},
        headers=_headers(TENANT_ADMIN, tenant_id=1),
    )
    assert missing.status_code == 404


# ── M1: budget listing tenant scoping ────────────────────────────────────────


async def test_budget_listing_is_tenant_scoped(client, db):
    await _seed_two_tenants()
    await upsert_budget(scope_type="tenant", scope_id=1, token_cap=10, tenant_id=1)
    await upsert_budget(scope_type="tenant", scope_id=2, token_cap=20, tenant_id=2)

    a = await client.get("/admin/budgets", headers=_headers(TENANT_ADMIN, tenant_id=1))
    assert a.status_code == 200
    scope_ids = {r["scope_id"] for r in a.json()["rows"]}
    assert scope_ids == {1}  # never sees Tenant B's cap

    p = await client.get("/admin/budgets", headers=_headers(PLATFORM_ADMIN))
    assert {r["scope_id"] for r in p.json()["rows"]} == {1, 2}


# ── M2: /metrics restricted to platform-admin ────────────────────────────────


async def test_metrics_platform_admin_only(client, db):
    forbidden = await client.get("/metrics", headers=_headers(TENANT_ADMIN, tenant_id=1))
    assert forbidden.status_code == 403

    ok = await client.get("/metrics", headers=_headers(PLATFORM_ADMIN))
    assert ok.status_code == 200
