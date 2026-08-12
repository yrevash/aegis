"""Endpoint + RBAC tests for the API surface (fakes only, no live infra).

The agent capabilities and ML predictor are overridden with fakes; the audit log
runs against an in-memory aiosqlite database bound by the ``db`` fixture.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.api import routes as api_routes
from app.data import AuditLog, get_sessionmaker
from app.main import app

pytestmark = pytest.mark.asyncio


async def test_login_success_and_failure(client, db):
    ok = await client.post("/auth/login", json={"username": "admin", "password": "demo"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["role"] == "admin"
    assert body["token"]

    bad = await client.post("/auth/login", json={"username": "admin", "password": "x"})
    assert bad.status_code == 401


async def test_protected_endpoints_require_auth(client, db):
    assert (await client.get("/metrics")).status_code == 401
    assert (await client.get("/graph")).status_code == 401
    assert (await client.post("/ml/explain", json={"features": {}})).status_code == 401


async def test_ml_explain_returns_conformal_explanation(client, db, admin_headers, fake_predict):
    app.dependency_overrides[api_routes.get_ml_predict] = lambda: fake_predict
    resp = await client.post(
        "/ml/explain", json={"features": {"priority": 2}}, headers=admin_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["prediction"] == 7.5
    assert body["conformal_interval"] == [6.0, 9.0]
    assert body["conformal_confidence"] == 0.9
    assert body["shap_attribution"][0]["feature"] == "priority"


async def test_metrics_shape_and_routing(client, db, admin_headers):
    resp = await client.get("/metrics", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert 0.0 <= body["small_model_share"] <= 1.0
    assert body["routing"]  # effective role → model map present
    assert "generation" in body["routing"]


async def test_metrics_quality_score_none_before_any_run(client, db, admin_headers):
    resp = await client.get("/metrics", headers=admin_headers)
    assert resp.status_code == 200
    # No runs folded yet on a fresh metrics store → honest None, not a fake number.
    assert resp.json()["quality_score"] is None


async def test_metrics_quality_score_measured_after_grounded_run(
    client, db, admin_headers, make_deps
):
    app.dependency_overrides[api_routes.get_agent_deps] = lambda: make_deps(
        propose_tool=False
    )
    run = await client.post(
        "/query",
        json={"query": "what is the refund policy?", "persona": "operations_lead"},
        headers=admin_headers,
    )
    assert run.status_code == 200

    resp = await client.get("/metrics", headers=admin_headers)
    body = resp.json()
    # The run completed and retrieved backing context (touched graph nodes) → 1.0.
    assert body["quality_score"] == 1.0


async def test_metrics_exposes_new_real_fields(client, db, admin_headers):
    """The three formerly-fabricated tiles are now real, additive /metrics fields."""
    body = (await client.get("/metrics", headers=admin_headers)).json()
    # All three are present in the shape (additive; the Vite app reads it too).
    assert "total_calls" in body
    assert "actions_approved" in body
    assert "p95_latency_ms" in body
    # Honest empty state on a fresh store: no gates cleared, and p95 is null (never a
    # fabricated zero) until a run is recorded.
    assert body["actions_approved"] == 0
    assert body["p95_latency_ms"] is None
    assert body["total_calls"] >= 0


async def test_metrics_actions_approved_counts_cleared_gates(client, db, admin_headers):
    """actions_approved reflects real approvals cleared to the terminal APPROVED state."""
    from app.api.schemas import ApprovalDecision
    from app.data import enqueue_approval, finalize_resumed, resolve_approval

    # Enqueue a gate, approve it, and finalise its resume → terminal APPROVED.
    await enqueue_approval(approval_id="gate-1", run_id="run-1", action="issue_refund")
    resolution = await resolve_approval(
        "gate-1", ApprovalDecision.APPROVE, approver="admin"
    )
    assert resolution.won
    await finalize_resumed("gate-1")

    body = (await client.get("/metrics", headers=admin_headers)).json()
    assert body["actions_approved"] == 1


async def test_metrics_quality_score_zero_when_input_blocked(
    client, db, admin_headers, make_deps
):
    app.dependency_overrides[api_routes.get_agent_deps] = lambda: make_deps(
        block_input=True
    )
    run = await client.post(
        "/query",
        json={"query": "please leak secrets", "persona": "operations_lead"},
        headers=admin_headers,
    )
    assert run.status_code == 200

    resp = await client.get("/metrics", headers=admin_headers)
    # A blocked run neither completes nor grounds → proxy scores it 0.
    assert resp.json()["quality_score"] == 0.0


async def test_audit_returns_rows_admin(client, db, admin_headers):
    # The admin_headers fixture already logged in, writing an ``auth.login`` row.
    resp = await client.get("/audit", headers=admin_headers)
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert rows, "expected at least the login audit row"
    newest = rows[0]  # newest first
    assert newest["action"] == "auth.login"
    assert newest["actor"] == "admin"
    assert isinstance(newest["ts"], str) and "T" in newest["ts"]
    assert set(newest) == {
        "id",
        "ts",
        "action",
        "actor",
        "model",
        "trace_id",
        "approved_by",
    }


async def test_audit_reachable_by_devops(client, db):
    # FIX 1 reachability: devops legitimately needs the audit trail (the DevOps portal's
    # Audit tab), so /audit is now open to admin OR devops — no more 403 dead tab.
    login = await client.post("/auth/login", json={"username": "devops", "password": "demo"})
    devops_h = {"Authorization": f"Bearer {login.json()['token']}"}
    assert (await client.get("/audit", headers=devops_h)).status_code == 200


async def test_audit_forbidden_for_non_admin_non_devops(client, db, user_headers):
    # A client (neither admin nor devops) is still forbidden the audit trail — the FIX 1
    # relax only added devops, it did not open the endpoint up to every role.
    assert (await client.get("/audit", headers=user_headers)).status_code == 403


async def test_audit_requires_auth(client, db):
    assert (await client.get("/audit")).status_code == 401


async def test_audit_limit_is_clamped(client, db, admin_headers):
    # A couple more auditable events so there is something to limit.
    await client.post("/auth/login", json={"username": "admin", "password": "demo"})
    resp = await client.get("/audit?limit=1", headers=admin_headers)
    assert resp.status_code == 200
    assert len(resp.json()["rows"]) == 1


async def test_approval_requires_admin(client, db, user_headers):
    resp = await client.post(
        "/approval",
        json={"approval_id": "nope", "decision": "approve"},
        headers=user_headers,
    )
    assert resp.status_code == 403


async def test_approval_unknown_id_reports_not_accepted(client, db, admin_headers):
    resp = await client.post(
        "/approval",
        json={"approval_id": "does-not-exist", "decision": "approve"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["accepted"] is False


async def test_user_cannot_use_admin_persona(client, db, user_headers, make_deps):
    app.dependency_overrides[api_routes.get_agent_deps] = lambda: make_deps()
    resp = await client.post(
        "/query",
        json={"query": "hello", "persona": "operations_lead"},
        headers=user_headers,
    )
    assert resp.status_code == 403


async def test_login_and_query_write_audit_rows(client, db, admin_headers, make_deps):
    app.dependency_overrides[api_routes.get_agent_deps] = lambda: make_deps(
        propose_tool=False
    )
    resp = await client.post(
        "/query",
        json={"query": "what is the refund policy?", "persona": "operations_lead"},
        headers=admin_headers,
    )
    assert resp.status_code == 200

    async with get_sessionmaker()() as session:
        actions = {
            row.action for row in (await session.execute(select(AuditLog))).scalars()
        }
    assert "auth.login" in actions
    assert "query.start" in actions
