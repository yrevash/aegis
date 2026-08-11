"""The audit writer persists rows and pulls the tenant from context when omitted."""

from __future__ import annotations

from aegis.governance import (
    GovernanceContext,
    list_recent_audit,
    record_audit,
    reset_governance_context,
    set_governance_context,
)


async def test_record_and_list_recent_audit(db):
    await record_audit(
        action="tool:update_request_status",
        actor="alice",
        model="gpt",
        trace_id="t-1",
        payload={"request_id": "R1"},
        approved_by="bob",
        tenant_id=1,
    )
    rows = await list_recent_audit(tenant_id=1)
    assert len(rows) == 1
    assert rows[0].action == "tool:update_request_status"
    assert rows[0].actor == "alice"
    assert rows[0].approved_by == "bob"
    assert rows[0].ts  # serialised as a non-empty ISO 8601 UTC string


async def test_record_audit_pulls_tenant_from_context(db):
    token = set_governance_context(GovernanceContext(tenant_id=3, user_id=9))
    try:
        # tenant_id omitted → taken from the bound governance context.
        await record_audit(
            action="autonomous:act",
            actor="agent",
            model="gpt",
            trace_id=None,
            payload={},
        )
    finally:
        reset_governance_context(token)
    # The row is attributed to tenant 3, so a tenant-3-scoped read finds it…
    assert len(await list_recent_audit(tenant_id=3)) == 1
    # …and a different tenant's scoped read does not.
    assert await list_recent_audit(tenant_id=4) == []
