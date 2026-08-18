"""The tenant-facing control plane for jobs — list, requeue, cancel.

The half of task 3.4 that needs a host: :mod:`aegis.jobs.admission` and
:mod:`aegis.jobs.cancel` decide tenant policy against a session, and this module supplies
the session, the estimate the policy is evaluated against, and the orchestrator calls that
actually start and stop work.

Three operations, and the reason each exists:

* :func:`list_jobs` — a tenant's background work is only real to it if it can see it. The
  read goes through our rows rather than the orchestrator, so it answers with Temporal
  down, exactly as :mod:`aegis.jobs.models` intends.
* :func:`requeue_job` — the admission-guarded start path. A failed or cancelled ingest is
  resumed from ``completed_stage``, so re-queueing a run that died at the graph stage does
  not re-parse two hundred pages. This is where :func:`aegis.jobs.admission.admit` runs,
  and therefore where a tenant meets its concurrency cap and its budget.
* :func:`cancel_job` — the tenant asks for the work to stop; the orchestrator stops it and
  our row records who asked.

**Admission runs before the workflow is started, not after.** The order is the point of
the gate: a job refused by admission must leave no trace in the orchestrator, which is
exactly what the "zero ``start_workflow`` calls" test asserts. Nothing here catches the
refusal — it propagates to the route, which turns it into a visible 429.

**The estimate is derived, never supplied by the caller.** It is the document's size in
megabytes times ``jobs.estimated_cost_usd.ingest_per_mb`` from the settings catalogue: a
number a platform admin tunes from a dashboard, evaluated against a column that is always
present. Trusting a client-supplied estimate would make the budget gate advisory, since
the client that wants the job is the one motivated to under-report it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from aegis.jobs import (
    Document,
    JobNotVisibleError,
    JobRun,
    admit,
)
from aegis.jobs import cancel_job as _cancel_job_row
from aegis.jobs.stages import DEFAULT_QUEUE
from aegis.settings import resolve
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data import get_sessionmaker, set_tenant_scope
from app.jobs.client import get_temporal_client
from app.jobs.flows import INGEST_WORKFLOW
from app.jobs.flows.contracts import IngestParams

logger = logging.getLogger(__name__)

__all__ = [
    "ESTIMATE_PER_MB_KEY",
    "JobRow",
    "MissingDocumentError",
    "cancel_job",
    "list_jobs",
    "requeue_job",
]

#: The catalogue key holding the per-megabyte USD estimate the budget gate pre-authorises
#: an ingest against.
ESTIMATE_PER_MB_KEY = "jobs.estimated_cost_usd.ingest_per_mb"

#: Bytes in one megabyte, spelled out because the estimate is a money figure and 10**6
#: versus 2**20 is a 5% error in every pre-authorisation the platform makes.
_BYTES_PER_MB = 1024 * 1024

#: How many jobs a single list call returns at most.
_LIST_LIMIT = 200


class MissingDocumentError(LookupError):
    """A job row's document is gone, so there is nothing to re-queue (a **404**).

    Separate from "not your job": the caller owns the row, the row simply no longer
    describes runnable work. Answering 403 would send an owner looking for a permission
    problem that does not exist.
    """


@dataclass(frozen=True, slots=True)
class JobRow:
    """One job as a tenant sees it — the projection the console and the API render.

    Built here rather than returning the ORM object so the response contract does not
    depend on a mapped class staying loaded after its session closes.

    Attributes:
        id: The ``job_runs`` row id.
        job_type: What kind of work it is, e.g. ``"ingest"``.
        status: The :class:`aegis.jobs.JobStatus` value, as its string.
        completed_stage: The last stage that committed, or ``None``.
        workflow_id: The orchestrator execution behind it.
        document_id: The document being processed, when the payload names one.
        cost_usd: What the run has cost so far.
        error: The failure reason, when it failed.
        cancelled_by: Who cancelled it, when it was cancelled.
        created_at: ISO 8601 UTC creation time.
        started_at: ISO 8601 UTC start time, or ``None``.
        finished_at: ISO 8601 UTC terminal time, or ``None`` — for a cancelled job this
            *is* the cancellation timestamp.
    """

    id: int
    job_type: str
    status: str
    completed_stage: str | None
    workflow_id: str
    document_id: int | None
    cost_usd: float
    error: str | None
    cancelled_by: str | None
    created_at: str | None
    started_at: str | None
    finished_at: str | None


def _iso(ts: datetime | None) -> str | None:
    """Render a (naive or aware) UTC timestamp as ISO 8601, or ``None``."""
    if ts is None:
        return None
    return (ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)).isoformat()


def _row(job: JobRun) -> JobRow:
    """Project one ORM row into the API's shape."""
    payload = job.payload or {}
    document_id = payload.get("document_id")
    return JobRow(
        id=job.id,
        job_type=job.job_type,
        status=job.status.value,
        completed_stage=job.completed_stage,
        workflow_id=job.workflow_id,
        document_id=int(document_id) if isinstance(document_id, int) else None,
        cost_usd=float(job.cost_usd or 0.0),
        error=job.error,
        cancelled_by=job.cancelled_by,
        created_at=_iso(job.created_at),
        started_at=_iso(job.started_at),
        finished_at=_iso(job.finished_at),
    )


async def list_jobs(*, tenant_id: int | None) -> list[JobRow]:
    """Return a tenant's most recent jobs, newest first.

    Args:
        tenant_id: The tenant to read. ``None`` is the platform-admin view — every
            tenant's rows — and is only ever reached from a route that has already
            established the caller holds that tier.

    Returns:
        Up to :data:`_LIST_LIMIT` rows.
    """
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, tenant_id)
        stmt = select(JobRun).order_by(JobRun.created_at.desc(), JobRun.id.desc())
        if tenant_id is not None:
            stmt = stmt.where(JobRun.tenant_id == tenant_id)
        rows = (await session.execute(stmt.limit(_LIST_LIMIT))).scalars().all()
    return [_row(job) for job in rows]


