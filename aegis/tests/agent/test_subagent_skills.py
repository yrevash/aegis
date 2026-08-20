"""A skill assigned to one agent reaches that lane's prompt — and no other lane's.

The run assembles ONE working-memory block and hands the same string to every lane, so
that block can only ever carry the main persona's answer to "which skills are in force".
Without the seam these tests drive, a skill written for the research lane would be
advertised to all four lanes and to the main persona besides, which is the opposite of
assigning it to one.

No database and no graph: the seam is a callable on :class:`AgentDeps`, so the claim can
be driven at the unit that owns it — one lane, one loop.
"""

from __future__ import annotations

import pytest

from aegis.agent import AgentConfig, AgentDeps
from aegis.agent.subagent import (
    MAIN_AGENT_ID,
    SKILLS_HEADER_PREFIX,
    SubAgentSpec,
    current_agent_id,
    run_subagent,
)
from aegis.core.types import GuardResult, GuardVerdict, RiskLevel
from aegis.gateway.types import LLMResult, Usage

pytestmark = pytest.mark.anyio

#: A block shaped like the assembler's: a tier the lane must keep, the skills tier it
#: must not, and a tier after it so "strip to the next heading" has something to stop at.
BLOCK = (
    "## Durable facts\n"
    "- prefers email\n"
    "\n"
    f"{SKILLS_HEADER_PREFIX} — call the load_skill tool with a name to read one in full\n"
    "- house_style (platform): the main lane's card\n"
    "\n"
    "## Recent conversation\n"
    "user: hello"
)


def _spec(agent_id: str = "research") -> SubAgentSpec:
    return SubAgentSpec(
        agent_id=agent_id,
        role=agent_id,
        label=f"{agent_id.title()} agent",
        system_prompt="You are a lane.",
        max_steps=1,
        timeout_s=5.0,
    )


def _deps(*, skill_cards_for=None, seen=None):  # noqa: ANN001
    """Deps whose planner records the system prompt it was handed and then stops."""

    async def complete(role, messages, *, tools=None, **_kw):  # noqa: ANN001, ARG001
        if seen is not None:
            seen.append(messages[0]["content"])
        return LLMResult(content="done", tool_calls=[], usage=Usage(), model="fake")

    async def check_input(text: str) -> GuardResult:
        return GuardResult(verdict=GuardVerdict.PASS, reason="clean", text=text)

    async def unreachable(*_a, **_k):  # pragma: no cover - not on this path
        raise AssertionError("not part of the sub-agent path")

    return AgentDeps(
        complete=complete,
        retrieve=unreachable,
        check_input=check_input,
        check_output=unreachable,
        tool_definitions_for=lambda _persona: [],
        run_tool=unreachable,
        tool_risk=lambda _name: RiskLevel.HIGH,
        render_system_prompt=lambda persona, extra_context=None: "sys",  # noqa: ARG005
        config=AgentConfig(),
        skill_cards_for=skill_cards_for,
    )


async def _prompt_for(spec: SubAgentSpec, deps: AgentDeps) -> str:
    seen: list[str] = []
    deps.complete = _deps(seen=seen, skill_cards_for=deps.skill_cards_for).complete
    await run_subagent(
        spec, "a sub-task", deps=deps, persona="operations_lead",
        writer=lambda _payload: None, working_memory=BLOCK,
    )
    return seen[0]


async def test_a_lane_carries_its_own_skills_and_not_the_runs(anyio_backend):
    """The claim, both halves: the lane's card is in, the main lane's card is out.

    MUTATION: drop the ``_strip_skills_section`` call from ``_lane_working_memory`` and
    the second assertion fails — the lane carries a card for a skill assigned to the
    main persona, which is a skill it can neither use nor load.
    """
    del anyio_backend
    asked: list[str] = []

    async def cards(agent_id: str) -> list[str]:
        asked.append(agent_id)
        return ["- citation_rules (platform): how this lane cites a source"]

    prompt = await _prompt_for(_spec("research"), _deps(skill_cards_for=cards))

    assert asked == ["research"], "the lane asked for somebody else's skills"
    assert "citation_rules" in prompt, "the lane's own skill never reached its prompt"
    assert "house_style" not in prompt, "the main persona's skill leaked into the lane"
    # Every other tier the assembler wrote is left exactly where it was.
    assert "## Durable facts" in prompt
    assert "prefers email" in prompt
    assert "## Recent conversation" in prompt


async def test_a_lane_with_no_skills_of_its_own_carries_none(anyio_backend):
    """An empty answer is an answer: the lane drops the section rather than inheriting it."""
    del anyio_backend

    async def cards(_agent_id: str) -> list[str]:
        return []

    prompt = await _prompt_for(_spec("policy"), _deps(skill_cards_for=cards))
    assert "house_style" not in prompt
    assert SKILLS_HEADER_PREFIX not in prompt
    assert "## Durable facts" in prompt


async def test_without_the_seam_a_lane_inherits_the_run_block_unchanged(anyio_backend):
    """The default, and it is today's behaviour byte for byte.

    A host that has not wired ``skill_cards_for`` — every existing test double, and any
    deployment that never assigns a skill to an agent — must see exactly what it saw
    before the seam existed.
    """
    del anyio_backend
    prompt = await _prompt_for(_spec("data"), _deps(skill_cards_for=None))
    assert "house_style" in prompt, "a lane lost the run's skills when nothing was assigned"


async def test_a_failing_skills_read_leaves_the_lane_running(anyio_backend):
    """A skills outage is never why an agent does not run."""
    del anyio_backend

    async def cards(_agent_id: str) -> list[str]:
        raise RuntimeError("the skills store is down")

    prompt = await _prompt_for(_spec("knowledge"), _deps(skill_cards_for=cards))
    assert "house_style" in prompt, "a failed read should degrade to the run's own block"


async def test_the_lane_identity_is_readable_from_the_tool_dispatch(anyio_backend):
    """``load_skill`` has no argument for the lane, so the lane leaves it in the context.

    MUTATION: remove the ``_CURRENT_AGENT.set`` in ``run_subagent`` and this reads
    ``main`` from inside the research lane — which is how a lane would end up loading
    the main persona's skills instead of its own.
    """
    del anyio_backend
    seen: list[str] = []

    async def cards(_agent_id: str) -> list[str]:
        seen.append(current_agent_id())
        return []

    assert current_agent_id() == MAIN_AGENT_ID, "outside a fan-out the lane is main"
    await _prompt_for(_spec("research"), _deps(skill_cards_for=cards))
    assert seen == ["research"]
    assert current_agent_id() == MAIN_AGENT_ID, "the lane's identity outlived the lane"


def test_the_restated_constants_are_the_ones_they_restate():
    """Two strings are copied into ``aegis.agent`` to keep it import-light. Pin both.

    MUTATION: change either copy and this fails, which is the whole point of restating a
    constant rather than importing a heavy module for it.
    """
    from aegis.memory.working import _SKILLS_HEADER
    from aegis.skills.store import MAIN_AGENT_ID as STORE_MAIN

    assert _SKILLS_HEADER.startswith(SKILLS_HEADER_PREFIX), (
        "the lane strips a section header the assembler no longer writes"
    )
    assert MAIN_AGENT_ID == STORE_MAIN
