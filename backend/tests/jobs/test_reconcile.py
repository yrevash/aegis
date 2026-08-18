"""The reconciler: no open row is ever left with nothing behind it.

The activities' own idempotency covers the skew where a *commit* outlives its
acknowledgement. This file covers the opposite one, which no idempotency key can reach: a
row that says ``RUNNING`` while the execution behind it is gone. Left alone that row is
silent — nothing retries it, nothing times it out, and the tenant watching the ingest sees
a progress bar that will never move again.

Everything here runs against the real PostgreSQL as the ``NOSUPERUSER NOBYPASSRLS`` role,
with a **fake orchestrator client**. That split is deliberate rather than a shortcut:
the interesting inputs are the ones a real server will not produce on demand — a workflow
id it has never heard of, a workflow that was terminated externally — and a test that
waited for a real server to forget something would be a test of the server's retention
policy. The rows, the transactions and the tenant scope are all real; only the answer to
"what became of this workflow?" is supplied.

The schedule itself *is* checked against a real server, at the bottom: "it runs on a
Temporal Schedule" is a claim about a thing that exists in the orchestrator, and asserting
it against a fake would prove only that the fake was called.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from aegis.jobs import Document, JobRun, JobStatus
from sqlalchemy import select
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import WorkflowEnvironment

from app.jobs.client import reset_temporal_client, set_temporal_client
from app.jobs.flows.contracts import ReconcileParams
from app.jobs.flows.reconcile import RECONCILE_WORKFLOW
from app.jobs.flows.reindex import REINDEX_CADENCE_WORKFLOW
from app.jobs.reconcile import Verdict, reconcile_stale_runs, verdict_for
from app.jobs.schedules import (
    RECONCILE_SCHEDULE_ID,
    ensure_platform_schedules,
    ensure_tenant_reindex_schedules,
    reindex_schedule_id,
)
from app.jobs.worker import start_worker_task

from .conftest import (
    TENANT_A,
    free_port,
    seed_document,
    seed_tenants,
    skip_without_temporal,
    temporal_cli_path,
)

#: How stale a row must be for the sweeps below to examine it.
_STALE_AFTER = 3600


def _params(*, stale_after_seconds: int = _STALE_AFTER, limit: int = 50) -> ReconcileParams:
    """Build the sweep's argument.

    Args:
        stale_after_seconds: The staleness threshold for this sweep.
        limit: The batch size.

    Returns:
        The :class:`ReconcileParams` for a platform-scoped sweep.
    """
    return ReconcileParams(
        tenant_id=None,
        workflow_id="reconcile-test",
        stale_after_seconds=stale_after_seconds,
        limit=limit,
    )


# ─────────────────────────────────────────────────────────────────────────────
# The fake orchestrator
# ─────────────────────────────────────────────────────────────────────────────


class _NotFoundHandle:
    """A workflow handle for an id the orchestrator has never heard of."""

    async def describe(self) -> Any:  # noqa: ANN401 - never returns
        """Raise the ``NOT_FOUND`` the real SDK raises.

        Raises:
            RPCError: Always, with ``NOT_FOUND``.
        """
        raise RPCError("workflow not found", RPCStatusCode.NOT_FOUND, b"")


@dataclass
class _StatusHandle:
    """A workflow handle that reports a fixed status."""

    status: WorkflowExecutionStatus

    async def describe(self) -> Any:  # noqa: ANN401 - a stand-in for the SDK's description
        """Return an object carrying the status, as the SDK's description does."""
        return self


