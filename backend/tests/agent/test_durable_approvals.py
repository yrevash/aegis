"""Durable park + async-resume tests for the orchestrator (fakes + SQLite only).

These prove the production HITL path: a gated run parks (worker returns) as a
persisted PENDING row + checkpoint, an admin resolves it out-of-band through the
shared :func:`app.agent.decide_approval` path, and the run resumes idempotently —
the tool executing exactly once even under a double decision. Also asserts the
``provenance`` event is emitted from the retrieve node.
"""

from __future__ import annotations

import pytest

from app.agent import ApprovalRegistry, decide_approval, get_parked_runs, run_agent
from app.api.schemas import ApprovalDecision, RunStatus
from app.data import get_approval, list_pending

pytestmark = pytest.mark.asyncio


def _spy(deps):
    """Wrap ``deps.run_tool`` with a call counter; return the counter list."""
    executed: list[int] = []
    original = deps.run_tool

    async def spy_run_tool(*args, **kwargs):
        executed.append(1)
        return await original(*args, **kwargs)

    deps.run_tool = spy_run_tool
    return executed


def _park_deps(make_deps, **kw):
    """Build fake deps whose live gate parks almost immediately (tiny timeout)."""
    # Risk-driven gate: a HIGH-risk tool pauses for a human (ML never gates).
    deps = make_deps(propose_tool=True, high_risk=True, **kw)
    deps.config.approval_park_timeout = 0.05
    return deps


async def _run_to_park(deps, registry, run_id):
    """Drive a gated run until it parks; return (event types, approval_id)."""
    types: list[str] = []
    approval_id: str | None = None
    async for ev in run_agent(
        "resolve R1", persona="operations_lead", deps=deps, registry=registry, run_id=run_id
    ):
        types.append(ev.type)
        if ev.type == "approval_required":
            approval_id = ev.approval_id
    return types, approval_id


async def test_gate_parks_with_durable_row_and_queued_event(db, make_deps):
    deps = _park_deps(make_deps)
    reg = ApprovalRegistry()
    types, approval_id = await _run_to_park(deps, reg, "run-park")

    # The park ends the stream at the gate with awaiting_approval.
    assert "approval_queued" in types
    assert "approval_required" in types
    assert types[-1] == "run_finished"

    # The durable row is the source of truth and survives the parked socket.
    rows = await list_pending()
    row = next(r for r in rows if r.run_id == "run-park")
    assert row.id == approval_id
    assert row.status == "pending"
    assert row.action == "update_request_status"
    assert row.sla_deadline is not None

    # A resumable checkpoint handle is retained for the async resumer.
    assert get_parked_runs().get("run-park") is not None
    get_parked_runs().pop("run-park")  # tidy the process-wide singleton


async def test_parked_run_finishes_awaiting_approval(db, make_deps):
    deps = _park_deps(make_deps)
    events = [
        e
        async for e in run_agent(
            "resolve R1", persona="operations_lead", deps=deps,
            registry=ApprovalRegistry(), run_id="run-status",
        )
    ]
    assert events[-1].status is RunStatus.AWAITING_APPROVAL
    get_parked_runs().pop("run-status")


async def test_inbox_decision_resumes_and_executes_exactly_once(db, make_deps):
    executed: list[int] = []
    deps = _park_deps(make_deps)
    original = deps.run_tool

    async def spy_run_tool(*args, **kwargs):
        executed.append(1)
        return await original(*args, **kwargs)

    deps.run_tool = spy_run_tool
    reg = ApprovalRegistry()
    _types, approval_id = await _run_to_park(deps, reg, "run-resume")
    assert executed == []  # parked: nothing has executed yet

    # Admin resolves out-of-band through the shared path → resumes from checkpoint.
    result = await decide_approval(
        approval_id, ApprovalDecision.APPROVE, approver="alice", registry=reg
    )
    assert result.accepted is True
    assert result.status == "approved"
    assert executed == [1]  # resumed and executed exactly once

    # Idempotency: a second decision is a no-op — no double execute.
    again = await decide_approval(
        approval_id, ApprovalDecision.APPROVE, approver="bob", registry=reg
    )
    assert again.accepted is False
    assert executed == [1]

    row = await get_approval(approval_id)
    assert row is not None and row.status == "approved"


