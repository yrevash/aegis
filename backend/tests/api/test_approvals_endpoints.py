"""HTTP tests for the durable approvals inbox endpoints (admin-scoped).

Pins the exact wire shapes the frontend codes to:

- ``GET  /approvals?status=pending&limit=50`` → ``{ rows: ApprovalRow[] }``
- ``POST /approvals/{id}/decision`` body ``{ decision }`` → ``{ id, status, accepted }``

The decision path is idempotent (the optimistic transition), which is asserted here
against a directly-enqueued row (the full resume-and-execute flow lives in the
orchestrator tests).
"""

from __future__ import annotations

import pytest

from app.api.schemas import RiskLevel
from app.data import enqueue_approval

pytestmark = pytest.mark.asyncio


async def test_inbox_lists_pending_rows(client, db, admin_headers):
    await enqueue_approval(
        approval_id="ap-http-1",
        run_id="run-http-1",
        action="update_request_status",
        args={"request_id": "R1"},
        risk=RiskLevel.HIGH,
        rationale="wide interval",
        ml_snapshot={"prediction": 12.0},
        persona="operations_lead",
    )
    resp = await client.get("/approvals?status=pending&limit=50", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "rows" in body
    row = next(r for r in body["rows"] if r["id"] == "ap-http-1")
    # Exact ApprovalRow contract.
    assert set(row) >= {
        "id", "run_id", "action", "args", "risk", "rationale", "status",
        "persona", "sla_deadline", "created_at", "ml_snapshot",
    }
    assert row["status"] == "pending"
    assert row["risk"] == "high"
    assert row["persona"] == "operations_lead"
    assert row["ml_snapshot"] == {"prediction": 12.0}


async def test_inbox_requires_admin(client, db, user_headers):
    resp = await client.get("/approvals", headers=user_headers)
    assert resp.status_code == 403


async def test_decision_endpoint_shape_and_idempotency(client, db, admin_headers):
    await enqueue_approval(
        approval_id="ap-http-2", run_id="run-http-2", action="x", risk=RiskLevel.HIGH
    )
    first = await client.post(
        "/approvals/ap-http-2/decision",
        json={"decision": "approve"},
        headers=admin_headers,
    )
    assert first.status_code == 200
    body = first.json()
    assert set(body) == {"id", "status", "accepted"}
    assert body["id"] == "ap-http-2"
    assert body["accepted"] is True

    # Idempotent: a replayed decision is a no-op.
    second = await client.post(
        "/approvals/ap-http-2/decision",
        json={"decision": "approve"},
        headers=admin_headers,
    )
    assert second.status_code == 200
    assert second.json()["accepted"] is False


async def test_decision_requires_admin(client, db, user_headers):
    resp = await client.post(
        "/approvals/whatever/decision",
        json={"decision": "approve"},
        headers=user_headers,
    )
    assert resp.status_code == 403


async def test_decision_unknown_id_not_accepted(client, db, admin_headers):
    resp = await client.post(
        "/approvals/missing/decision",
        json={"decision": "approve"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["accepted"] is False
