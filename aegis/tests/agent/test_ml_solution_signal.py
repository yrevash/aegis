"""ML is a non-gating SOLUTION SIGNAL, injected as evidence (founder decision).

These graph-level tests pin the reframed contract:

* ML runs *before* planning and its prediction is injected into the planner AND the
  final answer as supporting evidence (predict-then-plan).
* ML **never** gates, defers, or terminates a run.
* The human gate still fires — but on **tool risk only** (the money-shot).
* No subject / no adapter model → the agent answers with zero ML involvement.

The injected ``describe_prediction`` fake frames the evidence as
``"ML decision-support: ..."`` — the marker these tests look for in the prompts.
"""

from __future__ import annotations

import dataclasses

import pytest

from aegis.agent import ApprovalRegistry, run_agent
from aegis.core.types import ApprovalDecision

_ML_MARKER = "ML decision-support"


async def _drive(deps, query="resolve R1", persona="operations_lead", approve=True):
    """Run a query to completion, optionally auto-approving any human gate."""
    registry = ApprovalRegistry()
    events: list[dict] = []
    async for event in run_agent(query, persona=persona, deps=deps, registry=registry):
        events.append(event)
        if event["type"] == "approval_required" and approve:
            registry.resolve(event["approval_id"], ApprovalDecision.APPROVE, approver="al")
    return events


@pytest.mark.asyncio
async def test_prediction_runs_before_plan_and_is_injected(make_deps):
    deps = make_deps(propose_tool=True, uncertain=False, high_risk=False)
    order: list[str] = []
    captured: dict[str, list] = {}
    orig_predict = deps.predict_explain
    orig_complete = deps.complete

    def predict(features):
        order.append("predict")
        return orig_predict(features)

    async def complete(role, messages, *, tools=None, temperature=0.0, response_format=None):  # noqa: ANN001
        if tools is not None and "plan" not in order:
            order.append("plan")
            captured["messages"] = messages
        return await orig_complete(
            role, messages, tools=tools, temperature=temperature, response_format=response_format
        )

    deps = dataclasses.replace(deps, predict_explain=predict, complete=complete)
    await _drive(deps)

    assert order[:2] == ["predict", "plan"]
    plan_user_msg = captured["messages"][-1]["content"]
    assert _ML_MARKER in plan_user_msg


@pytest.mark.asyncio
async def test_ml_summary_injected_into_final_answer_context(make_deps):
    deps = make_deps(propose_tool=True, uncertain=False, high_risk=False)
    captured: dict[str, list] = {}
    orig_complete = deps.complete

    async def complete(role, messages, *, tools=None, temperature=0.0, response_format=None):  # noqa: ANN001
        if tools is None:  # the generate call composes the final answer
            captured["generate"] = messages
        return await orig_complete(
            role, messages, tools=tools, temperature=temperature, response_format=response_format
        )

    deps = dataclasses.replace(deps, complete=complete)
    await _drive(deps)

    generate_msg = captured["generate"][-1]["content"]
    assert _ML_MARKER in generate_msg


@pytest.mark.asyncio
async def test_degenerate_prediction_does_not_abstain_or_gate(make_deps):
    deps = make_deps(propose_tool=True, degenerate=True, high_risk=False)
    events = await _drive(deps, approve=False)
    types = [e["type"] for e in events]

    assert "abstained" not in types
    assert "approval_required" not in types
    assert "tool_call" in types
    assert "tool_result" in types
    assert types[-1] == "run_finished"
    assert any(e["type"] == "ml_explanation" for e in events)


@pytest.mark.asyncio
async def test_uncertain_prediction_still_executes_autonomously(make_deps):
    deps = make_deps(propose_tool=True, uncertain=True, high_risk=False)
    types = [e["type"] for e in await _drive(deps, approve=False)]
    assert "approval_required" not in types
    assert "abstained" not in types
    assert "tool_call" in types


@pytest.mark.asyncio
async def test_high_risk_action_gates_on_risk_not_ml(make_deps):
    deps = make_deps(propose_tool=True, uncertain=False, high_risk=True)
    saw_gate = False
    events = []
    registry = ApprovalRegistry()
    async for event in run_agent(
        "resolve R1", persona="operations_lead", deps=deps, registry=registry
    ):
        events.append(event)
        if event["type"] == "approval_required":
            saw_gate = True
            assert "risk" in event["rationale"].lower()  # gate reason is risk, not ML
            registry.resolve(event["approval_id"], ApprovalDecision.APPROVE)

    assert saw_gate
    ml = next(e for e in events if e["type"] == "ml_explanation")
    assert ml.get("gated") is None  # ML evidence carries no gating semantics
    assert "abstained" not in [e["type"] for e in events]


@pytest.mark.asyncio
async def test_no_subject_means_zero_ml(make_deps):
    deps = make_deps(propose_tool=True, high_risk=False)
    predicted = {"n": 0}
    orig_predict = deps.predict_explain

    def predict(features):
        predicted["n"] += 1
        return orig_predict(features)

    deps = dataclasses.replace(
        deps, features_for=lambda q, p: {}, predict_explain=predict
    )
    types = [e["type"] for e in await _drive(deps)]

    assert predicted["n"] == 0
    assert "ml_explanation" not in types
    assert types[-1] == "run_finished"
