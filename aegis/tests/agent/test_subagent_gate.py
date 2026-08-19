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
from aegis.core.types import GuardResult, GuardVerdict, RiskLevel
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

    async def check_input(text: str) -> GuardResult:
        # NOT ``unreachable``: a sub-agent's tool results go through the TOOL_RESULT
        # rail (§5.7), which falls back to the inbound rail when no dedicated one is
        # wired. A fake that raised here would be testing the rail's degrade path
        # instead of the loop.
        return GuardResult(verdict=GuardVerdict.PASS, reason="clean", text=text)

    return (
        AgentDeps(
            complete=complete,
            retrieve=unreachable,
            check_input=check_input,
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
    """Both halves, in one shape neither half alone can satisfy.

    The earlier version of this test built a persona strictly NARROWER than the spec, so
    the persona half decided the answer by itself: deleting the ``spec.tool_allowlist``
    filter from ``allowed_tool_definitions`` left it green, and the whole suite with it.
    A test that names a property has to fail when either half of it is removed, so the
    two allowlists here overlap **partially** and each contributes one exclusion:

    * ``read_request`` is the persona's and not the spec's — only the spec filter drops it;
    * ``escalate`` is the spec's and not the persona's — only the persona half drops it.
    """
    deps, _ = _deps(
        completions=[_reply("done")],
        tool_names=("read_request", "update_request_status", "add_case_note"),
    )
    spec = _spec(
        tool_allowlist=frozenset({"update_request_status", "add_case_note", "escalate"})
    )
    definitions = allowed_tool_definitions(spec, deps, "client")
    assert [d["function"]["name"] for d in definitions] == [
        "update_request_status",
        "add_case_note",
    ]


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


#: Every module in ``aegis.agent`` that code inside a gathered sub-agent task can
#: reach. Derived by EXCLUSION rather than listed, so a module added to the package
#: tomorrow is guarded by default: the tripwire's previous version named two files, and
#: a lane that reached ``interrupt`` through any third module would not have been seen.
#: The graph builds the ``approval`` node and the orchestrator detects its interrupt;
#: those two are the whole legitimate surface, so nothing else in the package is exempt.
_MAY_INTERRUPT = frozenset({"graph.py", "orchestrator.py"})


def _gathered_task_modules() -> list[Path]:
    """Return the package files a gathered task can execute."""
    package = Path(__file__).resolve().parents[3] / "aegis/src/aegis/agent"
    return sorted(
        path
        for path in package.glob("*.py")
        if path.name not in _MAY_INTERRUPT and path.name != "__init__.py"
    )


def _interrupt_reaches(tree: ast.AST) -> str | None:
    """Return why ``tree`` can reach ``interrupt``, or ``None`` when it cannot.

    Three shapes, because the earlier version caught only the first two and the third
    walked straight past it::

        from langgraph.types import interrupt   # ImportFrom  — was caught
        interrupt(value)                        # Call(Name)  — was caught
        import langgraph.types as _lt           # Import      — was NOT caught
        _lt.interrupt(value)                    # Attribute   — was NOT caught
        getattr(_lt, "interrupt")(value)        # getattr     — was NOT caught

    So: no ``import langgraph…`` module-object import at all in these files (the
    ``from langgraph.x import Name`` form the retry policy legitimately needs is still
    allowed, minus ``interrupt`` itself), and the identifier ``interrupt`` may not
    appear as a name, an attribute, or a getattr string anywhere in them.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and "interrupt" in {a.name for a in node.names}:
            return "imports interrupt"
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "langgraph" or alias.name.startswith("langgraph."):
                    return (
                        f"imports the langgraph module object ({alias.name!r}), which is "
                        "an attribute path to interrupt()"
                    )
        if isinstance(node, ast.Name) and node.id == "interrupt":
            return "names interrupt"
        if isinstance(node, ast.Attribute) and node.attr == "interrupt":
            return "reaches .interrupt through an attribute"
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and any(
                isinstance(arg, ast.Constant) and arg.value == "interrupt"
                for arg in node.args
            )
        ):
            return "reaches interrupt through getattr"
    return None


def test_no_module_a_gathered_task_runs_can_reach_interrupt():
    """``interrupt`` must be unreachable from anywhere a gathered task executes.

    An AST check rather than a grep: it cannot be fooled by the word appearing in a
    docstring (these modules' docstrings are full of it) and it fails on the *shape*,
    which is the only thing that could actually pause a lane.
    """
    modules = _gathered_task_modules()
    assert {p.name for p in modules} >= {"subagent.py", "team.py", "retry.py", "rails.py"}, (
        f"the tripwire is not scanning the fan-out's own modules: {modules}"
    )
    for path in modules:
        finding = _interrupt_reaches(ast.parse(path.read_text(encoding="utf-8")))
        assert finding is None, (
            f"aegis/agent/{path.name} {finding}; a gathered task must never pause the "
            "graph — sub-agents propose, the main graph's one gate acts."
        )
