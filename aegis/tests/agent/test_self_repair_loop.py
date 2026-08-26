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
    deps = make_deps(propose_tool=True, high_risk=False)
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
    deps = make_deps(propose_tool=True, high_risk=False)
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
            "tool_call",
            "tool_result",
            "guardrail",
            "token",
            "run_finished",
        ],
    )


@pytest.mark.asyncio
async def test_iteration_budget_caps_planning_rounds(make_deps):
    deps = make_deps(propose_tool=True, high_risk=False)
    run_tool, calls = _failing_run_tool(fail_first_n=99)
    deps = dataclasses.replace(deps, run_tool=run_tool)

    events = await _drive(deps, approve=False)
    types = [e["type"] for e in events]

    # The budget is the OUTER bound and no longer the one that fires first. A call
    # that fails identically every time is stopped by progress detection at three
    # attempts, before the fourth round the budget would allow — which is the point:
    # a budget bounds spend, it does not notice that nothing is being achieved.
    assert deps.config.max_plan_iterations == 4
    assert types.count("tool_call") == 3
    assert calls["n"] == 3

    reflections = [e for e in events if e["type"] == "reflection"]
    assert len(reflections) == 3
    # Rounds one and two repair; round three is the one progress detection stops.
    assert reflections[0]["will_retry"] is True
    assert reflections[1]["will_retry"] is True
    assert reflections[2]["will_retry"] is False
    assert reflections[2]["done"] is False

    # And the trace says WHICH bound fired. "budget exhausted" would be a lie here —
    # a round was still available; the loop stopped because nothing was improving.
    checks = [e for e in events if e["type"] == "verification"]
    assert checks[-1]["outcome"] == "OSCILLATING"
    assert checks[-1]["repairable"] is False
    assert types[-1] == "run_finished"


@pytest.mark.asyncio
async def test_larger_budget_allows_more_rounds(make_deps):
    deps = make_deps(propose_tool=True, high_risk=False)
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


@pytest.mark.asyncio
async def test_a_rail_refusal_stops_the_loop_instead_of_retrying_it(make_deps):
    """A result the output rail refused is a decision, not a failure to repair.

    ``act`` used to fold both into one ``ok=False``: the tool failed, or the tool
    succeeded and the rail declined to let its output into the prompt. Indistinguishable
    by ``reflect``, so both re-planned. At the old budget of two that was invisible —
    there was never a third round to waste. Raising the budget turns it into a retry
    loop pointed at our own guardrail, re-running the same call to be refused again for
    the same reason, spending real tokens each time.

    The tool here SUCCEEDS every time. Only the rail refuses. So a loop that retries is
    a loop repairing something that never broke.
    """
    deps = make_deps(propose_tool=True, high_risk=False)

    calls = {"n": 0}

    async def run_tool(persona, name, args, *, actor, model, trace_id, approver):  # noqa: ANN001
        calls["n"] += 1
        return _Outcome(ok=True, summary="the write landed")

    async def refuse_everything(text, *, tool_name, deps, writer):  # noqa: ANN001
        return False, "[tool result withheld by the output guardrail]"

    deps = dataclasses.replace(deps, run_tool=run_tool)

    import aegis.agent.graph as graph_module

    original = graph_module.screen_tool_result
    graph_module.screen_tool_result = refuse_everything
    try:
        events = await _drive(deps, approve=False)
    finally:
        graph_module.screen_tool_result = original

    types = [e["type"] for e in events]

    # The whole point: one attempt, not a budget's worth.
    assert calls["n"] == 1, f"the refused call was retried {calls['n']} times"
    assert types.count("tool_call") == 1

    reflections = [e for e in events if e["type"] == "reflection"]
    assert len(reflections) == 1
    assert reflections[0]["will_retry"] is False
    assert reflections[0]["done"] is False
    # And it says which of the two failures it was, so the trace is not ambiguous.
    assert "guardrail refused" in reflections[0]["reason"]

    assert types[-1] == "run_finished"


@pytest.mark.asyncio
async def test_an_identical_call_failing_three_times_stops_the_loop(make_deps):
    """A budget is not a termination guarantee on its own.

    A tool that fails the same way every time will consume every round it is given and
    end with nothing to show for the spend. The bound that matters is not "how many
    rounds are left" but "is this making progress" — three identical attempts is stuck,
    and stopping there is the difference between a bounded loop and an expensive one.

    The threshold is three rather than two on purpose: the second identical attempt is
    the ordinary repair of a transient failure, and condemning it would refuse to fix
    exactly the failures most worth fixing. ``test_larger_budget_allows_more_rounds``
    pins that lower edge; this pins the upper one.
    """
    deps = make_deps(propose_tool=True, high_risk=False)
    # Never succeeds. With a budget of 6 an unbounded loop would call six times.
    run_tool, calls = _failing_run_tool(fail_first_n=99)
    deps = dataclasses.replace(
        deps,
        run_tool=run_tool,
        config=dataclasses.replace(deps.config, max_plan_iterations=6),
    )

    events = await _drive(deps, approve=False)
    types = [e["type"] for e in events]

    assert calls["n"] == 3, f"the stuck call ran {calls['n']} times, expected 3"

    checks = [e for e in events if e["type"] == "verification"]
    assert checks, "verify emitted nothing"
    assert checks[-1]["outcome"] == "OSCILLATING"
    assert checks[-1]["repairable"] is False

    reflections = [e for e in events if e["type"] == "reflection"]
    assert reflections[-1]["will_retry"] is False
    assert types[-1] == "run_finished"


@pytest.mark.asyncio
async def test_a_read_only_round_does_not_spend_the_repair_budget(make_deps):
    """The arithmetic that made a failed write unretryable at the old default.

    The canonical demo is read-then-write: round one looks the record up, round two
    changes it. Counting the lookup against the repair budget meant that by the time the
    write failed there was nothing left to repair it with — the one moment the loop
    exists for was the one moment it could not act.

    ``verify`` returns ``repair_iterations: 0`` for a round that only read.
    """
    deps = make_deps(propose_tool=True, high_risk=False)
    run_tool, _ = _failing_run_tool(fail_first_n=0)
    deps = dataclasses.replace(deps, run_tool=run_tool)

    events = await _drive(deps, approve=False)
    checks = [e for e in events if e["type"] == "verification"]

    assert checks, "verify emitted nothing"
    # Whatever the verdict, a round that reported GATHERED must not be charged as a
    # repair round — that is the whole point of the separate counter.
    for check in checks:
        if check["outcome"] == "GATHERED":
            assert check["repairable"] is False
