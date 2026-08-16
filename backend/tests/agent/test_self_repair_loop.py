"""Bounded self-repair loop tests (Reflexion-style) — fakes only, no infra.

These pin the genuine plan → gate → act → reflect loop added to the agent graph:

* A first action that FAILS (``ToolOutcome.ok=False``) triggers a re-plan and a
  SECOND ``tool_call``, then the run finishes.
* A first action that SUCCEEDS does NOT loop — a single ``tool_call``, and the
  money-shot ordered subsequence still holds unchanged.
* The iteration budget (``max_plan_iterations``) HARD-CAPS the number of planning
  rounds even when the tool keeps failing — no infinite loop.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.agent import ApprovalRegistry, run_agent


def _ordered_subsequence(whole: list[str], sub: list[str]) -> bool:
    it = iter(whole)
    return all(item in it for item in sub)


async def _drive(deps, query="resolve R1", persona="operations_lead", approve=True):
    """Run a query to completion, auto-approving any human gate."""
    registry = ApprovalRegistry()
    events: list = []
    async for event in run_agent(query, persona=persona, deps=deps, registry=registry):
        events.append(event)
        if event.type == "approval_required" and approve:
            from app.api.schemas import ApprovalDecision

            registry.resolve(event.approval_id, ApprovalDecision.APPROVE, approver="al")
    return events


def _failing_run_tool(fail_first_n: int):
    """Return a ``run_tool`` fake whose first ``fail_first_n`` calls report failure.

    The ``ok`` flag is what the domain-agnostic ``reflect`` node reads to decide
    whether to re-plan, so this drives the loop deterministically.
    """
    calls = {"n": 0}

    async def run_tool(persona, name, args, *, actor, model, trace_id, approver):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] <= fail_first_n:
            return _Outcome(ok=False, summary=f"attempt {calls['n']} failed")
        return _Outcome(ok=True, summary=f"attempt {calls['n']} resolved")

    return run_tool, calls


class _Outcome:
    def __init__(self, ok: bool, summary: str) -> None:
        self.ok = ok
        self.summary = summary


# ── failed first action triggers a re-plan + second tool_call ────────────────
@pytest.mark.asyncio
async def test_failed_action_triggers_replan_and_second_tool_call(make_deps):
    # MEDIUM risk (no gate). The first action fails, the second succeeds.
    deps = make_deps(propose_tool=True, high_risk=False)
    run_tool, calls = _failing_run_tool(fail_first_n=1)
    deps = dataclasses.replace(deps, run_tool=run_tool)

    events = await _drive(deps, approve=False)
    types = [e.type for e in events]

    # Two rounds of act: the failure caused exactly one re-plan.
    assert types.count("tool_call") == 2
    assert types.count("tool_result") == 2
    assert calls["n"] == 2

    # A reflection event was streamed for each round; the first retried, second done.
    reflections = [e for e in events if e.type == "reflection"]
    assert len(reflections) == 2
    assert reflections[0].will_retry is True and reflections[0].done is False
    assert reflections[1].will_retry is False and reflections[1].done is True

    # The self-repair is ordered: first result, then a re-plan reasoning + 2nd call.
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


# ── successful first action does NOT loop (money-shot unchanged) ─────────────
@pytest.mark.asyncio
async def test_successful_action_does_not_loop(make_deps):
    deps = make_deps(propose_tool=True, high_risk=False)
    events = await _drive(deps, approve=False)
    types = [e.type for e in events]

    assert types.count("tool_call") == 1  # single action, no self-repair round
    reflections = [e for e in events if e.type == "reflection"]
    assert len(reflections) == 1
    assert reflections[0].done is True and reflections[0].will_retry is False

    # The existing money-shot ordered subsequence still holds unchanged.
    assert _ordered_subsequence(
        types,
        [
            "run_started",
            "guardrail",
            "retrieval",
            "tool_call",
            "tool_result",
            "guardrail",
            "token",
            "run_finished",
        ],
    )


# ── iteration budget hard-caps the number of planning rounds ─────────────────
@pytest.mark.asyncio
async def test_iteration_budget_caps_planning_rounds(make_deps):
    # The tool fails on EVERY call; the loop must still terminate at the hard cap.
    deps = make_deps(propose_tool=True, high_risk=False)
    run_tool, calls = _failing_run_tool(fail_first_n=99)
    deps = dataclasses.replace(deps, run_tool=run_tool)

    events = await _drive(deps, approve=False)
    types = [e.type for e in events]

    # Default budget is 2 planning rounds → exactly 2 tool_calls, then it gives up.
    assert deps.config.max_plan_iterations == 2
    assert types.count("tool_call") == 2
    assert calls["n"] == 2

    reflections = [e for e in events if e.type == "reflection"]
    assert len(reflections) == 2
    assert reflections[0].will_retry is True   # round 1 → retry
    assert reflections[1].will_retry is False  # round 2 → budget exhausted, stop
    assert reflections[1].done is False        # goal never met, but the run finishes
    assert "budget exhausted" in reflections[1].reason
    assert types[-1] == "run_finished"


@pytest.mark.asyncio
async def test_larger_budget_allows_more_rounds(make_deps):
    # Bumping the budget to 3 lets a tool that fails twice succeed on the 3rd round.
    deps = make_deps(propose_tool=True, high_risk=False)
    run_tool, calls = _failing_run_tool(fail_first_n=2)
    deps = dataclasses.replace(
        deps,
        run_tool=run_tool,
        config=dataclasses.replace(deps.config, max_plan_iterations=3),
    )

    types = [e.type for e in await _drive(deps, approve=False)]

    assert types.count("tool_call") == 3  # failed, failed, succeeded
    assert calls["n"] == 3
    assert types[-1] == "run_finished"
