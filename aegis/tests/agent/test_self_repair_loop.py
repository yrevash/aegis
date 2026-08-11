"""Bounded self-repair loop tests (Reflexion-style) — fakes + injected seams only.

These pin the genuine plan → gate → act → reflect loop in the agent graph:

* A first action that FAILS (``ToolOutcome.ok=False``) triggers a re-plan and a
  SECOND ``tool_call``, then the run finishes.
* A first action that SUCCEEDS does NOT loop — a single ``tool_call``, and the
  money-shot ordered subsequence still holds unchanged.
* The iteration budget (``max_plan_iterations``) HARD-CAPS the planning rounds.
"""

from __future__ import annotations

import dataclasses

import pytest

from aegis.agent import ApprovalRegistry, run_agent
from aegis.core.types import ApprovalDecision


def _ordered_subsequence(whole: list[str], sub: list[str]) -> bool:
    it = iter(whole)
    return all(item in it for item in sub)


async def _drive(deps, query="resolve R1", persona="operations_lead", approve=True):
    """Run a query to completion, auto-approving any human gate."""
    registry = ApprovalRegistry()
    events: list[dict] = []
    async for event in run_agent(query, persona=persona, deps=deps, registry=registry):
        events.append(event)
        if event["type"] == "approval_required" and approve:
            registry.resolve(event["approval_id"], ApprovalDecision.APPROVE, approver="al")
    return events


class _Outcome:
    def __init__(self, ok: bool, summary: str) -> None:
        self.ok = ok
        self.summary = summary


def _failing_run_tool(fail_first_n: int):
    """Return a ``run_tool`` fake whose first ``fail_first_n`` calls report failure."""
    calls = {"n": 0}

    async def run_tool(persona, name, args, *, actor, model, trace_id, approver):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] <= fail_first_n:
            return _Outcome(ok=False, summary=f"attempt {calls['n']} failed")
        return _Outcome(ok=True, summary=f"attempt {calls['n']} resolved")

    return run_tool, calls


@pytest.mark.asyncio
async def test_failed_action_triggers_replan_and_second_tool_call(make_deps):
    deps = make_deps(propose_tool=True, uncertain=False, high_risk=False)
    run_tool, calls = _failing_run_tool(fail_first_n=1)
    deps = dataclasses.replace(deps, run_tool=run_tool)

    events = await _drive(deps, approve=False)
    types = [e["type"] for e in events]

    assert types.count("tool_call") == 2
    assert types.count("tool_result") == 2
    assert calls["n"] == 2

    reflections = [e for e in events if e["type"] == "reflection"]
    assert len(reflections) == 2
    assert reflections[0]["will_retry"] is True and reflections[0]["done"] is False
    assert reflections[1]["will_retry"] is False and reflections[1]["done"] is True

    assert _ordered_subsequence(
        types,
        [
            "tool_call",
            "tool_result",
            "reflection",
            "tool_call",
            "tool_result",
            "reflection",
            "token",
            "run_finished",
        ],
    )
    assert types[-1] == "run_finished"


@pytest.mark.asyncio
async def test_successful_action_does_not_loop(make_deps):
    deps = make_deps(propose_tool=True, uncertain=False, high_risk=False)
    events = await _drive(deps, approve=False)
    types = [e["type"] for e in events]

    assert types.count("tool_call") == 1
    reflections = [e for e in events if e["type"] == "reflection"]
    assert len(reflections) == 1
    assert reflections[0]["done"] is True and reflections[0]["will_retry"] is False

    assert _ordered_subsequence(
        types,
        [
            "run_started",
            "guardrail",
            "retrieval",
            "ml_explanation",
            "tool_call",
            "tool_result",
            "guardrail",
            "token",
            "run_finished",
        ],
    )


@pytest.mark.asyncio
async def test_iteration_budget_caps_planning_rounds(make_deps):
    deps = make_deps(propose_tool=True, uncertain=False, high_risk=False)
    run_tool, calls = _failing_run_tool(fail_first_n=99)
    deps = dataclasses.replace(deps, run_tool=run_tool)

    events = await _drive(deps, approve=False)
    types = [e["type"] for e in events]

    assert deps.config.max_plan_iterations == 2
    assert types.count("tool_call") == 2
    assert calls["n"] == 2

    reflections = [e for e in events if e["type"] == "reflection"]
    assert len(reflections) == 2
    assert reflections[0]["will_retry"] is True
    assert reflections[1]["will_retry"] is False
    assert reflections[1]["done"] is False
    assert "budget exhausted" in reflections[1]["reason"]
    assert types[-1] == "run_finished"


@pytest.mark.asyncio
async def test_larger_budget_allows_more_rounds(make_deps):
    deps = make_deps(propose_tool=True, uncertain=False, high_risk=False)
    run_tool, calls = _failing_run_tool(fail_first_n=2)
    deps = dataclasses.replace(
        deps,
        run_tool=run_tool,
        config=dataclasses.replace(deps.config, max_plan_iterations=3),
    )

    types = [e["type"] for e in await _drive(deps, approve=False)]

    assert types.count("tool_call") == 3
    assert calls["n"] == 3
    assert types[-1] == "run_finished"
