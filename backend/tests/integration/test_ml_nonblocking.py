"""E2E: ML is non-blocking evidence — it never abstains or gates a run (founder rule).

A degenerate prediction (very low confidence, very wide interval) used to map to a
terminal *abstain* band. That is retired: ML is a solution SIGNAL only. Through the
HTTP surface, a degenerate prediction on a within-ceiling action must **not** emit
``abstained`` and must **not** route to the human gate — the run acts autonomously,
surfaces the ML evidence, and finishes cleanly. Driven end-to-end through
``POST /query``.
"""

from __future__ import annotations

import json

import pytest

from app.api import routes as api_routes
from app.main import app

pytestmark = pytest.mark.asyncio


async def test_degenerate_prediction_never_abstains_and_still_acts(
    client, db, admin_headers, make_deps, parse_sse
):
    # A proposed within-ceiling (MEDIUM) action + a degenerate prediction.
    app.dependency_overrides[api_routes.get_agent_deps] = lambda: make_deps(
        propose_tool=True, degenerate=True, high_risk=False
    )

    resp = await client.post(
        "/query",
        json={"query": "resolve request R1", "persona": "operations_lead"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    events = parse_sse(resp.text)
    types = [e["event"] for e in events]

    # ML never terminates or gates the run.
    assert "abstained" not in types
    assert "approval_required" not in types
    # The action executed autonomously and the run finished cleanly.
    assert "tool_call" in types
    assert "tool_result" in types
    assert types[-1] == "run_finished"
    assert json.loads(events[-1]["data"])["status"] == "completed"

    # The ML evidence is surfaced informationally, carrying no gating semantics.
    ml = json.loads(next(e for e in events if e["event"] == "ml_explanation")["data"])
    assert ml.get("gated") is None
    assert ml["prediction"] is not None