async def _estimate_usd(
    session: AsyncSession, document: Document, *, tenant_id: int | None
) -> float:
    """Return the pre-authorisation estimate for ingesting ``document``.

    Args:
        session: The scoped session, for resolving the catalogue rate.
        document: The document about to be ingested.
        tenant_id: The tenant the rate is resolved for.

    Returns:
        ``size_bytes / 1 MiB × jobs.estimated_cost_usd.ingest_per_mb``. Size is used
        rather than ``page_count`` because a document that has never been parsed has no
        page count, and the gate has to be able to refuse *that* job — the expensive one —
        rather than only the ones already partly done.
    """
    rate, _source = await resolve(session, ESTIMATE_PER_MB_KEY, tenant_id=tenant_id)
    return (document.size_bytes / _BYTES_PER_MB) * float(rate)


async def requeue_job(
    *, job_id: int, tenant_id: int | None, user_id: int | None
) -> JobRow:
    """Admit and re-start the ingestion behind an existing job row.

    The new execution gets a fresh workflow id. Reusing the old one would collide with the
    unique ``job_runs.workflow_id`` and the claim activity's ``ON CONFLICT DO NOTHING``
    would then leave the *old*, terminal row in place while new work ran against it — a
    row saying ``FAILED`` over a live execution. A new id gives the new attempt its own
    honest row, and the document's ``completed_stage`` is what makes it a resume rather
    than a restart.

    Args:
        job_id: The row whose document should be ingested again.
        tenant_id: The caller's tenant; the row must belong to it.
        user_id: The acting user, recorded on the new run for cost attribution.

    Returns:
        The row that was re-queued, as it stood before the new execution started.

    Raises:
        JobNotVisibleError: No such job under this tenant (a 403).
        MissingDocumentError: The job names no document, or it is gone (a 404).
        AdmissionDeniedError: The tenant is at its in-flight cap (a 429).
        BudgetExceededError: The estimate does not fit the remaining budget (a 429).
    """
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, tenant_id)
        tenant_clause = (
            JobRun.tenant_id.is_(None)
            if tenant_id is None
            else JobRun.tenant_id == tenant_id
        )
        job = (
            await session.execute(
                select(JobRun).where(JobRun.id == job_id, tenant_clause)
            )
        ).scalar_one_or_none()
        if job is None:
            raise JobNotVisibleError(
                f"job {job_id} is not this tenant's to re-queue (no such row under "
                f"tenant {tenant_id})"
            )
        document_id = (job.payload or {}).get("document_id")
        document = (
            None
            if not isinstance(document_id, int)
            else (
                await session.execute(
                    select(Document).where(Document.id == document_id)
                )
            ).scalar_one_or_none()
        )
        if document is None:
            raise MissingDocumentError(
                f"job {job_id} names no document that still exists, so there is nothing "
                "to re-queue"
            )
        document_id = document.id
        estimate = await _estimate_usd(session, document, tenant_id=tenant_id)
        # The gate. Raises, and nothing below it runs — which is the whole guarantee: a
        # refused job leaves no execution in the orchestrator to be found later.
        await admit(
            session,
            tenant_id=tenant_id,
            job_type=job.job_type,
            estimated_cost_usd=estimate,
        )
        row = _row(job)

    client = await get_temporal_client()
    # ``{job_type}:{tenant}:{document}:{nonce}`` — the shape an operator greps for in the
    # orchestrator's UI, with a nonce because a re-queue is a new execution.
    workflow_id = f"{row.job_type}:{tenant_id}:{document_id}:{uuid4().hex[:12]}"
    await client.start_workflow(
        INGEST_WORKFLOW,
        IngestParams(tenant_id=tenant_id, document_id=document_id, user_id=user_id),
        id=workflow_id,
        task_queue=DEFAULT_QUEUE,
    )
    logger.info(
        "re-queued job %s for tenant %s as workflow %s (estimated $%.4f)",
        job_id,
        tenant_id,
        workflow_id,
        estimate,
    )
    return row


async def cancel_job(*, job_id: int, tenant_id: int | None, cancelled_by: str) -> JobRow:
    """Stop a tenant's job through the orchestrator and record who stopped it.

    Args:
        job_id: The row to cancel.
        tenant_id: The caller's tenant; the row must belong to it.
        cancelled_by: The acting principal's username, recorded on the row.

    Returns:
        The cancelled row.

    Raises:
        JobNotVisibleError: No such job under this tenant (a 403).
        JobNotCancellableError: The job already reached a terminal state (a 409).
        Exception: Whatever the orchestrator client raises when it cannot be reached.
            Not caught: a cancel that did not happen must not be reported as one.
    """

    async def stop(workflow_id: str) -> None:
        """Ask Temporal to cancel the execution behind ``workflow_id``.

        A cancellation *request*, not a terminate: the workflow is told to unwind, so an
        in-flight activity's own transaction still commits or rolls back cleanly rather
        than being severed mid-write.
        """
        client = await get_temporal_client()
        await client.get_workflow_handle(workflow_id).cancel()

    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, tenant_id)
        job = await _cancel_job_row(
            session,
            job_id,
            tenant_id=tenant_id,
            cancelled_by=cancelled_by,
            stop_workflow=stop,
        )
        row = _row(job)
        await session.commit()
    logger.info("job %s cancelled by %s (tenant %s)", job_id, cancelled_by, tenant_id)
    return row
