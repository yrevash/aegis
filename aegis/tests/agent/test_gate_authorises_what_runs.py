"""The gate under fan-out: what the human is shown is what the platform runs.

Phase 5's central safety claim is that a sub-agent may *propose* a consequential action
and that the main graph's single ``gate → approval → act`` path is the only thing that
executes one. That claim was tested with a single proposing lane, and it was false the
moment a second lane proposed anything: ``run_team`` aggregates every lane's proposals
into ``tool_calls``, the approval dialog rendered ``max(calls, key=risk)`` — one action —
and ``act`` then looped over **all** of them. One dialog naming one write, three writes.

The fix is one gate that ENUMERATES everything it authorises (structured ``actions`` on
the event, and the same list spelled out in the ``rationale`` field a console already
renders), with ``act`` executing the enumerated ids and nothing else. These tests pin
the property that shape exists for: **the set shown and the set run are the same set.**
"""

from __future__ import annotations

import pytest

from aegis.agent import SubAgentSpec
from aegis.core.types import RiskLevel, RunStatus
from aegis.gateway.types import LLMResult, ToolCallResult, Usage

from .test_team_fanout import DEMO_QUERY, _drive, _one, build_team_deps

pytestmark = pytest.mark.anyio


def _proposer(request_id: str, status: str, call_id: str):
    """A lane that wants exactly one HIGH-risk status change and can take none."""

    async def _lane(messages):
        if any(m.get("role") == "tool" for m in messages):
            return LLMResult(
                content="proposed.", tool_calls=[], usage=Usage(), model="fake-cheap"
            )
        return LLMResult(
            content=f"{request_id} should be {status}.",
            tool_calls=[
                ToolCallResult(
                    id=call_id,
                    name="update_request_status",
                    args={"request_id": request_id, "status": status},
                )
            ],
            usage=Usage(prompt_tokens=6, completion_tokens=3, cost_usd=0.0002),
            model="fake-cheap",
        )

    return _lane


def _three_proposing_lanes():
    roster = [
        SubAgentSpec(
            agent_id=role,
            role=role,
            label=f"{role} agent",
            system_prompt=f"You are the {role} agent.",
            tool_allowlist=frozenset({"update_request_status"}),
        )
        for role in ("research", "knowledge", "data")
    ]
    deps, rec = build_team_deps(
        roster=roster,
        lane_behaviour={
            "research": _proposer("R1", "resolved", "c1"),
            "knowledge": _proposer("R2", "closed", "c2"),
            "data": _proposer("R3", "cancelled", "c3"),
        },
    )
    executed: list[tuple[str, dict]] = []

    async def run_tool(persona, name, args, **_kw):  # noqa: ANN001, ARG001
        rec.executed.append(name)
        executed.append((name, dict(args)))

        class _O:
            ok = True
            summary = f"{args} applied"

        return _O()

    deps.run_tool = run_tool
    return deps, executed


def _fingerprint(name: str, args: dict) -> tuple:
    return (name, tuple(sorted(args.items())))


async def test_one_gate_authorises_exactly_the_actions_it_enumerated():
    """Three lanes, three distinct HIGH-risk writes, ONE gate that names all three."""
    deps, executed = _three_proposing_lanes()
    events = await _drive(deps, DEMO_QUERY, approve=True)

    required = [e for e in events if e["type"] == "approval_required"]
    assert len(required) == 1, "the fan-out must still resolve at ONE gate"
    gate = required[0]

    shown = {_fingerprint(a["name"], a["args"]) for a in gate["actions"]}
    ran = {_fingerprint(name, args) for name, args in executed}
    assert len(gate["actions"]) == 3, (
        f"three lanes each proposed a distinct write; the gate enumerated "
        f"{len(gate['actions'])}: {gate['actions']!r}"
    )
    assert shown == ran, (
        f"the human authorised {sorted(shown)} and the platform ran {sorted(ran)}"
    )
    # Every proposal is attributed to the lane that made it, so a reviewer can see that
    # three different agents are asking for three different things.
    assert {a["agent_id"] for a in gate["actions"]} == {"research", "knowledge", "data"}
    assert _one(events, "run_finished")["status"] == RunStatus.COMPLETED.value


async def test_the_gates_human_readable_rationale_names_every_action_too():
    """``rationale`` is the field the approval dialog and the durable inbox row render.

    A structured ``actions`` list that no existing surface reads would leave the human
    exactly as misinformed as before, so the prose half is asserted separately.
    """
    deps, _executed = _three_proposing_lanes()
    events = await _drive(deps, DEMO_QUERY, approve=True)
    rationale = _one(events, "approval_required")["rationale"]

    assert "all 3 of these actions" in rationale, rationale
    for request_id in ("R1", "R2", "R3"):
        assert request_id in rationale, (
            f"the dialog's own text never mentions {request_id}, which approving it "
            f"would change: {rationale!r}"
        )


async def test_a_single_action_gate_reads_exactly_as_it_did_before():
    """One proposal is the common case and must not grow a list nobody needs."""
    deps, _executed = _three_proposing_lanes()
    deps.subagent_roster = lambda: [
        SubAgentSpec(
            agent_id="data",
            role="data",
            label="data agent",
            system_prompt="You are the data agent.",
            tool_allowlist=frozenset({"update_request_status"}),
        ),
        SubAgentSpec(
            agent_id="knowledge",
            role="knowledge",
            label="knowledge agent",
            system_prompt="You are the knowledge agent.",
        ),
    ]
    events = await _drive(deps, DEMO_QUERY, approve=True)
    gate = _one(events, "approval_required")
    assert len(gate["actions"]) == 1
    assert gate["action"] == "update_request_status"
    assert gate["risk"] == RiskLevel.HIGH.value
    assert "of these actions" not in gate["rationale"]


async def test_act_executes_the_approved_ids_and_nothing_else():
    """The filter, at the smallest unit: a call the gate never enumerated cannot run.

    Not a hypothetical. It is what makes "the human authorised what ran" a property of
    the code rather than of ``approval`` and ``act`` happening to iterate the same list.
    """
    from aegis.agent.graph import _authorised_calls

    calls = [
        {"id": "a", "name": "update_request_status", "args": {}},
        {"id": "b", "name": "update_request_status", "args": {}},
    ]
    gated = {"tool_calls": calls, "gated": True, "approved_call_ids": ["a"]}
    assert [c["id"] for c in _authorised_calls(gated)] == ["a"]
    # An ungated run never opened a dialog, so there is nothing to have been shown: it
    # executes its low-risk calls exactly as it always has.
    assert [c["id"] for c in _authorised_calls({"tool_calls": calls})] == ["a", "b"]
