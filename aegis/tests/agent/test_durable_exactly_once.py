"""Regression tests for the durable-execution invariants of the gate.

Each test here pins one hole that made "durable interrupt/resume with exactly-once tool
execution" untrue, and each fails on the pre-fix code:

* **A resolution only counts as live when a waiter takes it.** A registered future proves
  a gate exists, not that a live run will consume it; reporting an orphan as a live
  wake-up let a gate be audited APPROVED while the action never ran.
* **A failed resume raises.** Flattening it to ``False`` let the caller skip the
  compensating release and wedge the durable row in ``RESUMING`` forever.
* **Both registries are bounded.** They pin whole run states (a compiled graph plus its
  checkpointer), and several paths never remove an entry.
* **A retried node emits one node_started.** The retry policy sat outside the wrapper
  that emits before the body, so a transient failure produced a phantom, permanently
  unpaired node record.
"""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from aegis.agent import (
    ApprovalOutcome,
    ApprovalRegistry,
    GateHandedOffError,
    ParkedRunRegistry,
    ResumeFailedError,
    resume_parked_run,
    run_agent,
    run_summary,
)
from aegis.core.types import ApprovalDecision, RunStatus


class _Clock:
    """A hand-cranked monotonic clock for the TTL tests."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ── 1. an orphan gate must never be reported as a live wake-up ────────────────


@pytest.mark.asyncio
async def test_orphan_gate_is_not_reported_live_and_is_dropped():
    """A registered-but-never-awaited gate is NOT a live waiter.

    This is the disconnect window: ``run_agent`` registers the notify future and only
    reaches ``wait`` after an await plus three ``yield``s, so a client that goes away in
    between leaves a future present and not done. The pre-fix ``resolve`` returned
    ``True`` for exactly that state, which made the host finalise the row APPROVED, drop
    the parked handle, and leave no resumer able to claim a row that was no longer
    PENDING — an approved gate whose tool never ran.
    """
    registry = ApprovalRegistry()
    registry.register("orphan")

    live = await registry.notify_live(
        "orphan", ApprovalDecision.APPROVE, approver="alice", ack_timeout=0.05
    )

    assert live is False  # nobody took it → the durable resumer owns this decision
    assert registry.pending_ids() == []  # and the orphan future is gone, not leaked


@pytest.mark.asyncio
async def test_bare_resolve_cannot_tell_an_orphan_from_a_live_waiter():
    """Pins the trap the durable decision path used to fall into.

    ``resolve`` is the fire-and-forget form (an in-process caller that also drives the
    stream). It reports ``True`` for an orphan nobody will ever await — which is exactly
    why ``decide_approval`` must use the acknowledged :meth:`notify_live` instead. Kept as
    a test so the two forms can never quietly converge again.
    """
    registry = ApprovalRegistry()
    registry.register("orphan-2")
    assert registry.resolve("orphan-2", ApprovalDecision.APPROVE) is True


@pytest.mark.asyncio
async def test_live_waiter_that_consumes_is_reported_live():
    """The happy path is unchanged: a real waiter acknowledges and reports live."""
    registry = ApprovalRegistry()
    registry.register("gate-1")
    waiter = asyncio.create_task(registry.wait("gate-1"))
    await asyncio.sleep(0)  # let the waiter suspend on the future

    live = await registry.notify_live("gate-1", ApprovalDecision.APPROVE, approver="bob")

    assert live is True
    outcome = await waiter
    assert outcome.approved is True
    assert outcome.approver == "bob"
    assert registry.pending_ids() == []


@pytest.mark.asyncio
async def test_waiter_that_wakes_after_the_handoff_parks_instead_of_executing():
    """Exactly one side proceeds, even in the race the ack window cannot rule out.

    White-box on purpose: this is the sliver where the decision lands, the ack window
    closes with the waiter still suspended (its consuming task stalled or was cancelled),
    and only *then* does the waiter get scheduled. The durable resumer already owns the
    outcome at that point, so the waiter must park rather than execute it — otherwise the
    action runs twice.
    """
    registry = ApprovalRegistry()
    registry.register("gate-2")
    waiter = asyncio.create_task(registry.wait("gate-2"))
    await asyncio.sleep(0)  # the waiter is now suspended on the future

    gate = registry._gates["gate-2"]
    gate.future.set_result(ApprovalOutcome(decision=ApprovalDecision.APPROVE))
    gate.abandoned = True  # what notify_live's ack timeout does
    del registry._gates["gate-2"]

    with pytest.raises(GateHandedOffError):
        await waiter


@pytest.mark.asyncio
async def test_cancelled_waiter_is_not_reported_live():
    """A waiter whose task is cancelled (the SSE request died) is not a live waiter."""
    registry = ApprovalRegistry()
    registry.register("gate-cancel")
    waiter = asyncio.create_task(registry.wait("gate-cancel"))
    await asyncio.sleep(0)

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert registry.pending_ids() == []
    assert (
        await registry.notify_live(
            "gate-cancel", ApprovalDecision.APPROVE, ack_timeout=0.05
        )
        is False
    )


@pytest.mark.asyncio
async def test_parked_gate_decision_is_not_reported_live():
    """Once the socket-held wait times out and the run parks, no live waiter remains."""
    registry = ApprovalRegistry()
    registry.register("gate-park")
    waiter = asyncio.create_task(registry.wait("gate-park", timeout=0.02))
    with pytest.raises(TimeoutError):
        await waiter

    assert (
        await registry.notify_live(
            "gate-park", ApprovalDecision.APPROVE, ack_timeout=0.05
        )
        is False
    )


@pytest.mark.asyncio
async def test_discarding_a_gate_stops_a_later_decision_looking_live():
    """The streaming run's cleanup makes the orphan window explicit."""
    registry = ApprovalRegistry()
    registry.register("gate-3")

    assert registry.discard("gate-3") is True
    assert registry.is_pending("gate-3") is False
    assert (
        await registry.notify_live(
            "gate-3", ApprovalDecision.APPROVE, ack_timeout=0.05
        )
        is False
    )


