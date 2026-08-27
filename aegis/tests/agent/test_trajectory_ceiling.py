"""A run has a stated bound on its own trajectory, and it is enforced.

Aegis has no trajectory compaction: nothing summarises, evicts or budgets a run's own turn
history. The memory subsystem is excellent and governs *the store, across turns* — it never
sees what one run accumulates inside itself.

Until compaction exists, the honest answer to "what happens on a very long run" is a bound
somebody chose and wrote down, not a shrug. That is what this tests. A lane that stops at a
stated ceiling and keeps what it found is a different outcome from one that dies at a
context-window error nobody predicted, and the difference is entirely in whether the limit
was designed.

The ceiling is checked BEFORE the model call, not after: checking after would mean paying
for the very call the bound exists to avoid.
"""

from __future__ import annotations

import dataclasses

import pytest

from aegis.agent.subagent import SubAgentStatus


def test_the_ceiling_is_a_configured_field_with_a_recorded_provenance() -> None:
    """The number is a decision, not a constant that appeared.

    A ceiling whose origin is unwritten becomes folklore: nobody dares raise it because
    nobody knows what it was based on. The docstring carries the measurement and — just
    as importantly — the sample size behind it.
    """
    from aegis.agent.deps import AgentConfig

    cfg = AgentConfig()
    assert cfg.max_trajectory_tokens > 0
    assert cfg.max_tool_result_tokens > 0
    # The per-result bound must be well under the whole-trajectory bound, or it is not
    # bounding the thing that actually bites first.
    assert cfg.max_tool_result_tokens < cfg.max_trajectory_tokens

    source = AgentConfig.__doc__ or ""
    fields = __import__("inspect").getsource(AgentConfig)
    assert "11,859" in fields or "11859" in fields, (
        "the ceiling's measured basis is not recorded beside it"
    )
    assert "SAMPLES" in fields.upper(), (
        "the sample size behind the measurement is not stated; a thin measurement "
        "presented without its n reads as calibration"
    )


def test_ceiling_is_a_terminal_state_and_not_an_error() -> None:
    """`CEILING` sits beside `TIMEOUT`, deliberately.

    Both are designed ends: the lane stops, what it found is kept, and the synthesis
    names it as cut short. Modelling this as an error would make graceful degradation
    look like a fault, and a demo that shows a fault is a demo about a bug.
    """
    assert SubAgentStatus.CEILING.value == "ceiling"
    assert SubAgentStatus.CEILING is not SubAgentStatus.FAILED


def test_the_ceiling_is_exposed_as_a_tunable_knob() -> None:
    """It has to be adjustable, and adjustable within stated bounds.

    A hard-coded ceiling is a ceiling nobody can raise for a workload that legitimately
    needs more, so it gets removed instead of raised.
    """
    from aegis.agent.harness import harness_config

    keys = {k["key"] for k in harness_config()["knobs"]}
    assert "max_trajectory_tokens" in keys
    assert "max_tool_result_tokens" in keys


@pytest.mark.asyncio
async def test_a_lane_over_the_ceiling_stops_before_it_calls_the_model(make_deps) -> None:
    """The bound fires, and it fires without paying for the call.

    A ceiling checked after the call would still have spent the tokens it existed to
    protect, which is the whole point missed. Set the ceiling to 1 token so any real
    trajectory exceeds it, then assert the model was never reached.
    """
    from aegis.agent.subagent import run_subagent, SubAgentSpec

    deps = make_deps(propose_tool=False)
    calls = {"n": 0}

    async def counting_complete(role, messages, **kw):  # noqa: ANN001, ANN003
        calls["n"] += 1
        raise AssertionError("the model was called despite the trajectory ceiling")

    deps = dataclasses.replace(
        deps,
        complete=counting_complete,
        config=dataclasses.replace(deps.config, max_trajectory_tokens=1),
    )

    spec = SubAgentSpec(
        agent_id="a1", role="analyst", label="Analyst",
        system_prompt="You are an analyst.",
    )
    result = await run_subagent(
        spec,
        "summarise anything",
        deps=deps,
        persona="operations_lead",
        writer=lambda _e: None,
    )

    assert calls["n"] == 0, "the ceiling did not stop the call"
    assert result.status is SubAgentStatus.CEILING
    assert "ceiling" in (result.error or "").lower()
