"""Allowlist enforcement tests."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.adapter.personas import get_persona
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
    ToolNotAllowedError,
    UnknownToolError,
    is_allowed,
    run_tool,
    tools_for,
)


def _seed_request() -> ServiceRequest:
    return ServiceRequest(
        id="req-1",
        title="t",
        description="d",
        category=Category.BILLING,
        priority=Priority.LOW,
        channel=Channel.PORTAL,
        region=Region.EU,
        status=RequestStatus.NEW,
        customer_id="cust-1",
        created_at=datetime(2024, 1, 1),
        updated_at=datetime(2024, 1, 1),
        queue_depth_at_open=0,
        sla_hours=72.0,
    )


@pytest.fixture
def ctx(audit_sink):
    return ToolContext(
        store=InMemoryRecordStore([_seed_request()]), actor="client", audit=audit_sink
    )


async def test_client_cannot_run_admin_tool(ctx, audit_sink):
    with pytest.raises(ToolNotAllowedError):
        await run_tool(
            "client", "update_request_status", {"request_id": "req-1", "status": "closed"}, ctx
        )
    # Denied before any side effect: state untouched and nothing audited.
    assert ctx.store.get_request("req-1").status is RequestStatus.NEW
    assert audit_sink.entries == []


async def test_client_may_add_note(ctx):
    result = await run_tool(
        "client", "add_case_note", {"request_id": "req-1", "body": "please expedite"}, ctx
    )
    assert result.ok is True
    assert result.changed is True


async def test_admin_may_run_admin_tool(audit_sink):
    ctx = ToolContext(
        store=InMemoryRecordStore([_seed_request()]), actor="operations_lead", audit=audit_sink
    )
    result = await run_tool(
        "operations_lead",
        "update_request_status",
        {"request_id": "req-1", "status": "triaged"},
        ctx,
    )
    assert result.ok is True


async def test_unknown_tool_raises(ctx):
    with pytest.raises(UnknownToolError):
        await run_tool("operations_lead", "delete_everything", {}, ctx)


def test_is_allowed_matrix():
    assert is_allowed("operations_lead", "assign_request") is True
    assert is_allowed("client", "assign_request") is False
    assert is_allowed("client", "add_case_note") is True
    assert is_allowed("nobody", "add_case_note") is False


def test_tools_for_matches_persona_scope():
    admin_tools = {t.name for t in tools_for("operations_lead")}
    client_tools = {t.name for t in tools_for("client")}
    assert client_tools == {"add_case_note"}
    assert admin_tools >= client_tools
    # Persona.tool_names is the same single source of truth as the allowlist.
    assert set(get_persona("client").tool_names) == client_tools
