"""Cancellation — the guard, the record, and the order the two happen in.

Cancelling a durable job is two facts in two systems, and getting them out of step is the
whole risk. The orchestrator must stop executing; our row must say who stopped it and
when. This module owns the halves that are *ours* — deciding whether the caller may cancel
this row at all, and writing the record — and takes the "stop the work" half as a callable,
because that one is engine-specific and lives in the composing host.

**The order is load-bearing.** The stop is attempted *before* the row is written:

* stop first, write second — a failure to stop leaves the row untouched, so the tenant is
  told the cancellation did not happen, which is true;
* write first, stop second — a failure to stop leaves a row reading ``CANCELLED`` over
  work that is still running and still billing. That is a row that lies, and a lying row
  is worse than a failed request.

The reverse skew (stopped, but the write failed) is the recoverable one: the reconciler
already exists to find a row whose execution no longer accounts for itself.

**The tenant guard is structural, not a check somebody remembered.** The row is loaded by
``id`` *and* tenant in one statement, on a session the caller has bound to its own tenant
scope, so a job belonging to another tenant does not resolve — under Row-Level Security it
is not even visible. There is no code path in which a workflow id reaches the orchestrator
without a row having first been found under the caller's own scope.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aegis.jobs.models import JobRun, JobStatus

__all__ = [
    "TERMINAL_STATUSES",
    "CancellationError",
    "JobNotCancellableError",
    "JobNotVisibleError",
    "cancel_job",
]

#: The statuses a job can never leave. Cancelling one of these is refused rather than
#: quietly accepted: overwriting a ``SUCCEEDED`` row with ``CANCELLED`` would destroy the
#: outcome of work that really did happen, and telling a caller "cancelled" about a job
#: that finished an hour ago is a small lie with an audit trail behind it.
TERMINAL_STATUSES: tuple[JobStatus, ...] = (
    JobStatus.SUCCEEDED,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
)

#: Stops the work behind a workflow id. Async because every orchestrator's cancel is a
#: network call; typed here so the host's implementation is a parameter rather than an
#: import — which is what keeps this package free of an orchestrator SDK.
StopWorkflow = Callable[[str], Awaitable[None]]


class CancellationError(Exception):
    """Base for every refusal this module makes, always carrying a reason.

    Attributes:
        reason: A sentence naming what was refused and why, safe to show a human.
    """

    def __init__(self, reason: str) -> None:
        """Build the error with the reason it will be reported by."""
        super().__init__(reason)
        self.reason = reason


class JobNotVisibleError(CancellationError):
    """No such job under the caller's tenant scope (a **403**).

    Deliberately one error for "does not exist" and "is not yours". The two are
    indistinguishable *from the caller's position* — under RLS the row simply is not
    there — and answering them differently would turn this endpoint into an oracle for
    which job ids exist in other tenants.
    """


class JobNotCancellableError(CancellationError):
    """The job has already reached a terminal state (a **409**)."""


async def cancel_job(
    session: AsyncSession,
    job_id: int,
    *,
    tenant_id: int | None,
    cancelled_by: str,
    stop_workflow: StopWorkflow,
) -> JobRun:
    """Stop a tenant's job and record who stopped it.

    Args:
        session: A session already bound to ``tenant_id``'s scope. The caller commits —
            so a host that also writes an audit row commits both together or neither.
        job_id: The ``job_runs`` row to cancel.
        tenant_id: The caller's tenant. The row must belong to it; ``None`` matches only
            platform-level rows.
        cancelled_by: The acting principal, recorded on the row. A cancelled tenant job is
            an audit question ("who stopped the ingest?") before it is an operational one.
        stop_workflow: Called with the row's ``workflow_id`` to stop the execution, before
            anything is written. Whatever it raises propagates unchanged: the host knows
            what an unreachable orchestrator means and this package does not.

    Returns:
        The updated row, flushed but not committed.

    Raises:
        JobNotVisibleError: No row with that id belongs to this tenant.
        JobNotCancellableError: The job already finished, failed or was cancelled.
    """
    tenant_clause = (
        JobRun.tenant_id.is_(None) if tenant_id is None else JobRun.tenant_id == tenant_id
    )
    job = (
        await session.execute(select(JobRun).where(JobRun.id == job_id, tenant_clause))
    ).scalar_one_or_none()
    if job is None:
        raise JobNotVisibleError(
            f"job {job_id} is not this tenant's to cancel (no such row under tenant "
            f"{tenant_id})"
        )
    if job.status in TERMINAL_STATUSES:
        raise JobNotCancellableError(
            f"job {job_id} is already {job.status.value} and cannot be cancelled"
        )

    await stop_workflow(job.workflow_id)

    # Naive UTC, matching the column the rest of the row's timestamps are written in.
    now = datetime.now(UTC).replace(tzinfo=None)
    job.status = JobStatus.CANCELLED
    job.cancelled_by = cancelled_by
    # The cancellation timestamp *is* ``finished_at``: a cancelled job is finished at that
    # instant, and a second column would be a second place for the same fact to be wrong.
    job.finished_at = now
    await session.flush()
    return job