@dataclass
class _FakeClient:
    """The orchestrator's answers, and a record of every workflow it was asked to start.

    Recording the starts is what makes the restart branch provable: "the row was marked
    ``RECONCILING``" alone would be equally consistent with a reconciler that marked it and
    then did nothing at all, which is a worse bug than the one it was fixing.
    """

    statuses: dict[str, WorkflowExecutionStatus | None] = field(default_factory=dict)
    started: list[tuple[str, Any, str]] = field(default_factory=list)

    def get_workflow_handle(self, workflow_id: str) -> Any:  # noqa: ANN401 - a fake handle
        """Return a handle answering for ``workflow_id``."""
        status = self.statuses.get(workflow_id)
        if status is None:
            return _NotFoundHandle()
        return _StatusHandle(status)

    async def start_workflow(
        self, workflow: str, arg: Any, *, id: str, **_: Any  # noqa: A002, ANN401 - the SDK's own signature
    ) -> None:
        """Record a restart instead of performing one."""
        self.started.append((workflow, arg, id))


@pytest.fixture
def fake_temporal():
    """Install a fake orchestrator client for one test, and take it away afterwards."""
    client = _FakeClient()
    set_temporal_client(client)  # type: ignore[arg-type]  # a deliberate stand-in
    try:
        yield client
    finally:
        reset_temporal_client()


# ─────────────────────────────────────────────────────────────────────────────
# Seeding
# ─────────────────────────────────────────────────────────────────────────────


async def _seed_run(
    db,
    *,
    workflow_id: str,
    document_id: int | None = None,
    status: JobStatus = JobStatus.RUNNING,
    age_seconds: int = _STALE_AFTER * 2,
    job_type: str = "ingest",
    cancelled_by: str | None = None,
) -> int:
    """Insert one open job row of a chosen age, and return its id.

    Args:
        db: The serving-engine session factory.
        workflow_id: The id the reconciler will ask the orchestrator about.
        document_id: The document the run belongs to, recorded on ``payload``.
        status: The status to seed it in.
        age_seconds: How long ago it started. The default is comfortably past the
            threshold; a smaller value is how the "too young to question" case is built.
        job_type: The ``job_runs.job_type``, which selects the restart path.
        cancelled_by: Who cancelled it, if anyone.

    Returns:
        The new row's id.
    """
    async with db() as session:
        row = JobRun(
            tenant_id=TENANT_A,
            job_type=job_type,
            workflow_id=workflow_id,
            status=status,
            payload={} if document_id is None else {"document_id": document_id},
            result={},
            started_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
            cancelled_by=cancelled_by,
        )
        session.add(row)
        await session.commit()
        return row.id


async def _row(db, job_run_id: int) -> JobRun:
    """Read one job row back over the serving role."""
    async with db() as session:
        return (
            await session.execute(select(JobRun).where(JobRun.id == job_run_id))
        ).scalar_one()


# ─────────────────────────────────────────────────────────────────────────────
# The decision table, proved without a server
# ─────────────────────────────────────────────────────────────────────────────


def test_a_missing_workflow_is_a_failure_with_a_reason():
    verdict, reason = verdict_for(None, cancelled_by=None)

    assert verdict is Verdict.FAIL
    # A status change with no reason is a job nobody can support.
    assert "no execution with this workflow id" in reason


@pytest.mark.parametrize("status", list(WorkflowExecutionStatus))
def test_every_orchestrator_status_reaches_a_deliberate_verdict(status):
    """No status may fall through to a default nobody wrote down.

    Parametrised over the SDK's own enum rather than a hand-written list, so a status
    added by a future ``temporalio`` fails this test instead of quietly acquiring
    whichever branch happens to be last.
    """
    verdict, reason = verdict_for(status, cancelled_by=None)

    assert isinstance(verdict, Verdict)
    assert (verdict is Verdict.LEAVE) == (reason == "")


def test_a_cancelled_run_is_never_resurrected():
    # A person stopped this work on purpose. A sweeper that restarted it would be
    # overruling them from a background thread.
    verdict, reason = verdict_for(
        WorkflowExecutionStatus.TERMINATED, cancelled_by="admin@tenant-a"
    )

    assert verdict is Verdict.CANCEL
    assert "admin@tenant-a" in reason
    # The positive control: the same status without a canceller does restart, so the
    # refusal above is the ``cancelled_by`` check and not a status that never restarts.
    assert (
        verdict_for(WorkflowExecutionStatus.TERMINATED, cancelled_by=None)[0]
        is Verdict.RESTART
    )


