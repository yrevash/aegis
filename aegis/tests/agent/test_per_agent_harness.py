"""The per-agent harness record, and the user's memory reaching a lane's context (§5.9).

Three claims, all driven through the REAL ``run_agent`` over fakes:

1. **A four-agent run produces four readable records**, each carrying that agent's own
   model, tokens, cost and tool calls — and the sum of them **reconciles** with the one
   summed delta the fan-out node reports. A per-agent number nobody can tie back to the
   run's total is a number nobody should believe.
2. **The run-level record stays the supervisor's.** Everything a lane emitted lives in
   that lane's record, so the trace is four readable lanes plus the synthesis rather than
   one blurred stream — and a single-pass run is byte-for-byte the record it always was.
3. **A sub-agent sees the user's durable facts**, selected ONCE by the adapter's own
   selector (``deps.memory.assemble`` → ``memory_spec.render_profile`` /
   ``select_skills``). Four lanes, one selection: a second copy would show up here as a
   second call.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aegis.agent import run_summary
from aegis.gateway.types import LLMResult, ToolCallResult, Usage

from .test_team_fanout import (
    DEMO_QUERY,
    SIMPLE_QUERY,
    _drive,
    _one,
    _roster,
    build_team_deps,
)

pytestmark = pytest.mark.anyio


class _FakeMemory:
    """The adapter's memory selector, doubled — and counted.

    ``assemble`` is the seam behind which ``memory_spec.render_profile`` /
    ``select_skills`` live. Counting the calls is what makes "one selector in the
    codebase" testable: a second copy anywhere in the fan-out shows up here as a second
    call, and a lane re-selecting per agent shows up as four.
    """

    PROFILE = "## Durable facts\n- prefers email\n- works in CET"

    def __init__(self) -> None:
        self.assemble_calls: list[str] = []

    async def assemble(self, *, subject_id, session_id, persona, query, query_vec):  # noqa: ANN001, ARG002
        self.assemble_calls.append(query)
        return SimpleNamespace(
            text=self.PROFILE,
            recalled_fact_ids=[1, 2],
            recalled_message_ids=[7],
            tokens_used=11,
            conversation=[],
        )

    async def persist(self, **_kwargs) -> None:  # noqa: ANN003 - write path unused here
        return None


async def _use_a_cheap_tool(messages):
    """A lane that runs one within-ceiling tool, so its record has a real tool call."""
    if any(m.get("role") == "tool" for m in messages):
        return LLMResult(
            content="I noted the case and found what I needed.",
            tool_calls=[],
            usage=Usage(prompt_tokens=4, completion_tokens=2, cost_usd=0.0001),
            model="fake-cheap",
        )
    return LLMResult(
        content="R1 needs a note.",
        tool_calls=[ToolCallResult(id="c9", name="add_case_note", args={"id": "R1"})],
        usage=Usage(prompt_tokens=6, completion_tokens=3, cost_usd=0.0002),
        model="fake-cheap",
    )


# ── 1. Four agents, four records, and the totals reconcile ────────────────────


async def test_a_four_agent_run_produces_four_readable_records():
    deps, _ = build_team_deps(roster=_roster(4), lane_behaviour={"data": _use_a_cheap_tool})
    events = await _drive(deps, DEMO_QUERY)
    summary = run_summary(events)

    agents = {a["agent_id"]: a for a in summary["agents"]}
    assert set(agents) == {"research", "knowledge", "data", "policy"}

    for agent_id, record in agents.items():
        assert record["role"] == agent_id
        assert record["label"].endswith("agent")
        assert record["status"] == "done"
        assert record["model"] == "fake-cheap", "each lane names the model IT ran on"
        assert record["duration_ms"] is not None
        assert record["prompt_tokens"] > 0
        assert record["cost_usd"] > 0
        assert record["reasoning"], "a lane with no visible reasoning is not readable"

    # The tool call belongs to the lane that made it, and to no other lane.
    assert [t["tool"] for t in agents["data"]["tools"]] == ["add_case_note"]
    assert agents["data"]["tools"][0]["ok"] is True
    assert all(not agents[a]["tools"] for a in ("research", "knowledge", "policy"))
    # ``data`` ran a second round after the tool result, so its spend is the larger one.
    assert agents["data"]["prompt_tokens"] == 10
    assert agents["research"]["prompt_tokens"] == 6


async def test_the_per_agent_totals_reconcile_with_the_runs_summed_delta():
    """A per-agent cost nobody can tie back to the run's total is not evidence."""
    deps, _ = build_team_deps(roster=_roster(4), lane_behaviour={"data": _use_a_cheap_tool})
    events = await _drive(deps, DEMO_QUERY)
    summary = run_summary(events)

    team = summary["team"]
    assert team["agent_count"] == 4
    assert team["reconciles"] is True
    # Stated the long way too, so the assertion above cannot pass by agreeing with itself.
    assert team["prompt_tokens"] == sum(a["prompt_tokens"] for a in summary["agents"])
    assert team["node"]["prompt_tokens"] == team["prompt_tokens"]
    assert team["node"]["completion_tokens"] == team["completion_tokens"]
    assert team["node"]["cost_usd"] == pytest.approx(team["cost_usd"])
    # And the fan-out node's delta is genuinely part of what the run charged for.
    assert _one(events, "run_finished")["prompt_tokens"] >= team["prompt_tokens"]


