"""Unit-level guarantees of the fan-out: the shared pool, the roster read, the merge.

These are the pieces the end-to-end fan-out tests rest on, pinned on their own so a
failure says *which* piece broke rather than "the team run looks wrong".
"""

from __future__ import annotations

import asyncio

import pytest

from aegis.agent import AgentConfig, AgentDeps, SubAgentSpec
from aegis.agent.subagent import SubAgentResult, SubAgentStatus
from aegis.agent.team import (
    SharedRetrievalPool,
    TeamOutcome,
    TeamTask,
    build_team,
    plan_team_tasks,
    run_team,
    synthesis_note,
)
from aegis.core.types import RiskLevel
from aegis.gateway.types import LLMResult, Usage
from aegis.retrieval.models import GraphDelta, RetrievalResult, Source

pytestmark = pytest.mark.anyio


def _deps(*, roster=None, complete=None, retrieve=None, config=None) -> AgentDeps:
    async def unreachable(*_a, **_k):
        raise AssertionError("not part of this test's path")

    return AgentDeps(
        complete=complete or unreachable,
        retrieve=retrieve or unreachable,
        check_input=unreachable,
        check_output=unreachable,
        tool_definitions_for=lambda _p: [],
        run_tool=unreachable,
        tool_risk=lambda _n: RiskLevel.LOW,
        render_system_prompt=lambda persona, extra_context=None: "sys",  # noqa: ARG005
        config=config or AgentConfig(),
        subagent_roster=(lambda: roster) if roster is not None else None,
    )


def _spec(role: str, **kw) -> SubAgentSpec:
    return SubAgentSpec(
        agent_id=role, role=role, label=f"{role.title()} agent",
        system_prompt=f"You are {role}.", **kw
    )


# ── The shared retrieval pool: one retrieval per run, N readers ──────────────


async def test_the_pool_retrieves_once_however_many_concurrent_readers():
    calls: list[str] = []

    async def retrieve(query, *, scope):  # noqa: ANN001, ARG001
        calls.append(query)
        await asyncio.sleep(0.02)  # a real retrieval is slow enough to race
        return RetrievalResult(
            answer_context="corpus",
            sources=[Source(id="s", text="t", score=1.0)],
            num_candidates=1,
            graph_delta=GraphDelta(nodes=[], edges=[]),
            cache_hit=False,
        )

    pool = SharedRetrievalPool(_deps(retrieve=retrieve), "q", scope=None)
    contexts = await asyncio.gather(*(pool.context() for _ in range(8)))

    assert calls == ["q"], f"the pool retrieved {len(calls)} times for one run"
    assert set(contexts) == {"corpus"}
    assert pool.calls == 1


async def test_a_failing_pool_degrades_to_empty_context_not_a_failed_run():
    async def retrieve(query, *, scope):  # noqa: ANN001, ARG001
        raise RuntimeError("vector store down")

    pool = SharedRetrievalPool(_deps(retrieve=retrieve), "q", scope=None)
    assert await pool.context() == ""
    assert await pool.context() == "", "a failed pool must not retry per reader either"


# ── The roster is host data, read defensively ────────────────────────────────


def test_no_roster_hook_means_no_team():
    assert build_team(_deps(), 4) == []


def test_a_raising_roster_hook_means_no_team_rather_than_a_failed_run():
    deps = _deps(roster=[])
    deps.subagent_roster = lambda: (_ for _ in ()).throw(RuntimeError("bad wiring"))
    assert build_team(deps, 4) == []


def test_the_roster_is_truncated_to_the_requested_width():
    deps = _deps(roster=[_spec(r) for r in ("research", "knowledge", "data", "policy")])
    assert [s.role for s in build_team(deps, 2)] == ["research", "knowledge"]


def test_a_roster_entry_cannot_grant_itself_more_than_the_platform_allows():
    """Bounds are the tenant's config, never the roster's — a roster is host data."""
    deps = _deps(
        roster=[_spec("research", max_steps=99, timeout_s=999.0)],
        config=AgentConfig(subagent_max_steps=3, subagent_timeout_s=10.0),
    )
    spec = build_team(deps, 4)[0]
    assert spec.max_steps == 3
    assert spec.timeout_s == 10.0