# ─────────────────────────────────────────────────────────────────────────────
# The sweep, against real rows
# ─────────────────────────────────────────────────────────────────────────────


async def test_a_running_row_whose_workflow_is_gone_is_failed_with_a_reason(
    wired_jobs, fake_temporal
):
    await seed_tenants(wired_jobs, TENANT_A)
    document_id = await seed_document(wired_jobs, TENANT_A, sha="a" * 64)
    job_run_id = await _seed_run(
        wired_jobs, workflow_id="ingest:7:gone", document_id=document_id
    )
    # The orchestrator knows nothing about this id: ``_FakeClient`` answers NOT_FOUND for
    # every id it was not told about.

    report = await reconcile_stale_runs(_params())

    assert (report.examined, report.failed) == (1, 1)
    row = await _row(wired_jobs, job_run_id)
    assert row.status is JobStatus.FAILED
    assert row.error and "no execution with this workflow id" in row.error
    assert row.finished_at is not None
    # The document, too: a tenant reads the document's status, not the job row's.
    async with wired_jobs() as session:
        document = (
            await session.execute(select(Document).where(Document.id == document_id))
        ).scalar_one()
    assert document.status is JobStatus.FAILED
    assert document.error == row.error
    # And nothing was restarted, which is what distinguishes "closed" from "resumed".
    assert fake_temporal.started == []


async def test_a_row_whose_workflow_is_still_running_is_left_alone(
    wired_jobs, fake_temporal
):
    await seed_tenants(wired_jobs, TENANT_A)
    job_run_id = await _seed_run(wired_jobs, workflow_id="ingest:7:live")
    fake_temporal.statuses["ingest:7:live"] = WorkflowExecutionStatus.RUNNING

    report = await reconcile_stale_runs(_params())

    # The positive control for the test above: the sweep did look at this row and chose
    # not to touch it, rather than not looking at all.
    assert (report.examined, report.left, report.failed) == (1, 1, 0)
    row = await _row(wired_jobs, job_run_id)
    assert row.status is JobStatus.RUNNING
    assert row.error is None


async def test_a_row_younger_than_the_threshold_is_not_even_examined(
    wired_jobs, fake_temporal
):
    await seed_tenants(wired_jobs, TENANT_A)
    job_run_id = await _seed_run(
        wired_jobs, workflow_id="ingest:7:young", age_seconds=5
    )

    report = await reconcile_stale_runs(_params())

    # A row is legitimately RUNNING for as long as its work takes. A sweep that questioned
    # a job the instant it started would fight the pipeline it exists to protect.
    assert report.examined == 0
    assert (await _row(wired_jobs, job_run_id)).status is JobStatus.RUNNING


async def test_a_workflow_that_ended_with_the_row_still_open_is_restarted(
    wired_jobs, fake_temporal
):
    await seed_tenants(wired_jobs, TENANT_A)
    document_id = await seed_document(wired_jobs, TENANT_A, sha="a" * 64)
    job_run_id = await _seed_run(
        wired_jobs, workflow_id="ingest:7:orphan", document_id=document_id
    )
    fake_temporal.statuses["ingest:7:orphan"] = WorkflowExecutionStatus.TERMINATED

    report = await reconcile_stale_runs(_params())

    assert (report.examined, report.restarted) == (1, 1)
    row = await _row(wired_jobs, job_run_id)
    # RECONCILING and not RUNNING: the row is honestly "being dealt with" until the new
    # execution claims it, and the next sweep examines RECONCILING rows too, so a restart
    # whose orchestrator call failed is not a new kind of stuck.
    assert row.status is JobStatus.RECONCILING
    assert row.finished_at is None
    (workflow_name, argument, workflow_id) = fake_temporal.started[0]
    assert workflow_id == "ingest:7:orphan", (
        "the restart must reuse the original workflow id, or the resume reads a job row "
        "that is not this one"
    )
    assert workflow_name == "AegisIngest"
    assert (argument.tenant_id, argument.document_id) == (TENANT_A, document_id)