@pytest.mark.asyncio
async def test_closing_the_stream_at_the_gate_discards_the_notify_future(make_deps):
    """End-to-end: an SSE client that disconnects at the gate leaves no orphan.

    Drives the real ``run_agent`` to ``approval_required`` and then closes the generator
    exactly as a dropped SSE connection does. Pre-fix the future survived the closed
    generator and a decision arriving afterwards reported a live wake-up.
    """
    deps = make_deps(propose_tool=True, high_risk=True)
    registry = ApprovalRegistry()
    parked = ParkedRunRegistry()
    approval_id: str | None = None

    agen = run_agent(
        "resolve R1",
        persona="operations_lead",
        deps=deps,
        registry=registry,
        run_id="run-disconnect",
        parked_runs=parked,
    )
    async for event in agen:
        if event["type"] == "approval_required":
            approval_id = event["approval_id"]
            break
    await agen.aclose()  # the client went away

    assert approval_id is not None
    assert registry.pending_ids() == []  # no orphan future left behind
    # The run is NOT lost: the resumable handle is still parked for the durable resumer.
    assert parked.get("run-disconnect") is not None
    assert (
        await registry.notify_live(
            approval_id, ApprovalDecision.APPROVE, ack_timeout=0.05
        )
        is False
    )


# ── 2. a failed resume must be distinguishable from "nothing to resume" ───────


class _Snapshot:
    def __init__(self, nxt: tuple[str, ...]) -> None:
        self.next = nxt


class _BoomGraph:
    """A compiled-graph stand-in whose headless drive fails mid-flight."""

    def get_state(self, _config):  # noqa: ANN001, ANN201
        return _Snapshot(("act",))

    def astream(self, *_args, **_kwargs):  # noqa: ANN201
        async def _gen():
            raise RuntimeError("worker lost its store mid-resume")
            yield  # pragma: no cover - unreachable, makes this an async generator

        return _gen()


class _EmptyGraph:
    """A compiled-graph stand-in with no resumable step."""

    def get_state(self, _config):  # noqa: ANN001, ANN201
        return _Snapshot(())


