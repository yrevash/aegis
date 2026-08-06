"""E2E (b): the durable async-approval round-trip through the HTTP surface.

A HIGH-risk action defers (D5). Because ``httpx.ASGITransport`` buffers the SSE body,
the live socket can't stay open for a human, so the run **parks** (a tiny park timeout):
the stream ends ``awaiting_approval`` after persisting a durable inbox row + a checkpoint.
An admin then lists the inbox (``GET /approvals``), approves out-of-band
(``POST /approvals/{id}/decision``), and the run resumes headless from its checkpoint —
executing the tool **exactly once**, idempotent under a replayed decision.
"""

from __future__ import annotations

import json

import pytest

from app.api import routes as api_routes
from app.main import app

pytestmark = pytest.mark.asyncio


async def test_durable_approval_inbox_roundtrip_executes_tool_once(
    client, db, admin_headers, make_deps, parse_sse
):
    executed: list[str] = []
    # Confident HIGH-risk action → defers to the gate (D5); park almost immediately.
    deps = make_deps(propose_tool=True, uncertain=False, high_risk=True)
    deps.config.approval_park_timeout = 0.05
    original_run_tool = deps.run_tool

    async def spy_run_tool(*args, **kwargs):  # noqa: ANN002, ANN003
        executed.append(args[1] if len(args) > 1 else "tool")
        return await original_run_tool(*args, **kwargs)

    deps.run_tool = spy_run_tool
    app.dependency_overrides[api_routes.get_agent_deps] = lambda: deps

    # 1. The gated run parks: the stream ends awaiting_approval with the inbox events.
    resp = await client.post(
        "/query",
        json={"query": "resolve request R1", "persona": "operations_lead"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    events = parse_sse(resp.text)
    types = [e["event"] for e in events]
    assert "approval_queued" in types  # durable inbox row persisted
    assert "approval_required" in types
    finished = json.loads(events[-1]["data"])
    assert finished["status"] == "awaiting_approval"
    assert executed == []  # parked: nothing executed yet

    queued = json.loads(next(e for e in events if e["event"] == "approval_queued")["data"])
    approval_id = queued["approval_id"]
    assert queued["sla_deadline"] is not None  # SLA deadline on the durable row

    # 2. The durable inbox lists the pending row for the admin (async surface).
    inbox = await client.get("/approvals", headers=admin_headers)
    assert inbox.status_code == 200
    rows = inbox.json()["rows"]
    assert any(r["id"] == approval_id and r["status"] == "pending" for r in rows)

    # 3. Approve out-of-band → resumes from the checkpoint, tool runs exactly once.
    decided = await client.post(
        f"/approvals/{approval_id}/decision",
        json={"decision": "approve"},
        headers=admin_headers,
    )
    assert decided.status_code == 200
    body = decided.json()
    assert body["accepted"] is True
    assert body["status"] == "approved"
    assert executed == ["update_request_status"]  # resumed and executed once

    # 4. Idempotency: a replayed decision is a no-op — no double execution.
    replay = await client.post(
        f"/approvals/{approval_id}/decision",
        json={"decision": "approve"},
        headers=admin_headers,
    )
    assert replay.json()["accepted"] is False
    assert executed == ["update_request_status"]

    # 5. The inbox is now empty (the row left PENDING) and the parked handle is gone
    #    (the successful resume consumed it).
    inbox_after = await client.get("/approvals", headers=admin_headers)
    assert all(r["id"] != approval_id for r in inbox_after.json()["rows"])
