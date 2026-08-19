"""The sharp edge of phase 5: a sub-agent may PROPOSE a risky action, never take one.

Written before the fan-out existed, deliberately. The whole concurrency design rests on
one constraint — *no ``interrupt()`` inside a gathered task* — and that constraint is
only safe because a sub-agent cannot reach a consequential action at all. These tests
prove the removal rather than assuming it, at the smallest unit that can carry it: one
sub-agent, one loop, no graph.

The companion end-to-end proof (propose → gate → park → resume through the EXISTING
approval path) lives in ``test_team_fanout.py``, which needs the fan-out to exist.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from aegis.agent import AgentConfig, AgentDeps
from aegis.agent.subagent import (
    SubAgentSpec,
    SubAgentStatus,
    allowed_tool_definitions,
    run_subagent,
)
from aegis.core.types import RiskLevel
from aegis.gateway.types import BudgetExceededError, LLMResult, ToolCallResult, Usage

pytestmark = pytest.mark.anyio


class _Outcome:
    def __init__(self, ok: bool = True, summary: str = "noted") -> None:
        self.ok = ok
        self.summary = summary


def _spec(**kwargs) -> SubAgentSpec:
    base = {
        "agent_id": "a1",
        "role": "data",
        "label": "Data agent",
        "system_prompt": "You read records.",
        "tool_allowlist": frozenset({"update_request_status", "add_case_note"}),
        "max_steps": 3,
        "timeout_s": 5.0,
    }
    base.update(kwargs)
    return SubAgentSpec(**base)


def _deps(
    *,
    completions,
    risks=None,
    executed=None,
    tool_names=("update_request_status", "add_case_note"),
    config=None,
):
    """Build AgentDeps whose planner replies come from ``completions`` in order."""
    risks = risks or {"update_request_status": RiskLevel.HIGH, "add_case_note": RiskLevel.LOW}
    executed = executed if executed is not None else []
    replies = list(completions)

    async def complete(role, messages, *, tools=None, **_kw):  # noqa: ANN001, ARG001
        return replies.pop(0) if replies else LLMResult(
            content="done", tool_calls=[], usage=Usage(), model="fake"
        )

    def tool_definitions_for(persona: str) -> list[dict]:  # noqa: ARG001
        return [
            {"type": "function", "function": {"name": name, "parameters": {}}}
            for name in tool_names
        ]

    def tool_risk(name: str) -> RiskLevel:
        return risks.get(name, RiskLevel.HIGH)

    async def run_tool(persona, name, args, **_kw):  # noqa: ANN001, ARG001
        executed.append(name)
        return _Outcome(summary=f"{name} ran")

    async def unreachable(*_a, **_k):  # pragma: no cover - not used by these tests
        raise AssertionError("not part of the sub-agent path")

    return (
        AgentDeps(
            complete=complete,
            retrieve=unreachable,
            check_input=unreachable,
            check_output=unreachable,
            tool_definitions_for=tool_definitions_for,
            run_tool=run_tool,
            tool_risk=tool_risk,
            render_system_prompt=lambda persona, extra_context=None: "sys",  # noqa: ARG005
            config=config or AgentConfig(),
        ),
        executed,
    )


def _call(name: str, call_id: str = "c1") -> ToolCallResult:
    return ToolCallResult(id=call_id, name=name, args={"request_id": "R1"})


def _reply(content: str, *calls: ToolCallResult) -> LLMResult:
    return LLMResult(
        content=content,
        tool_calls=list(calls),
        usage=Usage(prompt_tokens=5, completion_tokens=2, cost_usd=0.0002),
        model="fake-cheap",
    )


# ── The constraint: HIGH risk is proposed, never executed ─────────────────────


async def test_high_risk_call_is_proposed_and_never_executed():
    deps, executed = _deps(
        completions=[
            _reply("I should resolve R1.", _call("update_request_status")),
            _reply("Proposed the status change; here is what I found."),
        ]
    )
    events: list[dict] = []
    result = await run_subagent(
        _spec(), "check R1", deps=deps, persona="operations_lead", writer=events.append
    )

    assert executed == [], "a sub-agent executed a HIGH-risk tool"
    assert [p["name"] for p in result.proposed_actions] == ["update_request_status"]
    assert result.proposed_actions[0]["agent_id"] == "a1"
    assert result.status is SubAgentStatus.OK
    # The proposal is visible as a tool_call with NO matching tool_result in this lane,
    # which is the honest signal that nothing ran here.
    call_ids = {e["call_id"] for e in events if e["type"] == "tool_call"}
    result_ids = {e["call_id"] for e in events if e["type"] == "tool_result"}
    assert call_ids == {"a1:c1"}
    assert result_ids == set()


async def test_below_ceiling_call_is_executed_normally():
    """The mirror image — otherwise the test above would pass on a broken loop."""
    deps, executed = _deps(
        completions=[
            _reply("Let me annotate it.", _call("add_case_note", "c9")),
            _reply("Annotated."),
        ]
    )
    events: list[dict] = []
    result = await run_subagent(
        _spec(), "note R1", deps=deps, persona="operations_lead", writer=events.append
    )
    assert executed == ["add_case_note"]
    assert result.proposed_actions == []
    assert [e["ok"] for e in events if e["type"] == "tool_result"] == [True]


async def test_the_gate_floor_is_what_decides_not_the_tool_name():
    """Lower ``gate_min_risk`` and the previously-executable tool becomes a proposal."""
    deps, executed = _deps(
        completions=[
            _reply("Annotating.", _call("add_case_note", "c9")),
            _reply("Proposed."),
        ],
        config=AgentConfig(gate_min_risk=RiskLevel.LOW),
    )
    result = await run_subagent(
        _spec(), "note R1", deps=deps, persona="operations_lead", writer=lambda _e: None
    )
    assert executed == []
    assert [p["name"] for p in result.proposed_actions] == ["add_case_note"]


# ── The intersection: spec ∩ persona, through the persona's own allowlist ─────


async def test_tools_are_the_spec_allowlist_intersected_with_the_personas():
    deps, _ = _deps(completions=[_reply("done")], tool_names=("add_case_note",))
    spec = _spec(tool_allowlist=frozenset({"update_request_status", "add_case_note"}))
    definitions = allowed_tool_definitions(spec, deps, "client")
    # The persona only offers add_case_note; the spec asked for two. The intersection
    # is one — a sub-agent can narrow its persona's reach, never widen it.
    assert [d["function"]["name"] for d in definitions] == ["add_case_note"]


async def test_a_tool_outside_the_intersection_is_refused_not_run():
    deps, executed = _deps(
        completions=[
            _reply("Escalating.", _call("update_request_status")),
            _reply("Could not escalate."),
        ],
        tool_names=("add_case_note",),  # the persona does not offer the risky tool
    )
    result = await run_subagent(
        _spec(), "escalate R1", deps=deps, persona="client", writer=lambda _e: None
    )
    assert executed == []
    assert result.proposed_actions == [], (
        "a tool the persona never offered must not become a gate proposal either"
    )


# ── It never raises. Except the tenant's own budget. ──────────────────────────


async def test_a_model_failure_becomes_a_result_not_an_exception():
    async def exploding(*_a, **_k):
        raise RuntimeError("gateway on fire")

    deps, _ = _deps(completions=[])
    deps.complete = exploding
    result = await run_subagent(
        _spec(), "anything", deps=deps, persona="operations_lead", writer=lambda _e: None
    )
    assert result.status is SubAgentStatus.FAILED
    assert "gateway on fire" in (result.error or "")


async def test_a_tool_explosion_becomes_a_failed_tool_result_not_a_raise():
    deps, _ = _deps(
        completions=[_reply("Annotating.", _call("add_case_note", "c9")), _reply("ok")]
    )

    async def exploding_tool(*_a, **_k):
        raise RuntimeError("store unreachable")

    deps.run_tool = exploding_tool
    events: list[dict] = []
    result = await run_subagent(
        _spec(), "note R1", deps=deps, persona="operations_lead", writer=events.append
    )
    assert result.status is SubAgentStatus.OK
    assert [e["ok"] for e in events if e["type"] == "tool_result"] == [False]


async def test_budget_exceeded_is_the_one_exception_allowed_out():
    async def broke(*_a, **_k):
        raise BudgetExceededError(
            scope="tenant", scope_id=1, limit_type="usd_cap", limit=1.0, used=2.0
        )

    deps, _ = _deps(completions=[])
    deps.complete = broke
    with pytest.raises(BudgetExceededError):
        await run_subagent(
            _spec(), "anything", deps=deps, persona="operations_lead",
            writer=lambda _e: None,
        )


async def test_the_step_cap_terminates_a_tool_hungry_agent():
    deps, executed = _deps(
        completions=[_reply(f"round {i}", _call("add_case_note", f"c{i}")) for i in range(9)]
    )
    result = await run_subagent(
        _spec(max_steps=2), "loop", deps=deps, persona="operations_lead",
        writer=lambda _e: None,
    )
    assert result.steps == 2
    assert executed == ["add_case_note", "add_case_note"]


# ── The structural tripwire ──────────────────────────────────────────────────


def test_the_subagent_module_cannot_reach_interrupt():
    """``interrupt`` must not be imported or called anywhere a gathered task runs.

    An AST check rather than a grep: it cannot be fooled by the word appearing in a
    docstring (this module's docstring is full of it) and it fails on the *shape* —
    an import or a call — which is the only shape that could actually pause a lane.
    """
    for module in ("subagent.py", "team.py"):
        path = Path(__file__).resolve().parents[3] / "aegis/src/aegis/agent" / module
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "interrupt" not in {a.name for a in node.names}, (
                    f"{module} imports interrupt; a gathered task must never pause "
                    "the graph — sub-agents propose, the main graph's one gate acts."
                )
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "interrupt", f"{module} calls interrupt()"
