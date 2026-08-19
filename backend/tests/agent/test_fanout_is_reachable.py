"""Phase 5's fan-out has to be reachable in the SHIPPED application, not only in tests.

The core owns the fan-out mechanism and deliberately ships no sub-agent roster of its
own — a core default would make every host fan out to agents it never declared. The
consequence is that a host which never binds ``AgentDeps.subagent_roster`` has the whole
phase turned off: ``decide_depth``'s ``available_agents`` is 0, the ceiling falls below
``min_fanout``, and **every** turn resolves SINGLE with ``decided_by='tenant_default'``,
whatever the classifier or the user asked for.

That was the shipped state. Every fan-out test injected its own roster, so nothing
noticed. These tests are over the real composition root and the real adapter.
"""

from __future__ import annotations

import pytest
from aegis.agent.router import Depth, DepthMode, DepthPolicy, decide_depth
from aegis.agent.team import build_team

from app.adapter import TOOL_REGISTRY, sub_agent_roster
from app.agent.deps import AgentDeps

pytestmark = pytest.mark.asyncio


async def test_the_composition_root_wires_a_subagent_roster():
    """``build_team`` over the REAL ``AgentDeps.default()``. No roster ⇒ every turn SINGLE."""
    deps = AgentDeps.default()
    assert deps.subagent_roster is not None, (
        "AgentDeps.default() leaves subagent_roster=None, so phase 5's fan-out is "
        "unreachable in the application"
    )
    specs = build_team(deps, deps.config.max_parallel_agents)
    assert len(specs) >= 2, specs
    assert len({s.agent_id for s in specs}) == len(specs), specs


async def test_the_shipped_roster_can_actually_field_the_platform_cap():
    """A roster shorter than ``max_parallel_agents`` silently IS the cap."""
    deps = AgentDeps.default()
    specs = build_team(deps, deps.config.max_parallel_agents)
    assert len(specs) == deps.config.max_parallel_agents, (
        f"the cap is ×{deps.config.max_parallel_agents} but the roster fields "
        f"{len(specs)}, so the widest team a tenant can buy is {len(specs)}"
    )


async def test_a_wide_turn_against_the_real_roster_resolves_team():
    """The end of the chain the missing binding broke: roster → ceiling → TEAM."""
    deps = AgentDeps.default()
    specs = build_team(deps, deps.config.max_parallel_agents)
    decision = await decide_depth(
        "anything",
        policy=DepthPolicy(
            mode=DepthMode.TEAM,
            max_parallel_agents=deps.config.max_parallel_agents,
            available_agents=len(specs),
        ),
    )
    assert decision.depth is Depth.TEAM
    assert decision.fanout == deps.config.max_parallel_agents


async def test_every_tool_a_roster_lane_may_reach_is_a_registered_tool():
    """An unregistered name resolves HIGH, so a typo would silently disarm a lane."""
    for spec in sub_agent_roster():
        unknown = set(spec.tool_allowlist) - set(TOOL_REGISTRY)
        assert not unknown, f"{spec.agent_id} allows unregistered tool(s) {unknown}"


async def test_only_the_lanes_with_a_write_remit_carry_write_tools():
    """Four read-only lanes with the same allowlist would be four routes to one action."""
    with_tools = {s.agent_id for s in sub_agent_roster() if s.tool_allowlist}
    assert with_tools == {"data"}, (
        f"the fan-out hands action tools to {sorted(with_tools)}; a research, knowledge "
        "or policy lane reads and reports"
    )
