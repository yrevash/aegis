"""The fan-out's bounds and the one refusal the platform makes.

Everything here is a defect an audit found in phase 5 that no test covered, and every
test is written so that reverting its fix turns it red:

* the tenant's budget cap is a **decision**, not a transient fault — it is never retried,
  and it survives the team's wall clock rather than being erased by it;
* the wall clock itself has coverage at all (deleting it used to leave the suite green);
* the shipped bounds fit inside the wall clock, so a saturated team reports the timeouts
  it actually had rather than wall-clock cancellations;
* every model call the router and the team planner make is charged to the run;
* an explicit width is never widened, and ``×0`` is not a full team;
* a duplicated roster ``agent_id`` does not collapse two lanes into one counted twice.
"""

from __future__ import annotations

import asyncio
import math

import pytest

from aegis.agent import AgentConfig, SubAgentSpec
from aegis.agent.retry import transient_only
from aegis.agent.router import Depth, DepthMode, DepthPolicy, decide_depth
from aegis.agent.team import _STAGGER_S
from aegis.core.types import RunStatus
from aegis.gateway.types import BudgetExceededError, LLMResult, Usage

from .test_team_fanout import DEMO_QUERY, _drive, _one, _roster, build_team_deps

pytestmark = pytest.mark.anyio


def _cap() -> BudgetExceededError:
    return BudgetExceededError(
        scope="tenant",
        scope_id=7,
        limit_type="usd_cap",
        limit=10.0,
        used=10.5,
        message="tenant usd cap reached",
    )


def _bounded(**overrides) -> AgentConfig:
    base = {
        "stream_chunk_words": 4,
        "query_rewrite_enabled": False,
        "agentic_retrieval_enabled": False,
        "answer_cache_enabled": False,
        "max_concurrent_agents": 4,
    }
    base.update(overrides)
    return AgentConfig(**base)


async def _hang(_messages):
    await asyncio.sleep(30)
    raise AssertionError("unreachable")  # pragma: no cover


# ── The cap is a refusal, not a fault ────────────────────────────────────────


def test_the_model_retry_policy_refuses_to_retry_a_budget_cap():
    """``default_retry_on`` is a DENY list: it says ``True`` to everything it does not name.

    So the policy the graph's model nodes and every sub-agent call run under cannot be
    LangGraph's stock one. Asserted on the predicate the package actually installs.
    """
    assert transient_only(_cap()) is False
    # And it is still a retry policy: a genuinely transient failure is still retried.
    assert transient_only(ConnectionError("gateway reset")) is True


async def test_the_tenant_cap_is_not_retried_inside_a_lane():
    """A hard governance refusal must not be spent against three more times, per lane."""
    attempts: list[int] = []

    async def _over_budget(_messages):
        attempts.append(1)
        raise _cap()

    deps, _ = build_team_deps(
        roster=_roster(2), lane_behaviour={"research": _over_budget}
    )
    await _drive(deps, DEMO_QUERY)
    assert len(attempts) == 1, (
        f"the tenant cap was retried {len(attempts)} times; a cap is not transient"
    )


async def test_budget_exceeded_survives_the_teams_wall_clock():
    """One lane hits the cap; a sibling outlives the wall clock.

    The cap must still terminate the run as ``blocked``. It is the ONE refusal the
    platform makes, and a wall clock is not permission to spend past it.
    """

    async def _over_budget(_messages):
        raise _cap()

    deps, _ = build_team_deps(
        roster=_roster(2),
        lane_behaviour={"research": _over_budget, "knowledge": _hang},
        config=_bounded(team_wall_clock_s=0.6, subagent_timeout_s=30.0),
    )
    events = await _drive(deps, DEMO_QUERY)
    assert [e for e in events if e["type"] == "budget_exceeded"], (
        "the tenant cap was raised inside a lane and vanished with the wall clock"
    )
    assert _one(events, "run_finished")["status"] == RunStatus.BLOCKED.value


# ── The wall clock exists, and it is the backstop rather than the deadline ───