async def test_inbox_reject_skips_execution(db, make_deps):
    executed: list[int] = []
    deps = _park_deps(make_deps)
    original = deps.run_tool

    async def spy_run_tool(*args, **kwargs):
        executed.append(1)
        return await original(*args, **kwargs)

    deps.run_tool = spy_run_tool
    reg = ApprovalRegistry()
    _types, approval_id = await _run_to_park(deps, reg, "run-reject")

    result = await decide_approval(
        approval_id, ApprovalDecision.REJECT, approver="alice", registry=reg
    )
    assert result.accepted is True
    assert result.status == "rejected"
    assert executed == []  # rejected → never executed
    assert await list_pending() == []
    get_parked_runs().pop("run-reject")  # reject drops the handle; tidy any residue


async def test_fresh_worker_rehydrates_and_resumes_by_thread_id(db, make_deps, monkeypatch):
    """Cross-worker resume is REAL: a worker with NO in-process ``ParkedRun`` handle
    rebuilds the graph on the shared durable checkpointer and resumes the parked run
    by ``thread_id`` — the gated tool executes exactly once and the run finishes.

    This bites the finding directly: it wipes the process-wide parked-run registry to
    simulate a fresh worker/restart, so the *only* way to resume is rehydration from
    the durable checkpoint. If ``resume_parked_run`` reverts to the in-process-handle-
    only shortcut, the tool never runs and the status never reaches ``approved`` — both
    assertions below fail.
    """
    from app.agent import approvals as approvals_mod

    deps = _park_deps(make_deps)
    executed = _spy(deps)
    reg = ApprovalRegistry()
    _types, approval_id = await _run_to_park(deps, reg, "run-fresh")
    assert executed == []  # parked: nothing executed yet

    # The durable checkpoint + PENDING row survive, but this "fresh worker" has no
    # in-process handle: replace the registry with an empty one.
    monkeypatch.setattr(approvals_mod, "_default_parked", approvals_mod.ParkedRunRegistry())
    assert get_parked_runs().get("run-fresh") is None  # no handle — must rehydrate

    result = await decide_approval(
        approval_id, ApprovalDecision.APPROVE, approver="alice", registry=reg, deps=deps
    )
    assert result.accepted is True
    assert result.status == "approved"
    assert executed == [1]  # rehydrated from the checkpoint & executed exactly once

    # Idempotency across the fresh-worker path: a replayed decision is a no-op.
    again = await decide_approval(
        approval_id, ApprovalDecision.APPROVE, approver="bob", registry=reg, deps=deps
    )
    assert again.accepted is False
    assert executed == [1]

    row = await get_approval(approval_id)
    assert row is not None and row.status == "approved"


async def test_fresh_worker_without_durable_checkpoint_cannot_resume(db, make_deps, monkeypatch):
    """Honest negative: with the in-process handle gone AND the checkpoint store
    wiped (a truly separate process on the in-memory saver — no shared Postgres),
    there is nothing to rehydrate, so the async resume reports it did not run.

    This guards the claim's boundary: rehydration only succeeds when the checkpoint is
    actually durable/shared, never by silently re-executing from thin air.
    """
    from app.agent import approvals as approvals_mod
    from app.data.session import reset_agent_checkpointer

    deps = _park_deps(make_deps)
    executed = _spy(deps)
    reg = ApprovalRegistry()
    _types, approval_id = await _run_to_park(deps, reg, "run-lost")
    assert executed == []

    # Fresh worker AND a fresh (empty) checkpoint store: the parked checkpoint is gone.
    monkeypatch.setattr(approvals_mod, "_default_parked", approvals_mod.ParkedRunRegistry())
    reset_agent_checkpointer()
    try:
        result = await decide_approval(
            approval_id, ApprovalDecision.APPROVE, approver="alice", registry=reg, deps=deps
        )
        # The decision still won the durable lock, but no checkpoint means no resume.
        assert result.status == "resuming"
        assert executed == []
    finally:
        reset_agent_checkpointer()


async def test_retrieve_emits_provenance_event(db, make_deps):
    deps = make_deps(propose_tool=False)  # pure Q&A, no gate
    types = [
        e.type
        async for e in run_agent("what is the refund policy?", deps=deps)
    ]
    assert "provenance" in types


async def test_provenance_event_carries_default_shape(db, make_deps):
    deps = make_deps(propose_tool=False)
    events = [e async for e in run_agent("what is the refund policy?", deps=deps)]
    prov = next(e for e in events if e.type == "provenance")
    assert prov.origins == []            # empty default until retrieval populates it
    assert prov.fusion.value == "none"
    assert prov.cache_hit is False
