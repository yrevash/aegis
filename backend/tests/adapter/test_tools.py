"""Tool tests — idempotency, reversibility and audit, with fakes only."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.adapter.schema import (
    Category,
    Channel,
    Priority,
    Region,
    RequestStatus,
    ServiceRequest,
)
from app.adapter.tools import (
    InMemoryRecordStore,
    ToolContext,
    add_case_note,
    assign_request,
    update_request_status,
)


def _seed_request() -> ServiceRequest:
    return ServiceRequest(
        id="req-1",
        title="t",
        description="d",
        category=Category.TECHNICAL,
        priority=Priority.HIGH,
        channel=Channel.CHAT,
        region=Region.NA,
        status=RequestStatus.NEW,
        customer_id="cust-1",
        assigned_agent_id="agent-1",
        created_at=datetime(2024, 1, 1),
        updated_at=datetime(2024, 1, 1),
        queue_depth_at_open=1,
        sla_hours=24.0,
    )


@pytest.fixture
def ctx(audit_sink):
    store = InMemoryRecordStore([_seed_request()])
    return ToolContext(store=store, actor="operations_lead", audit=audit_sink)


async def test_update_status_is_idempotent(ctx):
    args = {"request_id": "req-1", "status": "in_progress"}
    first = await update_request_status(args, ctx)
    second = await update_request_status(args, ctx)

    assert first.changed is True
    assert second.changed is False
    assert ctx.store.get_request("req-1").status is RequestStatus.IN_PROGRESS


async def test_update_status_is_reversible(ctx):
    result = await update_request_status(
        {"request_id": "req-1", "status": "resolved"}, ctx
    )
    assert result.inverse is not None
    # Applying the inverse restores the original status.
    await update_request_status(result.inverse.args, ctx)
    assert ctx.store.get_request("req-1").status is RequestStatus.NEW


async def test_assign_is_reversible(ctx):
    result = await assign_request({"request_id": "req-1", "agent_id": "agent-9"}, ctx)
    assert ctx.store.get_request("req-1").assigned_agent_id == "agent-9"
    await assign_request(result.inverse.args, ctx)
    assert ctx.store.get_request("req-1").assigned_agent_id == "agent-1"


async def test_add_note_idempotent_and_self_reversible(ctx):
    args = {"request_id": "req-1", "body": "checked logs", "author": "ops"}
    first = await add_case_note(args, ctx)
    second = await add_case_note(args, ctx)

    assert first.changed is True
    assert second.changed is False  # deterministic note id dedupes
    assert len(ctx.store.get_request("req-1").notes) == 1

    # The inverse retracts the note.
    await add_case_note(first.inverse.args, ctx)
    assert ctx.store.get_request("req-1").notes == []


async def test_tools_record_audit(ctx, audit_sink):
    await update_request_status({"request_id": "req-1", "status": "triaged"}, ctx)
    await assign_request({"request_id": "req-1", "agent_id": "agent-2"}, ctx)
    actions = [e["action"] for e in audit_sink.entries]
    assert "update_request_status" in actions
    assert "assign_request" in actions
    assert all(e["actor"] == "operations_lead" for e in audit_sink.entries)


async def test_missing_request_is_safe(ctx):
    result = await update_request_status({"request_id": "nope", "status": "closed"}, ctx)
    assert result.ok is False
    assert result.changed is False
