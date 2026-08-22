"""A parked run's durable record must be *completed* by the decision that resumes it.

The defect these cover: the header (``runs``) is a fold over the run's events, a run that
parks at the human gate ends its stream at the gate, and the continuation a human's
decision drove was headless — ``aegis.agent.orchestrator.resume_parked_run`` discarded
every chunk it yielded. So the ``approvals`` row recorded "approved, by alice, at 11:30"
while the ``runs`` row recorded "still waiting for a human", permanently. Two of our own
tables disagreeing about one run.

Real PostgreSQL (the ``db`` fixture), real ``decide_approval``, faked model/tools.
"""

from __future__ import annotations

import asyncio

import pytest
from aegis.core.types import RunStatus
from aegis.runs.record import read_run_header, reconcile_run_header

from app.agent import ApprovalRegistry, decide_approval, get_parked_runs, run_agent
from app.agent import run_log
from app.api.schemas import ApprovalDecision
from app.data.session import get_sessionmaker, set_tenant_scope

pytestmark = pytest.mark.asyncio


def _park_deps(make_deps):
    """Fake deps whose HIGH-risk proposal parks the live gate almost immediately."""
    deps = make_deps(propose_tool=True, high_risk=True)
    deps.config.approval_park_timeout = 0.05
    return deps


async def _park(deps, registry, run_id):
    """Drive a gated run until it parks; return its ``approval_id``."""
    approval_id: str | None = None
    async for event in run_agent(
        "resolve R1",
        persona="operations_lead",
        deps=deps,
        registry=registry,
        run_id=run_id,
    ):
        if event.type == "approval_required":
            approval_id = event.approval_id
    # The parked half is written from a tracked background task, so a test that read the
    # header immediately would be racing the writer rather than testing it.
    await _settle()
    return approval_id


async def _settle():
    """Wait for every in-flight durable run-record task to finish."""
    for _ in range(50):
        pending = [t for t in run_log._RECORD_TASKS if not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


async def _header(run_id):
    """Read the stored ``runs`` header for ``run_id``."""
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, None)
        return await read_run_header(session, run_id)


async def test_an_approved_park_completes_its_own_record(db, make_deps):
    """The continuation is appended to the same log and the header re-folds to done."""
    deps = _park_deps(make_deps)
    registry = ApprovalRegistry()
    approval_id = await _park(deps, registry, "run-record-approve")

    parked = await _header("run-record-approve")
    assert parked is not None, "a parked run must be recorded, not merely queued"
    assert parked.status is RunStatus.AWAITING_APPROVAL
    assert parked.approval_count == 1

    result = await decide_approval(
        approval_id, ApprovalDecision.APPROVE, approver="alice", registry=registry
    )
    assert result.status == "approved"

    done = await _header("run-record-approve")
    # The run's record now agrees with its approval row, which is the whole point.
    assert done.status is RunStatus.COMPLETED
    assert done.finished_at > parked.finished_at
    assert done.event_count > parked.event_count
    # Monotonic across the park boundary: the continuation was numbered from where the
    # parked stream stopped, never from zero (which would re-order the fold).
    assert done.last_seq > parked.last_seq
    # Both halves' node timings are summed, so the duration is the whole run's.
    assert done.duration_ms >= parked.duration_ms
    assert done.started_at == parked.started_at  # one run, not two


async def test_a_rejected_park_reaches_a_terminal_status_too(db, make_deps):
    """A refusal is a decision: the run must finish, not sit parked forever."""
    deps = _park_deps(make_deps)
    registry = ApprovalRegistry()
    approval_id = await _park(deps, registry, "run-record-reject")
    assert (await _header("run-record-reject")).status is RunStatus.AWAITING_APPROVAL

    result = await decide_approval(
        approval_id, ApprovalDecision.REJECT, approver="alice", registry=registry
    )
    assert result.status == "rejected"

    done = await _header("run-record-reject")
    assert done.status is not RunStatus.AWAITING_APPROVAL
    assert done.status is RunStatus.COMPLETED  # the graph answered; the action did not run
    assert done.tool_call_count == 0  # and nothing was executed on a refusal


async def test_the_header_stays_a_true_fold_of_its_own_log(db, make_deps):
    """``events win`` must remain executable after the append, not just before it."""
    deps = _park_deps(make_deps)
    registry = ApprovalRegistry()
    approval_id = await _park(deps, registry, "run-record-fold")
    await decide_approval(
        approval_id, ApprovalDecision.APPROVE, approver="alice", registry=registry
    )

    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, None)
        _rebuilt, changed = await reconcile_run_header(session, "run-record-fold")
        assert changed is False, "the stored header disagreed with the events it claims"


async def test_replaying_the_continuation_double_counts_nothing(db, make_deps):
    """A retry, a second resume, a replayed decision: the record moves exactly once."""
    deps = _park_deps(make_deps)
    registry = ApprovalRegistry()
    approval_id = await _park(deps, registry, "run-record-twice")
    await decide_approval(
        approval_id, ApprovalDecision.APPROVE, approver="alice", registry=registry
    )
    once = await _header("run-record-twice")

    # 1. The durable decision is idempotent, so a replayed approval never resumes again.
    replay = await decide_approval(
        approval_id, ApprovalDecision.APPROVE, approver="bob", registry=registry
    )
    assert replay.accepted is False

    # 2. And the record layer refuses on its own, independently of that lock: a header
    #    that is no longer parked has no continuation outstanding.
    appended = await run_log.record_run_continuation(
        run_id="run-record-twice",
        events=[
            {"type": "node_started", "node": "replay", "label": "replayed"},
            {
                "type": "run_finished",
                "status": "completed",
                "prompt_tokens": 999999,
                "completion_tokens": 999999,
                "cost_usd": 99.0,
                "cache_hit": False,
            },
        ],
        tenant_id=None,
    )
    assert appended is False

    twice = await _header("run-record-twice")
    assert twice == once  # every field, not just the count


async def test_a_run_with_no_header_is_refused_loudly_rather_than_half_recorded(
    db, make_deps, caplog
):
    """A continuation with nothing to append to must say so, not invent a run.

    The parked half can be missing — stores off when the run parked, or a record that
    failed — and appending anyway would file a run whose log starts in the middle: no
    ``run_started``, ``started_at`` at the moment a human clicked approve.
    """
    with caplog.at_level("ERROR", logger="app.agent.run_log"):
        appended = await run_log.record_run_continuation(
            run_id="run-that-never-parked",
            events=[{"type": "run_finished", "status": "completed"}],
            tenant_id=None,
        )
    assert appended is False
    assert await _header("run-that-never-parked") is None
    assert "no durable header" in caplog.text.lower()


@pytest.fixture(autouse=True)
def _tidy_parked_runs():
    """Keep the process-wide parked-run registry out of the next test's way."""
    yield
    for run_id in (
        "run-record-approve",
        "run-record-reject",
        "run-record-fold",
        "run-record-twice",
    ):
        get_parked_runs().pop(run_id)