async def test_a_reconciling_row_is_swept_again_rather_than_resting_there(
    wired_jobs, fake_temporal
):
    await seed_tenants(wired_jobs, TENANT_A)
    job_run_id = await _seed_run(
        wired_jobs, workflow_id="ingest:7:stuck", status=JobStatus.RECONCILING
    )

    report = await reconcile_stale_runs(_params())

    # RECONCILING is a transient state, never a resting place: a row whose restart was
    # ordered but whose orchestrator call then failed must be looked at again.
    assert report.examined == 1
    assert (await _row(wired_jobs, job_run_id)).status is JobStatus.FAILED


async def test_a_job_type_with_no_restart_path_is_closed_rather_than_left(
    wired_jobs, fake_temporal
):
    await seed_tenants(wired_jobs, TENANT_A)
    job_run_id = await _seed_run(
        wired_jobs, workflow_id="reindex:7", job_type="reindex"
    )
    fake_temporal.statuses["reindex:7"] = WorkflowExecutionStatus.TERMINATED

    report = await reconcile_stale_runs(_params())

    assert (report.restarted, report.failed) == (0, 1)
    row = await _row(wired_jobs, job_run_id)
    assert row.status is JobStatus.FAILED
    assert row.error and "reindex" in row.error
    assert fake_temporal.started == []


async def test_the_sweep_is_bounded_by_its_limit(wired_jobs, fake_temporal):
    await seed_tenants(wired_jobs, TENANT_A)
    for index in range(5):
        await _seed_run(wired_jobs, workflow_id=f"ingest:7:{index}")

    report = await reconcile_stale_runs(_params(limit=2))

    # Each row costs an RPC. An unbounded sweep after an outage would be a thundering herd
    # against the server that is already struggling.
    assert report.examined == 2


async def test_reconciling_the_same_row_twice_is_idempotent(wired_jobs, fake_temporal):
    await seed_tenants(wired_jobs, TENANT_A)
    document_id = await seed_document(wired_jobs, TENANT_A, sha="a" * 64)
    job_run_id = await _seed_run(
        wired_jobs, workflow_id="ingest:7:gone", document_id=document_id
    )

    first = await reconcile_stale_runs(_params())
    second = await reconcile_stale_runs(_params())

    assert first.failed == 1
    # The row is closed, so the second sweep no longer sees it — a reconciler that
    # re-failed a failed row would re-stamp ``finished_at`` on every pass and make every
    # duration this platform reports for that job wrong.
    assert second.examined == 0
    row = await _row(wired_jobs, job_run_id)
    assert row.status is JobStatus.FAILED


# ─────────────────────────────────────────────────────────────────────────────
# The schedule, against a real server
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
async def temporal_server():
    """A real Temporal dev server for one test."""
    binary = temporal_cli_path()
    if binary is None:
        skip_without_temporal(
            "that the reconciler really is registered as a Temporal Schedule rather than "
            "as a table this platform would have to sweep itself."
        )
    env = await WorkflowEnvironment.start_local(
        dev_server_existing_path=binary, port=free_port(), ui=False
    )
    try:
        yield env
    finally:
        await env.shutdown()