def test_a_duck_typed_roster_entry_is_accepted_and_a_broken_one_is_skipped():
    class _Entry:
        role = "research"
        label = "Research"
        system_prompt = "go"
        tool_allowlist = ("add_case_note",)

    class _Broken:
        @property
        def role(self):
            raise RuntimeError("unreadable roster row")

    deps = _deps(roster=[_Entry(), _Broken()])
    specs = build_team(deps, 4)
    assert [s.role for s in specs] == ["research"]
    assert specs[0].tool_allowlist == frozenset({"add_case_note"})


# ── Task planning degrades to a working team, never a broken one ─────────────


async def test_a_failed_task_planner_gives_every_agent_the_whole_query():
    async def boom(*_a, **_k):
        raise RuntimeError("cheap model down")

    specs = [_spec("research"), _spec("knowledge")]
    tasks = await plan_team_tasks("the question", specs, deps=_deps(complete=boom))
    assert [t.task for t in tasks] == ["the question", "the question"]


async def test_a_short_planner_reply_is_a_non_answer_not_a_partial_team():
    async def one_line(*_a, **_k):
        return LLMResult(content="only one line", tool_calls=[], usage=Usage(), model="m")

    specs = [_spec("research"), _spec("knowledge")]
    tasks = await plan_team_tasks("the question", specs, deps=_deps(complete=one_line))
    assert [t.task for t in tasks] == ["the question", "the question"]


# ── The fan-out never cancels a sibling ──────────────────────────────────────


async def test_return_exceptions_keeps_a_sibling_alive_when_one_lane_explodes():
    finished: list[str] = []

    async def complete(role, messages, **_kw):  # noqa: ANN001, ARG001
        system = messages[0]["content"]
        if "You are boom." in system:
            raise RuntimeError("this lane is on fire")
        await asyncio.sleep(0.05)
        finished.append("ok-lane")
        return LLMResult(content="found it", tool_calls=[], usage=Usage(), model="m")

    deps = _deps(complete=complete, config=AgentConfig(max_concurrent_agents=4))
    tasks = [
        TeamTask(spec=_spec("boom"), task="t"),
        TeamTask(spec=_spec("fine"), task="t"),
    ]
    outcome = await run_team(tasks, deps=deps, persona="p", writer=lambda _e: None)

    assert finished == ["ok-lane"], "a sibling was cancelled by another lane's failure"
    by_id = {r.agent_id: r for r in outcome.results}
    assert by_id["boom"].status is SubAgentStatus.FAILED
    assert by_id["fine"].status is SubAgentStatus.OK


async def test_an_empty_task_list_is_an_empty_outcome_not_a_crash():
    outcome = await run_team([], deps=_deps(), persona="p", writer=lambda _e: None)
    assert outcome.results == []
    assert outcome.totals() == {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0}


# ── The merge names its omissions ────────────────────────────────────────────


def _result(agent_id: str, status: SubAgentStatus, findings: str = "", error=None):
    return SubAgentResult(
        agent_id=agent_id, role=agent_id, label=f"{agent_id.title()} agent",
        status=status, findings=findings, error=error,
    )


def test_the_note_says_three_of_four_and_names_who_did_not_make_it():
    outcome = TeamOutcome(
        results=[
            _result("research", SubAgentStatus.OK, "found A"),
            _result("knowledge", SubAgentStatus.OK, "found B"),
            _result("data", SubAgentStatus.OK, "found C"),
            _result("policy", SubAgentStatus.TIMEOUT, error="timed out after 45s"),
        ]
    )
    note = synthesis_note(outcome)
    assert "3 of 4 agents" in note
    assert "policy agent timed out" in note
    assert "45s" in note


def test_an_agent_that_returned_nothing_usable_is_omitted_not_counted():
    outcome = TeamOutcome(
        results=[
            _result("research", SubAgentStatus.OK, "found A"),
            _result("knowledge", SubAgentStatus.OK, "   "),
        ]
    )
    assert [r.agent_id for r in outcome.omitted] == ["knowledge"]
    assert "1 of 2 agents" in synthesis_note(outcome)


def test_totals_sum_every_lane_including_the_ones_that_failed():
    a = _result("a", SubAgentStatus.OK, "x")
    a.prompt_tokens, a.completion_tokens, a.cost_usd = 10, 4, 0.01
    b = _result("b", SubAgentStatus.FAILED, error="down")
    b.prompt_tokens, b.completion_tokens, b.cost_usd = 3, 0, 0.002
    outcome = TeamOutcome(results=[a, b])
    assert outcome.totals() == {
        "prompt_tokens": 13,
        "completion_tokens": 4,
        "cost_usd": pytest.approx(0.012),
    }