async def test_a_lanes_events_are_in_its_own_record_and_not_the_runs():
    """Four readable lanes, not one blurred stream."""
    deps, _ = build_team_deps(roster=_roster(4), lane_behaviour={"data": _use_a_cheap_tool})
    events = await _drive(deps, DEMO_QUERY)
    summary = run_summary(events)

    lane_reasoning = {r for a in summary["agents"] for r in a["reasoning"]}
    assert lane_reasoning
    assert not (set(summary["reasoning"]) & lane_reasoning)
    # The lanes' own nodes do not masquerade as graph nodes in the run-level record.
    assert all(not n["node"].startswith("agent:") for n in summary["nodes"])
    # The proposal/execution of a within-ceiling tool stayed inside the lane.
    assert [t["tool"] for t in summary["tools"]] == []


async def test_a_single_pass_run_has_no_agent_dimension_at_all():
    """The record a run without a fan-out produces is the one it always produced."""
    deps, _ = build_team_deps()
    events = await _drive(deps, SIMPLE_QUERY)
    summary = run_summary(events)

    assert summary["agents"] == []
    assert summary["team"] is None
    started = [e["node"] for e in events if e["type"] == "node_started"]
    assert [n["node"] for n in summary["nodes"]] == started


# ── 2. The user's memory reaches the lane, selected once, by the adapter ──────


async def test_a_subagents_context_carries_the_users_profile_selected_by_the_adapter():
    memory = _FakeMemory()
    deps, _ = build_team_deps(roster=_roster(4))
    deps.memory = memory
    sent: list[str] = []
    inner = deps.complete

    async def complete(role, messages, **kwargs):  # noqa: ANN001, ANN003
        if messages and "You are ONE agent in a concurrent team" in messages[0]["content"]:
            sent.append(messages[0]["content"])
        return await inner(role, messages, **kwargs)

    deps.complete = complete
    await _drive(
        deps, DEMO_QUERY, session_id="s-1", memory_subject="user:7"
    )

    assert len(sent) == 4, "every lane should have been prompted"
    for prompt in sent:
        assert _FakeMemory.PROFILE in prompt, (
            "a sub-agent that cannot see the user's durable facts is a WORSE agent "
            "than the single one it replaced"
        )
    # ONE selector: four lanes, one call to the adapter's selection.
    assert memory.assemble_calls == [DEMO_QUERY], (
        "the profile must come from the adapter's selector, called once for the run — "
        f"got {len(memory.assemble_calls)} selections"
    )


async def test_the_fanout_emits_the_same_memory_event_the_single_pass_path_does():
    """The recall is the SAME node body, so it is just as visible on the team path."""
    deps, _ = build_team_deps(roster=_roster(3))
    deps.memory = _FakeMemory()
    events = await _drive(deps, DEMO_QUERY, session_id="s-1", memory_subject="user:7")

    memory_event = _one(events, "memory")
    assert memory_event["recalled_fact_count"] == 2
    assert memory_event.get("agent_id") is None, "recall is supervisor-level work"