async def test_the_team_wall_clock_cuts_a_lane_that_outlives_it():
    """Coverage for the wall clock itself: deleting it used to leave the suite green.

    ``policy`` is given a per-lane clock far longer than the team's, so only the team's
    can end it. Its siblings still land, and the cut lane is NAMED rather than absent.
    """
    deps, _ = build_team_deps(
        roster=[
            *_roster(2),
            SubAgentSpec(
                agent_id="policy",
                role="policy",
                label="Policy agent",
                system_prompt="You are the policy agent.",
                timeout_s=3.0,
            ),
        ],
        lane_behaviour={"policy": _hang},
        # The lane's own clock is three times the team's, so ONLY the team's can end it:
        # delete the wall clock and this lane reports ``timeout`` instead, three seconds
        # later. That is the coverage the wall clock did not have.
        config=_bounded(subagent_timeout_s=3.0, team_wall_clock_s=1.0),
    )
    events = await _drive(deps, DEMO_QUERY)
    synthesis = _one(events, "synthesis")
    assert {a["agent_id"] for a in synthesis["contributing"]} == {"research", "knowledge"}
    assert [a["agent_id"] for a in synthesis["omitted"]] == ["policy"]
    assert synthesis["omitted"][0]["status"] == "cancelled"
    assert "wall clock" in synthesis["omitted"][0]["reason"]
    assert _one(events, "run_finished")["status"] == RunStatus.COMPLETED.value


def test_the_default_team_wall_clock_fits_its_own_per_lane_bounds():
    """max_parallel/max_concurrent waves × per-lane timeout must fit the wall clock.

    A team clock BELOW the sum of the bounds it is supposed to backstop cuts lanes
    before their own clocks fire, so the synthesis says "was cut short" about an agent
    that in fact timed out — a designed terminal state reported as the wrong one.
    """
    c = AgentConfig()
    waves = math.ceil(c.max_parallel_agents / c.max_concurrent_agents)
    worst = waves * c.subagent_timeout_s + _STAGGER_S * (c.max_parallel_agents - 1)
    assert c.team_wall_clock_s >= worst, (
        f"{c.max_parallel_agents} lanes at {c.max_concurrent_agents} concurrent = "
        f"{waves} waves × {c.subagent_timeout_s}s + "
        f"{_STAGGER_S * (c.max_parallel_agents - 1)}s stagger = {worst}s worst case, "
        f"but team_wall_clock_s is {c.team_wall_clock_s}s"
    )


async def test_a_saturated_team_reports_timeouts_not_wall_clock_cancellations():
    """Every lane hangs and the fourth waits for a slot; all four must report ``timeout``.

    The wall clock is derived from the same invariant the test above pins, rather than
    hardcoded, so this scenario and that arithmetic cannot drift apart.
    """
    parallel, concurrent, per_lane = 4, 3, 0.4
    waves = math.ceil(parallel / concurrent)
    wall = waves * per_lane + _STAGGER_S * (parallel - 1)

    deps, _ = build_team_deps(
        roster=_roster(4),
        lane_behaviour=dict.fromkeys(
            ("research", "knowledge", "data", "policy"), _hang
        ),
        config=_bounded(
            max_parallel_agents=parallel,
            max_concurrent_agents=concurrent,
            subagent_timeout_s=per_lane,
            team_wall_clock_s=wall,
        ),
    )
    events = await _drive(deps, DEMO_QUERY)
    omitted = {a["agent_id"]: a["status"] for a in _one(events, "synthesis")["omitted"]}
    assert set(omitted.values()) == {"timeout"}, (
        f"a lane was cut by the team wall clock rather than by its own: {omitted}"
    )


# ── Every model call the run makes is charged to the run ─────────────────────


async def test_the_team_planners_model_call_is_charged_to_the_run():
    """``plan_team_tasks`` spends a cheap call per team turn; the run must report it."""
    deps, _ = build_team_deps(roster=_roster(2))
    inner = deps.complete

    async def complete(role, messages, **kwargs):  # noqa: ANN001, ANN003
        system = messages[0]["content"] if messages else ""
        if "You split a request into independent sub-tasks" in system:
            return LLMResult(
                content="sub-task 1\nsub-task 2",
                tool_calls=[],
                usage=Usage(prompt_tokens=1000, completion_tokens=500, cost_usd=1.25),
                model="fake-cheap",
            )
        return await inner(role, messages, **kwargs)

    deps.complete = complete
    events = await _drive(deps, DEMO_QUERY)
    finished = _one(events, "run_finished")
    assert finished["prompt_tokens"] >= 1000, (
        f"planner spent 1000 prompt tokens; the run reports {finished['prompt_tokens']}"
    )
    assert finished["cost_usd"] >= 1.25


