"""Does a tenant's ``agent.gate_min_risk`` actually gate, end to end?

The whole defect was that it did not. The key was in the catalogue, ``TIGHTEN_ONLY``,
writable by a tenant admin and renderable on a screen — and
:class:`~app.agent.deps.AgentConfig` carried a hardcoded ``RiskLevel.HIGH`` that the
composition root never overrode, because the config is built once and *synchronously*
while settings resolution is per tenant and *async*. So a tenant admin who asked for
**more** oversight over their own agents got exactly nothing.

This test is deliberately the whole vertical: a real settings row written through the
real resolver, the real governance context a request binds, the real
:func:`app.agent.run_agent`, and an assertion on **the gate's behaviour** — the same
MEDIUM-risk tool executing unattended before the tenant tightened, and pausing for a
human afterwards. Asserting that ``deps.config.gate_min_risk`` holds a value would pass
just as happily against a floor nothing reads, which is the bug.
"""

from __future__ import annotations

import pgsupport
import pytest
from aegis.governance.context import (
    GovernanceContext,
    reset_governance_context,
    set_governance_context,
)
from aegis.governance.rls import set_tenant_scope
from aegis.settings import SettingScope, write_setting

from app.agent import ApprovalRegistry, run_agent
from app.api.schemas import ApprovalDecision, Role
from app.data import Tenant, User, get_sessionmaker

pytestmark = pytest.mark.asyncio

_TENANT = 7811
_USER = 78111


async def _seed_tenant() -> None:
    """One tenant with one admin — the FKs a tenant-scoped settings row needs."""
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Tenant(id=_TENANT, name="Gate floor tenant"),
            User(id=_USER, username="gate-floor-admin", role=Role.ADMIN, tenant_id=_TENANT),
        )
        await session.commit()


async def _tenant_admin_sets_gate(value: str) -> None:
    """Tighten ``agent.gate_min_risk`` the way the tenant admin's request would."""
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, _TENANT)
        await write_setting(
            session,
            "agent.gate_min_risk",
            value,
            scope=SettingScope.TENANT,
            actor_role="tenant_admin",
            tenant_id=_TENANT,
            actor_user_id=_USER,
        )
        await session.commit()


async def _drive_as_tenant(deps) -> list[str]:  # noqa: ANN001 - AgentDeps
    """Run one query as this tenant, auto-approving any gate; return the event types."""
    registry = ApprovalRegistry()
    token = set_governance_context(
        GovernanceContext(tenant_id=_TENANT, user_id=_USER, role=Role.ADMIN)
    )
    types: list[str] = []
    try:
        async for event in run_agent(
            "Please resolve request R1",
            persona="operations_lead",
            role="admin",
            deps=deps,
            registry=registry,
        ):
            types.append(event.type)
            if event.type == "approval_required":
                registry.resolve(
                    event.approval_id, ApprovalDecision.APPROVE, approver="alice"
                )
    finally:
        reset_governance_context(token)
    return types


async def test_a_tenants_medium_floor_gates_a_medium_risk_tool(db, make_deps):
    """The same run, the same MEDIUM-risk tool: unattended, then gated.

    The control half matters as much as the claim. Without it, a test that only asserted
    the pause could pass on a graph that gates everything, and would say nothing about
    whether the tenant's setting was what caused it.
    """
    await _seed_tenant()
    deps = make_deps(propose_tool=True, high_risk=False)

    # The platform floor is HIGH and stays HIGH: what deserves a gate is the tenant's
    # call, so a MEDIUM-risk tool runs unattended until somebody says otherwise.
    before = await _drive_as_tenant(deps)
    assert "tool_call" in before
    assert "approval_required" not in before

    # The tenant admin asks for more oversight over their own agents.
    await _tenant_admin_sets_gate("medium")

    after = await _drive_as_tenant(deps)
    assert "approval_required" in after, (
        "the tenant tightened agent.gate_min_risk to MEDIUM and the same MEDIUM-risk "
        "tool still ran unattended — the setting is writable, displayable and binds "
        "nothing"
    )
    assert after.index("approval_required") < after.index("tool_call")
