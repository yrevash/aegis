"""Cancellation against a real PostgreSQL: the tenant guard, and the order of the two writes.

Two properties are checked in the only way that can fail.

**A tenant cannot cancel another tenant's job**, and the proof is not that an exception
type came back — it is that the ``stop_workflow`` callable was **never invoked**. If the
guard ran after the stop, the neighbour's work would already be dead by the time the
refusal was raised, and a test asserting only the exception would pass on that code.

**The stop happens before the row is written.** A stop that fails must leave the row
untouched, because a row reading ``CANCELLED`` over an execution that is still running and
still billing is worse than a failed request. The test makes the stop raise and then reads
the row back from the database, rather than trusting the in-memory object.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aegis.governance.rls import set_tenant_scope
from aegis.jobs import (
    JobNotCancellableError,
    JobNotVisibleError,
    JobRun,
    JobStatus,
    cancel_job,
)

from .._seed import ensure_tenants

_TENANT = 70401
_OTHER_TENANT = 70402


@pytest_asyncio.fixture
async def db(pg_sessionmaker: async_sessionmaker) -> async_sessionmaker:
    """The unprivileged sessionmaker with the tenants the job foreign keys need."""
    await ensure_tenants(pg_sessionmaker, _TENANT, _OTHER_TENANT)
    return pg_sessionmaker


class _Stopper:
    """Records every workflow it was asked to stop, and can be made to fail."""

    def __init__(self, *, fails: bool = False) -> None:
        self.stopped: list[str] = []
        self.fails = fails

    async def __call__(self, workflow_id: str) -> None:
        """Record the request, raising first when this stopper is a failing one."""
        if self.fails:
            raise RuntimeError("the orchestrator is unreachable")
        self.stopped.append(workflow_id)


async def _add_job(
    db: async_sessionmaker,
    tenant_id: int,
    *,
    status: JobStatus = JobStatus.RUNNING,
    workflow_id: str = "wf-1",
) -> int:
    """Insert one job row and return its id."""
    async with db() as session:
        job = JobRun(
            tenant_id=tenant_id,
            job_type="ingest",
            workflow_id=workflow_id,
            status=status,
        )
        session.add(job)
        await session.commit()
        return job.id


async def _cancel(
    db: async_sessionmaker,
    job_id: int,
    *,
    tenant_id: int,
    stopper: _Stopper,
    cancelled_by: str = "an-admin",
) -> JobRun:
    """Cancel the way a request would: scope bound, committed on success."""
    async with db() as session:
        await set_tenant_scope(session, tenant_id)
        job = await cancel_job(
            session,
            job_id,
            tenant_id=tenant_id,
            cancelled_by=cancelled_by,
            stop_workflow=stopper,
        )
        await session.commit()
        return job


async def _read(db: async_sessionmaker, job_id: int, *, tenant_id: int) -> JobRun:
    """Read a job row back from the database under a bound scope."""
    async with db() as session:
        await set_tenant_scope(session, tenant_id)
        return (
            await session.execute(select(JobRun).where(JobRun.id == job_id))
        ).scalar_one()


async def test_cancelling_stops_the_work_and_records_who_asked(db) -> None:
    """The happy path: the orchestrator is told, and the row carries the audit answer."""
    job_id = await _add_job(db, _TENANT, workflow_id="wf-own")
    stopper = _Stopper()

    await _cancel(db, job_id, tenant_id=_TENANT, stopper=stopper, cancelled_by="ada")

    assert stopper.stopped == ["wf-own"]
    row = await _read(db, job_id, tenant_id=_TENANT)
    assert row.status is JobStatus.CANCELLED
    assert row.cancelled_by == "ada"
    # The cancellation timestamp *is* ``finished_at``: a cancelled job is finished.
    assert row.finished_at is not None


async def test_a_tenant_cannot_cancel_another_tenants_job_and_never_reaches_it(db) -> None:
    """The neighbour's work is never even asked to stop — the guard is before the stop."""
    job_id = await _add_job(db, _OTHER_TENANT, workflow_id="wf-neighbour")
    stopper = _Stopper()

    with pytest.raises(JobNotVisibleError) as caught:
        await _cancel(db, job_id, tenant_id=_TENANT, stopper=stopper)

    assert stopper.stopped == [], "the neighbour's workflow was asked to stop"
    assert str(job_id) in caught.value.reason
    row = await _read(db, job_id, tenant_id=_OTHER_TENANT)
    assert row.status is JobStatus.RUNNING
    assert row.cancelled_by is None


async def test_a_terminal_job_is_refused_rather_than_overwritten(db) -> None:
    """Cancelling a finished job would destroy the outcome of work that really happened."""
    job_id = await _add_job(
        db, _TENANT, status=JobStatus.SUCCEEDED, workflow_id="wf-done"
    )
    stopper = _Stopper()

    with pytest.raises(JobNotCancellableError):
        await _cancel(db, job_id, tenant_id=_TENANT, stopper=stopper)

    assert stopper.stopped == []
    row = await _read(db, job_id, tenant_id=_TENANT)
    assert row.status is JobStatus.SUCCEEDED


async def test_a_stop_that_fails_leaves_the_row_untouched(db) -> None:
    """No row may claim ``CANCELLED`` over an execution nobody managed to stop."""
    job_id = await _add_job(db, _TENANT, workflow_id="wf-unstoppable")

    with pytest.raises(RuntimeError, match="unreachable"):
        await _cancel(db, job_id, tenant_id=_TENANT, stopper=_Stopper(fails=True))

    row = await _read(db, job_id, tenant_id=_TENANT)
    assert row.status is JobStatus.RUNNING
    assert row.cancelled_by is None
    assert row.finished_at is None