@pytest.mark.asyncio
async def test_failed_resume_raises_rather_than_reporting_false():
    """A broken drive must not look like "there was nothing to resume".

    The caller has already won the optimistic ``PENDING → RESUMING`` transition. Pre-fix
    the exception was swallowed and ``False`` returned, so the caller skipped its
    finalise/release and the row sat in ``RESUMING`` forever: unmatched by the decision
    path (``PENDING`` only) and unmatched by the SLA sweeper (``PENDING`` only).
    """
    with pytest.raises(ResumeFailedError):
        await resume_parked_run(
            "run-boom",
            ApprovalDecision.APPROVE,
            graph=_BoomGraph(),
            config={"configurable": {"thread_id": "run-boom"}},
        )


@pytest.mark.asyncio
async def test_absent_checkpoint_still_reports_false():
    """The honest negative keeps its old meaning: nothing to resume is not a failure."""
    assert (
        await resume_parked_run(
            "run-empty",
            ApprovalDecision.APPROVE,
            graph=_EmptyGraph(),
            config={"configurable": {"thread_id": "run-empty"}},
        )
        is False
    )


# ── 3. neither registry may grow without bound ────────────────────────────────


def test_parked_run_registry_evicts_on_ttl():
    """A handle nothing ever pops is evicted rather than pinning a whole run state.

    This is the SLA-sweeper hole: an approval that later EXPIRES or is auto-REJECTED is
    only ever touched as a durable row, so the park path's entry — a compiled LangGraph
    plus its ``InMemorySaver``, i.e. the entire run — was retained forever.
    """
    clock = _Clock()
    parked = ParkedRunRegistry(ttl_seconds=60.0, clock=clock)
    parked.register("run-expired", object(), {"configurable": {"thread_id": "run-expired"}})
    assert parked.get("run-expired") is not None

    clock.advance(61.0)

    assert parked.get("run-expired") is None
    assert parked.ids() == []


def test_parked_run_registry_keeps_live_handles():
    """Eviction is TTL-bounded, not eager: a fresh handle stays resumable."""
    clock = _Clock()
    parked = ParkedRunRegistry(ttl_seconds=60.0, clock=clock)
    parked.register("run-fresh", object(), {})
    clock.advance(30.0)
    parked.register("run-newer", object(), {})

    clock.advance(31.0)  # run-fresh is now 61s old, run-newer only 31s

    assert parked.ids() == ["run-newer"]


@pytest.mark.asyncio
async def test_approval_registry_evicts_unwaited_gates_on_ttl():
    """A gate nobody ever waited on or resolved is evicted, not leaked.

    Left in place it also corrupts ``pending_ids``/``is_pending``, which then report
    gates that nothing is waiting on.
    """
    clock = _Clock()
    registry = ApprovalRegistry(ttl_seconds=60.0, clock=clock)
    registry.register("stale")
    assert registry.is_pending("stale") is True

    clock.advance(61.0)

    assert registry.sweep() == ["stale"]
    assert registry.pending_ids() == []


@pytest.mark.asyncio
async def test_errored_run_drops_its_parked_handle(make_deps):
    """A run that dies after the gate must not keep its resumable handle.

    The budget path already popped; the generic ``except Exception`` path did not, so any
    post-gate failure pinned a compiled graph plus its checkpointer with nothing left to
    resume.
    """
    deps = make_deps(propose_tool=True, high_risk=True)
    original_check = deps.check_output

    async def exploding_check_output(text, contexts=None):  # noqa: ANN001, ANN202
        raise RuntimeError("output guardrail service is down")

    deps.check_output = exploding_check_output
    registry = ApprovalRegistry()
    parked = ParkedRunRegistry()

    types: list[str] = []
    async for event in run_agent(
        "resolve R1",
        persona="operations_lead",
        deps=deps,
        registry=registry,
        run_id="run-error",
        parked_runs=parked,
    ):
        types.append(event["type"])
        if event["type"] == "approval_required":
            registry.resolve(event["approval_id"], ApprovalDecision.APPROVE, approver="al")

    assert "error" in types  # the run really did fail after the gate
    assert types[-1] == "run_finished"
    assert parked.ids() == []
    assert original_check is not None  # the fake was genuinely replaced


# ── 4. a rejected gate is never reported as approved ──────────────────────────


