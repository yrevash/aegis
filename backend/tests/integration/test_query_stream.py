"""The vertical-slice integration test — the deliverable.

Drives ``POST /query`` end-to-end through the real FastAPI app and the real
LangGraph agent, with a **fake gateway** (canned ``complete`` returning a tool call
then a final answer) and every other capability faked. Asserts the full ordered
``StreamEvent`` sequence streams out, including the human-in-the-loop pause and the
``POST /approval`` resume path. No live infra, no keys, no network.

Because ``httpx.ASGITransport`` buffers the response body, the paused stream is
resolved through the process-wide :class:`ApprovalRegistry` side-channel: a
concurrent task watches for the pending gate and calls ``POST /approval`` (exactly
what a human operator's browser does), which unblocks the run so the buffered body
completes with the post-approval events appended.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.agent import get_approval_registry
from app.api import routes as api_routes
from app.main import app

pytestmark = pytest.mark.asyncio


def _ordered_subsequence(whole: list[str], sub: list[str]) -> bool:
    it = iter(whole)
    return all(item in it for item in sub)


async def test_query_streams_full_sequence_with_gate_and_approval(
    client, db, admin_headers, make_deps, parse_sse
):
    app.dependency_overrides[api_routes.get_agent_deps] = lambda: make_deps(
        propose_tool=True, high_risk=True
    )
    registry = get_approval_registry()

    async def resolver() -> dict | None:
        for _ in range(2000):
            pending = registry.pending_ids()
            if pending:
                resp = await client.post(
                    "/approval",
                    json={"approval_id": pending[0], "decision": "approve"},
                    headers=admin_headers,
                )
                return resp.json()
            await asyncio.sleep(0.005)
        return None

    resolver_task = asyncio.create_task(resolver())
    resp = await client.post(
        "/query",
        json={"query": "Please resolve request R1", "persona": "operations_lead"},
        headers=admin_headers,
    )
    approval_result = await resolver_task

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert approval_result is not None
    assert approval_result["accepted"] is True

    events = parse_sse(resp.text)
    types = [e["event"] for e in events]

    assert types[0] == "run_started"
    assert types[-1] == "run_finished"
    assert _ordered_subsequence(
        types,
        [
            "run_started",
            "guardrail",          # input rail
            "retrieval",          # started
            "retrieval",          # done (graph delta)
            "ml_explanation",     # conformal + SHAP
            "approval_required",  # the human gate (money-shot pause)
            "tool_call",          # only after approval
            "tool_result",
            "guardrail",          # output rail
            "token",              # streamed answer
            "run_finished",
        ],
    )
    # Bounded autonomy: nothing executes before the human approves.
    assert types.index("approval_required") < types.index("tool_call")

    approval_event = next(e for e in events if e["event"] == "approval_required")
    payload = json.loads(approval_event["data"])
    assert payload["action"] == "update_request_status"
    assert payload["rationale"]

    finished = json.loads(events[-1]["data"])
    assert finished["status"] == "completed"
    assert finished["prompt_tokens"] > 0

    # The graph delta streamed during retrieval populates GET /graph.
    graph = await client.get("/graph", headers=admin_headers)
    assert graph.status_code == 200
    assert graph.json()["nodes"]

    # The finished run is reflected on the metrics dashboard.
    metrics = await client.get("/metrics", headers=admin_headers)
    assert metrics.json()["cost_per_1k_queries_usd"] > 0


async def test_query_rejection_path_skips_execution(
    client, db, admin_headers, make_deps, parse_sse
):
    app.dependency_overrides[api_routes.get_agent_deps] = lambda: make_deps(
        propose_tool=True, high_risk=True
    )
    registry = get_approval_registry()

    async def resolver() -> dict | None:
        for _ in range(2000):
            pending = registry.pending_ids()
            if pending:
                resp = await client.post(
                    "/approval",
                    json={"approval_id": pending[0], "decision": "reject"},
                    headers=admin_headers,
                )
                return resp.json()
            await asyncio.sleep(0.005)
        return None

    resolver_task = asyncio.create_task(resolver())
    resp = await client.post(
        "/query",
        json={"query": "resolve R1", "persona": "operations_lead"},
        headers=admin_headers,
    )
    await resolver_task

    types = [e["event"] for e in parse_sse(resp.text)]
    assert "approval_required" in types
    assert "tool_call" not in types  # rejected → never executed
    assert types[-1] == "run_finished"
