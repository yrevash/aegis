"""The adaptive fan-out, end to end: width, concurrency, the gate, and degradation.

Every test here drives the REAL ``run_agent`` over fakes — no host, no network, no
model. What is being pinned is the phase's whole argument:

* a simple question stays a single pass, and the ``routing`` event says why;
* a multi-part one fans out, and the stream shows genuinely **interleaved** events from
  concurrent lanes;
* a sub-agent's HIGH-risk action reaches the human gate — the SAME one — and nowhere
  else, and ``interrupt()`` is never reachable from inside a gathered task;
* one lane dying leaves its siblings alone and is NAMED in the synthesis;
* the tenant's own budget is still the one thing that can refuse a run;
* an explicit width from the user is honoured exactly, and the classifier is skipped.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from aegis.agent import (
    AgentConfig,
    AgentDeps,
    ApprovalRegistry,
    ParkedRunRegistry,
    SubAgentSpec,
    resume_parked_run,
    run_agent,
)
from aegis.agent import graph as graph_module
from aegis.core.types import ApprovalDecision, GuardResult, GuardVerdict, RiskLevel, RunStatus
from aegis.gateway.types import BudgetExceededError, LLMResult, ToolCallResult, Usage
from aegis.retrieval.models import GraphDelta, RetrievalResult, Source
from aegis.retrieval.types import RetrievalScope

pytestmark = pytest.mark.anyio

#: The rehearsed complex demo query. Pinned in a test because the classifier
#: UNDER-firing here is the failure that would happen on stage.
DEMO_QUERY = (
    "Compare our escalation policy against what changed in the regulation this "
    "quarter, and tell me which open requests are affected."
)

SIMPLE_QUERY = "What is my remaining budget?"


class _Outcome:
    def __init__(self, ok: bool = True, summary: str = "R1 resolved") -> None:
        self.ok = ok
        self.summary = summary


def _roster(n: int = 4, **overrides) -> list[SubAgentSpec]:
    """Build an n-agent sub-agent roster (the shape an adapter declares in §5.6)."""
    kinds = [
        ("research", "Research agent", frozenset()),
        ("knowledge", "Knowledge agent", frozenset()),
        ("data", "Data agent", frozenset({"update_request_status", "add_case_note"})),
        ("policy", "Policy agent", frozenset()),
    ]
    specs = []
    for role, label, tools in kinds[:n]:
        specs.append(
            SubAgentSpec(
                agent_id=role,
                role=role,
                label=label,
                system_prompt=f"You are the {role} agent.",
                tool_allowlist=tools,
                **overrides,
            )
        )
    return specs


class _Recorder:
    """Records what the fakes were asked to do, so the assertions can be on calls."""

    def __init__(self) -> None:
        self.retrievals: list[str] = []
        self.executed: list[str] = []
        self.completions: list[tuple[str, str]] = []  # (kind, agent-or-role)
        self.starts: dict[str, float] = {}


def _classify(messages) -> str:
    """Name which prompt this is, from its system/user text (the fake's dispatcher)."""
    system = messages[0]["content"] if messages else ""
    user = messages[-1]["content"] if messages else ""
    if "You size a query" in system:
        return "depth"
    if "You split a request into independent sub-tasks" in system:
        return "team_plan"
    if "You are ONE agent in a concurrent team" in system:
        return "subagent"
    if "Findings from the agents that worked on it" in user:
        return "synthesis"
    if "standalone search query" in system or "rewrite a user's latest turn" in system:
        return "rewrite"
    if "retrieval sufficiency judge" in system:
        return "judge"
    return "generation"


def _agent_of(messages) -> str:
    """Read which sub-agent a lane's prompt belongs to (its system prompt names it)."""
    system = messages[0]["content"]
    for role in ("research", "knowledge", "data", "policy"):
        if f"the {role} agent" in system:
            return role
    return "?"


def build_team_deps(
    *,
    roster=None,
    config=None,
    lane_delay: float = 0.0,
    lane_behaviour=None,
    tool_risk_of=None,
    recorder: _Recorder | None = None,
):
    """Wire ``AgentDeps`` with a sub-agent roster and fully scripted model replies."""
    rec = recorder or _Recorder()
    roster = _roster() if roster is None else roster
    lane_behaviour = lane_behaviour or {}
    risks = tool_risk_of or {
        "update_request_status": RiskLevel.HIGH,
        "add_case_note": RiskLevel.LOW,
    }

    async def check_input(text: str) -> GuardResult:
        return GuardResult(verdict=GuardVerdict.PASS, reason="clean", text=text)

    async def check_output(text: str, contexts=None) -> GuardResult:  # noqa: ANN001, ARG001
        return GuardResult(verdict=GuardVerdict.PASS, reason="clean", text=text)

    async def retrieve(query: str, *, scope: RetrievalScope) -> RetrievalResult:  # noqa: ARG001
        rec.retrievals.append(query)
        return RetrievalResult(
            answer_context="Shared corpus context for the run.",
            sources=[Source(id="kb-1", text="Escalation policy", score=0.9)],
            num_candidates=5,
            graph_delta=GraphDelta(nodes=[], edges=[]),
            cache_hit=False,
        )

    async def complete(role, messages, *, tools=None, **_kw):  # noqa: ANN001, ARG001
        kind = _classify(messages)
        if kind == "team_plan":
            rec.completions.append(("team_plan", ""))
            return LLMResult(
                content="\n".join(f"sub-task {i + 1}" for i in range(len(roster))),
                tool_calls=[],
                usage=Usage(prompt_tokens=4, completion_tokens=4, cost_usd=0.0001),
                model="fake-cheap",
            )
        if kind == "subagent":
            agent = _agent_of(messages)
            rec.completions.append(("subagent", agent))
            rec.starts.setdefault(agent, time.perf_counter())
            behaviour = lane_behaviour.get(agent)
            if behaviour is not None:
                return await behaviour(messages)
            if lane_delay:
                await asyncio.sleep(lane_delay)
            return LLMResult(
                content=f"The {agent} agent found something relevant.",
                tool_calls=[],
                usage=Usage(prompt_tokens=6, completion_tokens=3, cost_usd=0.0002),
                model="fake-cheap",
            )
        if kind == "synthesis":
            rec.completions.append(("synthesis", ""))
            return LLMResult(
                content="Merged answer across the agents.",
                tool_calls=[],
                usage=Usage(prompt_tokens=10, completion_tokens=5, cost_usd=0.0005),
                model="fake-generation",
            )
        if kind == "depth":
            rec.completions.append(("depth", ""))
            return LLMResult(content="1", tool_calls=[], usage=Usage(), model="fake-cheap")
        rec.completions.append((kind, ""))
        return LLMResult(
            content="A single-pass answer.",
            tool_calls=[],
            usage=Usage(prompt_tokens=5, completion_tokens=2, cost_usd=0.0001),
            model="fake-generation",
        )

    def tool_definitions_for(persona: str) -> list[dict]:  # noqa: ARG001
        return [
            {"type": "function", "function": {"name": name, "parameters": {}}}
            for name in ("update_request_status", "add_case_note")
        ]

    def tool_risk(name: str) -> RiskLevel:
        return risks.get(name, RiskLevel.HIGH)

    async def run_tool(persona, name, args, **_kw):  # noqa: ANN001, ARG001
        rec.executed.append(name)
        return _Outcome()

    deps = AgentDeps(
        complete=complete,
        retrieve=retrieve,
        check_input=check_input,
        check_output=check_output,
        tool_definitions_for=tool_definitions_for,
        run_tool=run_tool,
        tool_risk=tool_risk,
        render_system_prompt=lambda persona, extra_context=None: "You are Aegis.",  # noqa: ARG005
        config=config
        or AgentConfig(
            stream_chunk_words=4,
            query_rewrite_enabled=False,
            agentic_retrieval_enabled=False,
            answer_cache_enabled=False,
            max_concurrent_agents=4,
        ),
        subagent_roster=lambda: roster,
    )
    return deps, rec


async def _drive(deps, query, *, approve=None, **kwargs):
    """Run to completion, resolving any gate with ``approve``; return the events."""
    registry = ApprovalRegistry()
    events: list[dict] = []
    async for event in run_agent(
        query, persona="operations_lead", deps=deps, registry=registry, **kwargs
    ):
        events.append(event)
        if event["type"] == "approval_required" and approve is not None:
            registry.resolve(
                event["approval_id"],
                ApprovalDecision.APPROVE if approve else ApprovalDecision.REJECT,
                approver="al",
            )
    return events


def _one(events, etype):
    matches = [e for e in events if e["type"] == etype]
    assert matches, f"no {etype} event in the stream"
    return matches[0]


# ── 1. A simple question stays a single pass, and the trace says why ──────────


async def test_a_simple_question_runs_single_pass_with_no_fanout():
    deps, rec = build_team_deps()
    events = await _drive(deps, SIMPLE_QUERY)

    routing = _one(events, "routing")
    assert routing["depth"] == "single"
    assert routing["fanout"] == 0
    assert routing["decided_by"] == "auto"
    assert "single-intent" in routing["reason"]
    assert not routing["used_llm"], "a short, obvious query must not pay for a model call"

    assert [n for n in events if n["type"] == "node_started" and n["node"] == "run_team"] == []
    assert [c for c in rec.completions if c[0] == "subagent"] == []
    assert _one(events, "run_finished")["status"] == RunStatus.COMPLETED.value


async def test_the_rehearsed_demo_query_classifies_team():
    """Under-firing on the demo query is worse than over-firing: it happens on stage."""
    deps, _ = build_team_deps()
    events = await _drive(deps, DEMO_QUERY)
    routing = _one(events, "routing")
    assert routing["depth"] == "team"
    assert routing["fanout"] == 4
    assert "fanning out" in routing["reason"]


# ── 2. The fan-out is genuinely concurrent ────────────────────────────────────


async def test_a_multipart_question_fans_out_with_interleaved_events():
    """Three lanes, live. The proof is the ORDER: no lane finishes before the next starts."""
    deps, rec = build_team_deps(roster=_roster(3), lane_delay=0.6)
    events = await _drive(deps, DEMO_QUERY)

    beats = [e for e in events if e["type"] == "agent_status" and e["status"] == "started"]
    assert {b["agent_id"] for b in beats} == {"research", "knowledge", "data"}

    # Interleaving: at least one agent starts after another has already started AND
    # before that one is done. Sequential execution cannot produce this ordering.
    order = [
        (e["agent_id"], e["status"])
        for e in events
        if e["type"] == "agent_status" and e["status"] in {"started", "done"}
    ]
    first_agent = order[0][0]
    done_index = next(i for i, (a, s) in enumerate(order) if a == first_agent and s == "done")
    started_before_first_finished = {
        a for a, s in order[:done_index] if s == "started" and a != first_agent
    }
    assert started_before_first_finished, f"lanes ran sequentially: {order}"

    # The launches are staggered, so N agents never hit the gateway as one burst.
    starts = sorted(rec.starts.values())
    assert starts[-1] - starts[0] >= 0.2, "sub-agents launched as a burst"

    # Every sub-agent event carries its identity; supervisor-level events do not.
    for event in events:
        if event["type"] == "agent_status":
            assert event["agent_id"]
    assert _one(events, "synthesis").get("agent_id") is None, (
        "the post-fan-in synthesis must not inherit a lane's identity"
    )


async def test_four_agents_retrieve_the_tenants_chunks_once():
    """Amendment A's supply-side rule, asserted on the call count."""
    deps, rec = build_team_deps(roster=_roster(4))
    await _drive(deps, DEMO_QUERY)
    assert len(rec.retrievals) == 1, (
        f"four agents retrieved {len(rec.retrievals)} times; the run shares ONE pool"
    )


async def test_the_node_returns_one_summed_delta():
    deps, _ = build_team_deps(roster=_roster(3))
    events = await _drive(deps, DEMO_QUERY)
    finished = _one(events, "run_finished")
    run_team = [
        e for e in events if e["type"] == "node_finished" and e["node"] == "run_team"
    ]
    assert len(run_team) == 1
    # 3 lanes × (6, 3) tokens, summed into the single node delta the reducers add.
    assert finished["prompt_tokens"] >= 18
    assert finished["cost_usd"] > 0


# ── 3. The gate: one of it, and a sub-agent can only reach it by proposing ────


async def _propose_high_risk(messages):
    """A lane that wants a HIGH-risk action. It may ask; it may never act."""
    if any(m.get("role") == "tool" for m in messages):
        return LLMResult(
            content="I proposed the status change and could go no further.",
            tool_calls=[],
            usage=Usage(prompt_tokens=4, completion_tokens=2, cost_usd=0.0001),
            model="fake-cheap",
        )
    return LLMResult(
        content="R1 is overdue; it should be resolved.",
        tool_calls=[
            ToolCallResult(id="c1", name="update_request_status",
                           args={"request_id": "R1", "status": "resolved"})
        ],
        usage=Usage(prompt_tokens=6, completion_tokens=3, cost_usd=0.0002),
        model="fake-cheap",
    )


async def test_a_subagent_proposal_gates_and_resumes_through_the_existing_path():
    deps, rec = build_team_deps(
        roster=_roster(3), lane_behaviour={"data": _propose_high_risk}
    )
    events = await _drive(deps, DEMO_QUERY, approve=True)

    required = _one(events, "approval_required")
    assert required["action"] == "update_request_status"
    assert required["risk"] == RiskLevel.HIGH.value
    # The action ran EXACTLY ONCE, and only after the gate resolved.
    assert rec.executed == ["update_request_status"]
    approval_index = events.index(required)
    tool_results = [
        i for i, e in enumerate(events)
        if e["type"] == "tool_result" and e["ok"] and "resolved" in e["summary"]
    ]
    assert tool_results and min(tool_results) > approval_index
    assert _one(events, "run_finished")["status"] == RunStatus.COMPLETED.value


async def test_a_rejected_subagent_proposal_never_executes():
    """A refused gate reports ``rejected``, even though the other lanes did answer.

    This asserted ``COMPLETED`` while ``RunStatus.REJECTED`` did not yet exist and
    every terminal state that was not an error collapsed into one value. It is the
    same contract ``test_rejected_gate_in_a_multi_round_run_is_not_reported_approved``
    pins for a single agent: work happening elsewhere in the run is not evidence that
    the human approved the one action they were asked about. Reporting ``completed``
    here would make a run the operator refused indistinguishable, in the runs list and
    in the audit record, from one that did what it proposed.
    """
    deps, rec = build_team_deps(
        roster=_roster(3), lane_behaviour={"data": _propose_high_risk}
    )
    events = await _drive(deps, DEMO_QUERY, approve=False)
    assert rec.executed == []
    assert _one(events, "run_finished")["status"] == RunStatus.REJECTED.value


async def test_a_gated_team_run_parks_and_resumes_out_of_band():
    """The park/resume machinery is the EXISTING one; a fan-out changes nothing."""
    deps, rec = build_team_deps(
        roster=_roster(3),
        lane_behaviour={"data": _propose_high_risk},
        config=AgentConfig(
            stream_chunk_words=4,
            query_rewrite_enabled=False,
            agentic_retrieval_enabled=False,
            answer_cache_enabled=False,
            max_concurrent_agents=4,
            approval_park_timeout=0.05,
        ),
    )
    parked = ParkedRunRegistry()
    events = await _drive(deps, DEMO_QUERY, run_id="team-park", parked_runs=parked)

    assert _one(events, "run_finished")["status"] == RunStatus.AWAITING_APPROVAL.value
    assert rec.executed == [], "a parked run must not have acted"

    handle = parked.get("team-park")
    assert handle is not None
    assert await resume_parked_run(
        "team-park", ApprovalDecision.APPROVE, graph=handle.graph, config=handle.config
    )
    assert rec.executed == ["update_request_status"], "resume must run it exactly once"


async def test_interrupt_is_never_reached_from_inside_a_gathered_task(monkeypatch):
    """The non-negotiable constraint, asserted at runtime rather than by inspection.

    Every sub-agent lane is a task spawned by ``asyncio.gather`` inside ``run_team``;
    the ``approval`` node is not. So the task that reaches ``interrupt()`` must be the
    one running ``run_team``'s *caller*, never a lane — which is exactly what makes the
    one gate the only path to a consequential action.
    """
    lanes: set[int] = set()
    interrupting: list[int] = []
    real_interrupt = graph_module.interrupt

    async def _tracking_lane(messages):
        lanes.add(id(asyncio.current_task()))
        return await _propose_high_risk(messages)

    def _spy(value):
        interrupting.append(id(asyncio.current_task()))
        return real_interrupt(value)

    monkeypatch.setattr(graph_module, "interrupt", _spy)
    deps, _ = build_team_deps(roster=_roster(3), lane_behaviour={"data": _tracking_lane})
    await _drive(deps, DEMO_QUERY, approve=True)

    assert lanes, "no lane ran; the assertion below would be vacuous"
    assert interrupting, "the gate never interrupted; the assertion below would be vacuous"
    assert not (set(interrupting) & lanes), (
        "interrupt() was reached from inside a gathered sub-agent task"
    )


# ── 4. One lane dying must not take the others with it ───────────────────────


async def test_a_timed_out_agent_is_omitted_and_named_and_siblings_finish():
    async def _hang(_messages):
        await asyncio.sleep(30)
        raise AssertionError("unreachable")  # pragma: no cover

    deps, _ = build_team_deps(
        roster=_roster(3), lane_behaviour={"policy": _hang, "data": _hang}
    )
    # ``data`` is the third roster entry, so use a roster whose third agent hangs.
    deps.subagent_roster = lambda: [
        *_roster(2),
        SubAgentSpec(
            agent_id="policy",
            role="policy",
            label="Policy agent",
            system_prompt="You are the policy agent.",
            timeout_s=0.15,
        ),
    ]
    events = await _drive(deps, DEMO_QUERY)

    synthesis = _one(events, "synthesis")
    assert {a["agent_id"] for a in synthesis["contributing"]} == {"research", "knowledge"}
    assert [a["agent_id"] for a in synthesis["omitted"]] == ["policy"]
    assert synthesis["omitted"][0]["status"] == "timeout"
    assert "2 of 3 agents" in synthesis["summary"]
    assert "policy agent timed out" in synthesis["summary"]

    answer = "".join(e["text"] for e in events if e["type"] == "token")
    assert "2 of 3 agents" in answer, "the omission must be in the ANSWER, not only an event"
    assert _one(events, "run_finished")["status"] == RunStatus.COMPLETED.value


async def test_an_agent_killed_mid_run_leaves_its_siblings_alone():
    async def _killed(_messages):
        raise asyncio.CancelledError

    deps, rec = build_team_deps(roster=_roster(3), lane_behaviour={"knowledge": _killed})
    events = await _drive(deps, DEMO_QUERY)

    synthesis = _one(events, "synthesis")
    assert {a["agent_id"] for a in synthesis["contributing"]} == {"research", "data"}
    assert [a["agent_id"] for a in synthesis["omitted"]] == ["knowledge"]
    assert _one(events, "run_finished")["status"] == RunStatus.COMPLETED.value


async def test_one_agents_model_failure_does_not_cancel_the_others():
    async def _boom(_messages):
        raise RuntimeError("deployment down")

    deps, _ = build_team_deps(roster=_roster(3), lane_behaviour={"research": _boom})
    events = await _drive(deps, DEMO_QUERY)
    synthesis = _one(events, "synthesis")
    assert {a["agent_id"] for a in synthesis["contributing"]} == {"knowledge", "data"}
    assert synthesis["omitted"][0]["status"] == "failed"


# ── 5. The tenant's own budget is still the one refusal ──────────────────────


async def test_budget_exceeded_inside_a_gathered_task_blocks_the_run():
    async def _over_budget(_messages):
        raise BudgetExceededError(
            scope="tenant",
            scope_id=7,
            limit_type="usd_cap",
            limit=10.0,
            used=10.5,
            message="tenant usd cap reached",
        )

    deps, _ = build_team_deps(roster=_roster(3), lane_behaviour={"data": _over_budget})
    events = await _drive(deps, DEMO_QUERY)

    budget = _one(events, "budget_exceeded")
    assert budget["scope"] == "tenant"
    assert budget["limit_type"] == "usd_cap"
    assert _one(events, "run_finished")["status"] == RunStatus.BLOCKED.value


# ── 6. The user's width is the user's decision ───────────────────────────────


async def test_an_explicit_single_is_honoured_and_the_classifier_is_skipped():
    deps, rec = build_team_deps()
    events = await _drive(deps, DEMO_QUERY, depth_mode="single")

    routing = _one(events, "routing")
    assert routing["depth"] == "single"
    assert routing["decided_by"] == "user"
    assert [c for c in rec.completions if c[0] == "depth"] == [], (
        "an explicit width must SKIP the classifier, not be overruled by it after"
    )
    assert [c for c in rec.completions if c[0] == "subagent"] == []


async def test_an_explicit_team_on_a_trivial_query_is_honoured_exactly():
    """The platform does not second-guess somebody spending their own budget."""
    deps, rec = build_team_deps()
    events = await _drive(deps, SIMPLE_QUERY, depth_mode="team", requested_fanout=3)

    routing = _one(events, "routing")
    assert routing["depth"] == "team"
    assert routing["fanout"] == 3
    assert routing["decided_by"] == "user"
    assert len({a for k, a in rec.completions if k == "subagent"}) == 3


async def test_an_explicit_width_above_the_cap_is_narrowed_never_widened():
    deps, _ = build_team_deps(
        roster=_roster(4),
        config=AgentConfig(
            stream_chunk_words=4,
            query_rewrite_enabled=False,
            agentic_retrieval_enabled=False,
            answer_cache_enabled=False,
            max_parallel_agents=2,
            max_concurrent_agents=2,
        ),
    )
    events = await _drive(deps, SIMPLE_QUERY, depth_mode="team", requested_fanout=6)
    routing = _one(events, "routing")
    assert routing["fanout"] == 2
    assert routing["decided_by"] == "platform_cap"
    assert "narrowed" in routing["reason"]


async def test_an_unreadable_mode_defaults_to_single_not_to_the_classifier():
    """The manual path must not introduce a more permissive default than the auto one."""
    deps, rec = build_team_deps()
    events = await _drive(deps, DEMO_QUERY, depth_mode="wide-open")
    routing = _one(events, "routing")
    assert routing["depth"] == "single"
    assert [c for c in rec.completions if c[0] == "subagent"] == []


# ── 7. No roster, no team — and the degradation is loud ──────────────────────


async def test_without_a_subagent_roster_every_turn_is_single_pass():
    deps, rec = build_team_deps()
    deps.subagent_roster = None
    events = await _drive(deps, DEMO_QUERY)
    routing = _one(events, "routing")
    assert routing["depth"] == "single"
    assert routing["decided_by"] == "tenant_default"
    assert "no sub-agent team" in routing["reason"]
    assert [c for c in rec.completions if c[0] == "subagent"] == []


async def test_team_disabled_forces_single_even_on_an_explicit_request():
    deps, _ = build_team_deps(
        config=AgentConfig(
            stream_chunk_words=4,
            query_rewrite_enabled=False,
            agentic_retrieval_enabled=False,
            answer_cache_enabled=False,
            team_enabled=False,
        )
    )
    events = await _drive(deps, DEMO_QUERY, depth_mode="team", requested_fanout=4)
    routing = _one(events, "routing")
    assert routing["depth"] == "single"
    assert routing["decided_by"] == "tenant_default"