@pytest.mark.asyncio
async def test_rejected_gate_in_a_multi_round_run_is_not_reported_approved(make_deps):
    """A REJECTED gate must read as rejected even when an earlier round executed.

    The reachable path: round 1 proposes a MEDIUM-risk tool (below the HIGH gate) which
    executes and reports failure, ``reflect`` re-plans, and round 2 proposes a HIGH-risk
    tool that the human REJECTS — routing straight to ``generate`` with nothing executed
    after the gate. ``run_summary`` derived ``gate.approved`` from "did ANY tool_result
    appear anywhere in the stream", so round 1's result stood in as evidence and the
    audit record claimed the rejected action was approved.
    """
    deps = make_deps(propose_tool=True, high_risk=False)
    from aegis.core.types import RiskLevel

    executed: list[str] = []

    def escalating_risk(name: str) -> RiskLevel:
        # Round 1 is MEDIUM (waved through); once it has run, round 2 is HIGH (gated).
        return RiskLevel.HIGH if executed else RiskLevel.MEDIUM

    class _Failed:
        ok = False
        summary = "attempt did not resolve the request"

    async def failing_run_tool(persona, name, args, *, actor, model, trace_id, approver):  # noqa: ANN001, ANN202
        executed.append(name)
        return _Failed()

    deps.tool_risk = escalating_risk
    deps.run_tool = failing_run_tool
    deps.config.max_plan_iterations = 2

    registry = ApprovalRegistry()
    events: list[dict] = []
    async for event in run_agent(
        "resolve R1", persona="operations_lead", deps=deps, registry=registry
    ):
        events.append(event)
        if event["type"] == "approval_required":
            registry.resolve(event["approval_id"], ApprovalDecision.REJECT, approver="al")

    types = [e["type"] for e in events]
    # The scenario really is the multi-round one: a tool ran BEFORE the gate…
    assert executed == ["update_request_status"]
    assert types.index("tool_result") < types.index("approval_required")
    # …and nothing ran after it, because the human rejected.
    assert types[types.index("approval_required") :].count("tool_result") == 0

    gate = run_summary(events)["gate"]
    assert gate["gated"] is True
    assert gate["resolved"] is True
    assert gate["approved"] is False


# ── 5. a retried node emits exactly one node_started ──────────────────────────


@pytest.mark.asyncio
async def test_transient_node_failure_emits_one_node_started(make_deps):
    """A retried node stays one node execution on the wire.

    ``_timed`` emits ``node_started`` BEFORE the body, so wiring the retry policy at the
    node level (which re-invokes the whole wrapper) produced a second ``node_started`` for
    one logical execution — and ``run_summary`` folded that into an extra node record that
    could never be paired with a ``node_finished``, permanently stuck at
    ``duration_ms: None``. Retrying the body inside the wrapper keeps the pair 1:1.
    """
    deps = make_deps(propose_tool=False, high_risk=False)
    original_complete = deps.complete
    failures = {"n": 0}

    async def flaky_complete(role, messages, *, tools=None, **kwargs):  # noqa: ANN001, ANN003
        # Only the planner's call (the one carrying tools) fails, and only once.
        if tools is not None and failures["n"] == 0:
            failures["n"] += 1
            raise ConnectionError("gateway blipped")
        return await original_complete(role, messages, tools=tools, **kwargs)

    deps = dataclasses.replace(deps, complete=flaky_complete)
    deps.config.max_plan_iterations = 1

    events = [
        e
        async for e in run_agent(
            "resolve R1", persona="operations_lead", deps=deps, registry=ApprovalRegistry()
        )
    ]

    assert failures["n"] == 1  # the transient failure really happened
    assert events[-1]["status"] == RunStatus.COMPLETED.value  # and was retried through

    plan_started = [
        e for e in events if e["type"] == "node_started" and e["node"] == "plan"
    ]
    plan_finished = [
        e for e in events if e["type"] == "node_finished" and e["node"] == "plan"
    ]
    assert len(plan_started) == 1
    assert len(plan_finished) == 1

    # The folded record has no phantom, unpaired node entry.
    summary = run_summary(events)
    assert [n["node"] for n in summary["nodes"] if n["duration_ms"] is None] == []