async def test_the_reconciler_is_registered_as_a_schedule_and_re_declaring_it_updates(
    temporal_server,
):
    client: Client = temporal_server.client

    first = await ensure_platform_schedules(client)
    second = await ensure_platform_schedules(client)

    assert first[RECONCILE_SCHEDULE_ID] == "created"
    # Every worker bootstrap declares the schedules, so the second call must converge
    # rather than raise — and it must *update*, because a schedule created by an older
    # build with an older interval would otherwise outlive the setting that changed it.
    assert second[RECONCILE_SCHEDULE_ID] == "updated"
    description = await client.get_schedule_handle(RECONCILE_SCHEDULE_ID).describe()
    assert description.schedule.action.workflow == RECONCILE_WORKFLOW
    assert description.schedule.spec.intervals, (
        "the reconciler schedule fires on nothing, so no sweep would ever run"
    )


@pytest.fixture
async def reconcile_env(wired_jobs):
    """A dev server with the platform's own workers running against it.

    Through :func:`app.jobs.worker.start_worker_task`, the same call the API lifespan
    makes — which is also what declares the schedules, so this fixture is itself part of
    what the test below proves.
    """
    binary = temporal_cli_path()
    if binary is None:
        skip_without_temporal(
            "that the reconciler's schedule really drives a sweep that closes a stranded "
            "row, end to end."
        )
    env = await WorkflowEnvironment.start_local(
        dev_server_existing_path=binary, port=free_port(), ui=False
    )
    set_temporal_client(env.client)
    stop = asyncio.Event()
    worker = start_worker_task(stop)
    try:
        yield env
    finally:
        stop.set()
        await asyncio.wait_for(worker, timeout=30)
        reset_temporal_client()
        await env.shutdown()


async def test_the_schedule_drives_a_real_sweep_that_closes_a_stranded_row(
    reconcile_env, wired_jobs
):
    """End to end: schedule → workflow → activity → the row actually changes.

    Every other test in this file supplies the orchestrator's answer. This one supplies
    nothing: the workflow id on the row is one this brand-new server has genuinely never
    heard of, the sweep is started by the schedule the worker bootstrap declared, and the
    only thing asserted is that the row stopped saying ``RUNNING``. That is the difference
    between "the reconciler is correct" and "the reconciler runs".
    """
    await seed_tenants(wired_jobs, TENANT_A)
    document_id = await seed_document(wired_jobs, TENANT_A, sha="a" * 64)
    job_run_id = await _seed_run(
        wired_jobs, workflow_id="ingest:7:vanished", document_id=document_id
    )

    await reconcile_env.client.get_schedule_handle(RECONCILE_SCHEDULE_ID).trigger()

    deadline = asyncio.get_running_loop().time() + 60
    row = await _row(wired_jobs, job_run_id)
    while row.status is JobStatus.RUNNING and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.25)
        row = await _row(wired_jobs, job_run_id)
    assert row.status is JobStatus.FAILED, (
        "the scheduled sweep never reached the row: the schedule, the workflow or the "
        f"activity is not wired. The row still reads {row.status}."
    )
    assert row.error and "no execution with this workflow id" in row.error


async def test_every_existing_tenant_gets_a_re_index_cadence(temporal_server, wired_jobs):
    """The cadence half of §3.5 is wired, not merely available.

    This is the function :func:`app.jobs.worker.run_workers` calls on every boot — which
    is the point: a schedule declared only at tenant-creation time would be missing for
    every tenant that predates the feature, and its absence is invisible by construction,
    because the symptom of no cadence is that nothing happens.
    """
    await seed_tenants(wired_jobs, TENANT_A)

    created = await ensure_tenant_reindex_schedules(temporal_server.client)

    schedule_id = reindex_schedule_id(TENANT_A)
    assert created == {schedule_id: "created"}
    description = await temporal_server.client.get_schedule_handle(schedule_id).describe()
    # It points at the cadence *tick*, not at the debounced workflow: a schedule appends
    # the fire time to the workflow id it starts, so pointing it at the re-index directly
    # would give every scheduled run its own debounce window and silently unfold the fold.
    assert description.schedule.action.workflow == REINDEX_CADENCE_WORKFLOW
    assert (
        await ensure_tenant_reindex_schedules(temporal_server.client)
    ) == {schedule_id: "updated"}
