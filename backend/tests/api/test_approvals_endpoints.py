"""HTTP tests for the durable approvals inbox endpoints.

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


async def test_inbox_shows_a_non_admin_only_the_gates_they_raised(client, db):
    """A client sees the fate of its own gates, and of no others (§7.1).

    This replaces the flat 403 the inbox used to answer a non-admin with. That refusal
    was itself the defect: a user whose run trips the HIGH-risk gate had no screen that
    told them what happened to it. What matters is not that they can read *something*
    but that reading is scoped to the gates they raised — so this asserts both halves,
    and asserts the row comes back with the decision withheld and explained.
    """
    from app.seed import SeedSummary, ensure_principal, platform_principal, seed_password

    await ensure_principal(platform_principal("client"), SeedSummary())
    login = await client.post(
        "/auth/login", json={"username": "client", "password": seed_password()}
    )
    assert login.status_code == 200, login.text
    body = login.json()
    headers = {"Authorization": f"Bearer {body['token']}"}
    me = body["user_id"]
    assert me is not None

    await enqueue_approval(
        approval_id="ap-mine",
        run_id="run-mine",
        action="issue_credit",
        risk=RiskLevel.HIGH,
        requested_by=me,
    )
    await enqueue_approval(
        approval_id="ap-someone-elses",
        run_id="run-theirs",
        action="issue_credit",
        risk=RiskLevel.HIGH,
        requested_by=me + 1000,
    )

    resp = await client.get("/approvals?status=all", headers=headers)
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert {r["id"] for r in rows} == {"ap-mine"}
    assert rows[0]["decidable"] is False
    assert rows[0]["blocked_reason"]


async def test_inbox_forbids_a_non_admin_filtering_by_tenant(client, db, user_headers):
    """A non-admin cannot widen its scope with a query parameter."""
    resp = await client.get("/approvals?tenant_id=1", headers=user_headers)
    assert resp.status_code == 403


async def test_inbox_rejects_an_unknown_status_word(client, db, admin_headers):
    """A typo is a 400 naming the accepted words, never a silently empty queue."""
    resp = await client.get("/approvals?status=pendign", headers=admin_headers)
    assert resp.status_code == 400
    assert "pending" in resp.json()["detail"]


async def test_inbox_carries_every_call_the_gate_authorises(client, db, admin_headers):
    """A gate that authorises three calls lists three in the inbox, not one.

    ``action`` is only the representative — the highest-risk call. The durable row
    stored nothing else, so the inbox asked a person to authorise a fan-out while
    naming one of its writes.
    """
    await enqueue_approval(
        approval_id="ap-fanout",
        run_id="run-fanout",
        action="issue_credit",
        args={"amount": 1},
        actions=[
            {"id": "c1", "name": "issue_credit", "args": {"amount": 1}, "risk": "high"},
            {"id": "c2", "name": "notify_owner", "args": {}, "risk": "low"},
            {"id": "c3", "name": "close_ticket", "args": {}, "risk": "low"},
        ],
        risk=RiskLevel.HIGH,
    )
    resp = await client.get("/approvals", headers=admin_headers)
    row = next(r for r in resp.json()["rows"] if r["id"] == "ap-fanout")
    assert [a["name"] for a in row["actions"]] == [
        "issue_credit",
        "notify_owner",
        "close_ticket",
    ]


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
