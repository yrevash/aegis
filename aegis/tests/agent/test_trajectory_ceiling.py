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
    events_seen: list[object] = []
    result = await run_subagent(
        spec,
        "summarise anything",
        deps=deps,
        persona="operations_lead",
        writer=events_seen.append,
    )

    assert calls["n"] == 0, "the ceiling did not stop the call"
    assert result.status is SubAgentStatus.CEILING
    assert "ceiling" in (result.error or "").lower()

    # THE WIRE, not just the returned object. Passing `writer=lambda _e: None` here is
    # what let a ceiling lane emit `status="done"` for a whole release: the object was
    # correct and nothing ever looked at what the console was actually told. A terminal
    # state the operator cannot see is not a terminal state.
    statuses = [
        e.get("status")
        for e in events_seen
        if isinstance(e, dict) and e.get("type") == "agent_status"
    ]
    assert "ceiling" in statuses, (
        f"a lane cut at its ceiling reported {statuses!r} on the wire — if 'done' is in "
        "there, the console is drawing a truncated lane as a clean one"
    )
    assert "done" not in statuses


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


def test_a_truncated_lane_keeps_what_it_found() -> None:
    """The ceiling branch promises the findings survive the cut. They must.

    They did not: ``CEILING`` is not ``OK``, ``contributed`` tested for ``OK``, and so
    every finding a lane produced before hitting its ceiling was dropped — while the
    error string three lines away said "what it found before that is kept".
    """
    from aegis.agent.subagent import SubAgentResult, SubAgentStatus

    found = SubAgentResult(
        agent_id="a1", role="analyst", label="Analyst",
        status=SubAgentStatus.CEILING,
        findings="Q3 attrition rose 4 points in the Pune site.",
        error="stopped at its trajectory ceiling",
    )
    assert found.contributed, "a truncated lane's findings were thrown away"

    empty = SubAgentResult(
        agent_id="a2", role="analyst", label="Analyst",
        status=SubAgentStatus.CEILING, findings="  ",
    )
    assert not empty.contributed, "a lane with nothing to say must not count"


def test_the_synthesis_names_the_ceiling_either_way() -> None:
    """Whether a truncated lane contributes or not, the reader is told it was cut.

    Both halves are load-bearing and each was wrong at a different moment. Before the
    fix, a truncated lane was omitted and described as having "returned nothing usable"
    — the exact opposite of what a ceiling means. After the first fix it contributed and
    was silently counted among the healthy lanes, so "3 of 3" hid the truncation.
    """
    from aegis.agent.subagent import SubAgentResult, SubAgentStatus
    from aegis.agent.team import TeamOutcome, synthesis_note

    def note_for(findings: str) -> str:
        return synthesis_note(
            TeamOutcome(
                results=[
                    SubAgentResult(
                        agent_id="a1", role="analyst", label="Analyst",
                        status=SubAgentStatus.CEILING,
                        findings=findings,
                        error="stopped at its trajectory ceiling",
                    )
                ]
            )
        )

    with_findings = note_for("Attrition rose 4 points.")
    assert "cut short at its trajectory ceiling" in with_findings
    assert "1 of 1" in with_findings, "it did contribute; the count must say so"

    without = note_for("")
    assert "cut short at its trajectory ceiling" in without
    assert "returned nothing usable" not in without


def test_the_ceiling_measures_hindi_at_its_real_cost() -> None:
    """A bound that silently halves itself in one language is not a bound.

    ``json.dumps`` defaults to ``ensure_ascii=True``, so every Devanagari character became
    a six-byte ``\\uXXXX`` escape before being tokenised. A Hindi lane was measured at over
    twice its real cost and cut at the ceiling while carrying less than half the stated
    bound — well inside any model's context window — and the operator had no way to see
    it, because the configured number and the compared number were not the same number.

    This product ships to India. The assertion is deliberately loose (1.35×) so it tests
    the defect and not the tokeniser's exact behaviour, while still failing hard on the
    2.1–2.6× inflation the escaping caused.
    """
    import json

    from aegis.memory.tokens import count_tokens

    hindi = (
        "कर्मचारी अवकाश नीति के अनुसार अर्जित अवकाश तीस दिन तक अग्रेषित किया जा सकता है। " * 40
    )
    messages = [{"role": "user", "content": hindi}]

    real = count_tokens(hindi)
    measured = count_tokens(json.dumps(messages, ensure_ascii=False, default=str))
    assert real > 0, "the fixture is vacuous"
    assert measured / real < 1.35, (
        f"the ceiling measures Hindi at {measured / real:.2f}x its real cost; a lane "
        "would be cut far below the configured bound with nothing on screen to say so"
    )

    # The anti-vacuity half: prove the OLD expression really was this wrong, so this test
    # cannot pass merely because the fixture is too small to show the difference.
    escaped = count_tokens(json.dumps(messages, default=str))
    assert escaped / real > 1.8, (
        "the fixture no longer demonstrates the escaping blow-up, so it no longer "
        "protects against reintroducing it"
    )


def test_the_per_result_bound_is_shared_by_both_paths() -> None:
    """A lane and the main graph must not disagree about the same ceiling.

    They did. `max_tool_result_tokens` was enforced only inside `run_subagent`, so an
    identical oversized tool result was truncated in a fan-out lane and passed whole on
    the single-agent path — which is the path a demo actually runs, and the path whose
    `results` feed both `verify` and `generate`, so the unbounded text reached the model
    twice.

    Asserting on the shared function rather than on two copies of the logic is the point:
    one implementation cannot drift from itself.
    """
    from pathlib import Path

    from aegis.agent.subagent import bound_result_text

    long = "row data " * 3000
    out = bound_result_text(long, max_tokens=100)
    assert len(out) < len(long)
    assert "truncated" in out
    assert bound_result_text("short", max_tokens=100) == "short"
    assert bound_result_text(long, max_tokens=0) == long

    # And the main graph must actually route through it.
    graph = (
        Path(__file__).resolve().parents[2] / "src" / "aegis" / "agent" / "graph.py"
    ).read_text()
    assert "bound_result_text(" in graph, (
        "the main graph appends tool summaries without the per-result ceiling; a lane "
        "truncates the same result and the single-agent path does not"
    )