async def test_the_depth_classifiers_model_call_is_charged_to_the_run():
    """The width classifier's call is the tenant's money too, even when it says SINGLE."""
    deps, _ = build_team_deps(roster=_roster(2))
    inner = deps.complete

    async def complete(role, messages, **kwargs):  # noqa: ANN001, ANN003
        system = messages[0]["content"] if messages else ""
        if "You size a query" in system:
            return LLMResult(
                content="1",
                tool_calls=[],
                usage=Usage(prompt_tokens=800, completion_tokens=1, cost_usd=0.75),
                model="fake-cheap",
            )
        return await inner(role, messages, **kwargs)

    deps.complete = complete
    # >= 20 words with no multi-part marker: the ambiguous band, which is the only one
    # that spends the classifier's call.
    query = " ".join(["please", "consider"] * 15)
    events = await _drive(deps, query)
    assert _one(events, "routing")["used_llm"], "the classifier never ran; test vacuous"
    finished = _one(events, "run_finished")
    assert finished["prompt_tokens"] >= 800, finished
    assert finished["cost_usd"] >= 0.75


# ── The classifier is bounded, and it never widens what the user asked for ───


async def test_a_hanging_depth_classifier_still_defaults_to_single():
    """'SINGLE on every failure path' has to include the model that never answers."""

    async def _never_answers(role, messages, **kwargs):  # noqa: ANN001, ANN003, ARG001
        await asyncio.sleep(30)
        raise AssertionError("unreachable")  # pragma: no cover

    query = " ".join(["please", "consider"] * 15)
    decision = await asyncio.wait_for(
        decide_depth(query, policy=DepthPolicy(), complete=_never_answers), timeout=5.0
    )
    assert decision.depth is Depth.SINGLE


async def test_an_explicit_width_is_never_widened_by_the_platform():
    """Amendment A: the platform may narrow a user's width, never widen it."""
    policy = DepthPolicy(mode=DepthMode.TEAM, requested_fanout=1, max_parallel_agents=4)
    decision = await decide_depth("anything", policy=policy)
    assert decision.fanout <= 1, (
        f"user asked for ×1 and the platform fanned out to ×{decision.fanout} "
        f"as decided_by={decision.decided_by!r}: {decision.reason}"
    )
    assert decision.depth is Depth.SINGLE
    assert decision.decided_by == "user"


async def test_an_explicit_zero_width_is_not_a_full_team():
    """``requested_fanout or ceiling`` turned the narrowest request into the widest team."""
    policy = DepthPolicy(mode=DepthMode.TEAM, requested_fanout=0, max_parallel_agents=4)
    decision = await decide_depth("anything", policy=policy)
    assert decision.fanout == 0 or decision.depth is Depth.SINGLE, (
        f"user asked for ×0 and got ×{decision.fanout}: {decision.reason}"
    )


async def test_team_mode_with_no_explicit_width_still_gets_the_platform_default():
    """The mirror image, so the two fixes above cannot pass by disabling Team mode."""
    policy = DepthPolicy(mode=DepthMode.TEAM, requested_fanout=None, max_parallel_agents=4)
    decision = await decide_depth("anything", policy=policy)
    assert decision.depth is Depth.TEAM
    assert decision.fanout == 4
    assert decision.decided_by == "user"


# ── A malformed roster row is never silent ───────────────────────────────────


async def test_a_duplicate_roster_agent_id_does_not_collapse_two_lanes_into_one():
    """Two roster rows sharing an id must not become one lane, credited twice."""
    roster = [
        SubAgentSpec(
            agent_id="dup",
            role="research",
            label="Research agent",
            system_prompt="You are the research agent.",
        ),
        SubAgentSpec(
            agent_id="dup",
            role="knowledge",
            label="Knowledge agent",
            system_prompt="You are the knowledge agent.",
        ),
    ]
    deps, _ = build_team_deps(roster=roster)
    events = await _drive(deps, DEMO_QUERY)
    synthesis = _one(events, "synthesis")
    labels = [a["label"] for a in synthesis["contributing"]]
    assert len(set(labels)) == len(labels) == 2, (
        f"the synthesis credits the same lane twice: {labels}"
    )
    # Both remits actually ran: the first row's is what used to be discarded.
    assert {a["role"] for a in synthesis["contributing"]} == {"research", "knowledge"}
