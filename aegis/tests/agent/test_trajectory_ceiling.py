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


def test_the_per_result_ceiling_is_read_by_something() -> None:
    """A declared field that nothing reads is not a bound.

    This build shipped that defect twice before catching it a third time: a read-back
    seam with no production binding, then a memory screen whose callers had no parameter
    to pass it through. Both looked complete from the config surface. So this asserts the
    cheapest possible property — that some module other than the config and the knob list
    actually mentions the field.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    declared_in = {"deps.py", "harness.py"}
    readers = [
        path.name
        for path in (root / "aegis" / "src" / "aegis").rglob("*.py")
        if "max_tool_result_tokens" in path.read_text() and path.name not in declared_in
    ]
    assert readers, (
        "max_tool_result_tokens is declared and exposed as a knob but read by nothing — "
        "a ceiling nothing enforces is a configuration field, not a bound"
    )


def test_an_oversized_tool_result_is_truncated_and_says_so() -> None:
    """Silent truncation is worse than the overflow it prevents.

    A model handed a quietly shortened result reasons confidently about the fragment it
    was given. Told plainly that it is looking at the beginning of something longer, it
    can ask for the rest or qualify its answer. The marker is the whole point.
    """
    from aegis.agent.subagent import _tool_message

    class _Call:
        id = "c1"
        name = "find_requests"

    long = "row data " * 3000
    out = _tool_message(_Call(), long, max_tokens=100)

    assert len(out["content"]) < len(long)
    assert "truncated" in out["content"]
    assert "full text is on the run record" in out["content"]

    # Under the ceiling, nothing is touched.
    assert _tool_message(_Call(), "short", max_tokens=100)["content"] == "short"
    # Disabled, nothing is touched either — the bound is opt-out, not mandatory.
    assert _tool_message(_Call(), long, max_tokens=0)["content"] == long
