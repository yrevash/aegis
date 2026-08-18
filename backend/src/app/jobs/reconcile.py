"""The reconciler — the sweeper that makes a stranded row impossible to hide.

:mod:`app.jobs.activities` covers one half of dual-write skew: an activity that commits to
PostgreSQL and dies before the orchestrator records its completion, which is why every
activity there is idempotent on ``(workflow_id, stage)``. **This module covers the other
half**, which no amount of idempotency can reach: a row that says ``RUNNING`` while the
execution behind it no longer exists — because the orchestrator's history was pruned, the
server was reset, the workflow was terminated externally, or the close-out activity was
the thing that never got to run.

Without a sweeper that row is *silent*. Nothing retries it, nothing times it out, nothing
reports it; the tenant watching the ingest sees a progress bar that will never move again.
That is precisely the failure mode the pre-existing consolidation job has today
(``aegis/src/aegis/memory/consolidate.py``: a worker killed mid-job strands its row in
``RUNNING`` forever, matched by no sweeper and retried by nothing), and ending it is why
this module exists.

How a verdict is reached
------------------------

For every open row older than a threshold, the reconciler asks the orchestrator what
became of that workflow id and reaches one of four verdicts. :func:`verdict_for` is a
**pure function** of the answer, so the decision table is provable without a server, a
database or a clock — and :data:`Verdict` is exhaustive over
:class:`temporalio.client.WorkflowExecutionStatus`, so a status the SDK adds later fails
the test rather than falling into a default branch nobody wrote down.

============================ =====================================================
Orchestrator says            Verdict
============================ =====================================================
``RUNNING`` / continued      **leave** — the job is fine; it is just slow.
no such workflow             **fail** — nothing will ever finish this. Say so.
``FAILED`` / ``TIMED_OUT``   **fail**, carrying the orchestrator's own reason.
``CANCELED``                 **cancel** — a deliberate stop is not a failure.
``COMPLETED`` / terminated   **restart** — no live execution, work possibly unfinished.
============================ =====================================================

Why "restart" is safe, and why it is not a retry loop
-----------------------------------------------------

A restart re-starts the *same workflow id*, and the workflow's first act is
:func:`app.jobs.activities.start_ingest`, which reads ``completed_stage`` off our row and
hands it to :func:`aegis.jobs.stages.remaining_stages`. So a document that already parsed
is not re-parsed; a workflow that had genuinely completed simply runs zero stages and
closes its row out. The restart is therefore *convergent*: each pass leaves strictly less
work, and a row that keeps coming back is a row whose stages keep failing — which the
stage retry policies, not this sweeper, are responsible for.

A restart is refused when the row records a ``cancelled_by``. Cancellation is a decision a
person made (task 3.4), and a sweeper that resurrected cancelled work would be overruling
them from a background thread.

Both skews of the sweep itself are self-healing, which is the property to check when
reading the ordering below:

* the orchestrator start succeeds and our transaction then rolls back — the row stays
  ``RUNNING`` and the restarted execution re-claims it, because ``start_ingest`` is an
  upsert;
* our transaction commits and the start fails — the row is left ``RECONCILING``, and
  ``RECONCILING`` is swept by the *next* pass exactly like ``RUNNING`` is.

Neither leaves a row nothing will look at again, which is the one outcome that would
reintroduce the bug this module closes.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from enum import StrEnum

from aegis.jobs.models import Document, JobRun, JobStatus
from aegis.jobs.scope import tenant_activity
from aegis.jobs.stages import DEFAULT_QUEUE
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from app.jobs.activities import _now
from app.jobs.client import get_temporal_client
from app.jobs.flows.contracts import (
    RECONCILE_STALE_RUNS,
    IngestParams,
    ReconcileParams,
    ReconcileReport,
)
from app.jobs.flows.ingest import INGEST_WORKFLOW

logger = logging.getLogger(__name__)

__all__ = [
    "RECONCILE_ACTIVITIES",
    "RECONCILE_STALE_RUNS",
    "Verdict",
    "reconcile_stale_runs",
    "verdict_for",
]


class Verdict(StrEnum):
    """What the reconciler decided to do about one open row.

    Four rather than the three the specification names, because ``CANCELLED`` is not
    ``FAILED``: a tenant who stopped their own ingest and later reads "failed: ..." on the
    row has been told something untrue about their own action, and the audit trail that
    ``cancelled_by`` exists to support would contradict the status beside it.
    """

    LEAVE = "leave"
    FAIL = "fail"
    CANCEL = "cancel"
    RESTART = "restart"


#: The statuses that mean "this execution is still live". Named rather than inlined
#: because the second one is easy to forget: a workflow that continued-as-new has a new
#: run id under the *same* workflow id, and reading it as "gone" would fail a job that is
#: running perfectly well.
_LIVE_STATUSES = frozenset(
    {WorkflowExecutionStatus.RUNNING, WorkflowExecutionStatus.CONTINUED_AS_NEW}
)

#: Terminal statuses that are a genuine failure of the work.
_FAILED_STATUSES = frozenset(
    {WorkflowExecutionStatus.FAILED, WorkflowExecutionStatus.TIMED_OUT}
)

#: Terminal statuses that leave our row open with no live execution and no evidence the
#: work is finished. ``COMPLETED`` belongs here and that is not a contradiction: if the
#: workflow completed while our row still says ``RUNNING``, the close-out activity is the
#: thing that never committed, and re-running the workflow is what closes it — at the cost
#: of zero stages, because every one of them is already recorded on the row.
_RESUMABLE_STATUSES = frozenset(
    {WorkflowExecutionStatus.COMPLETED, WorkflowExecutionStatus.TERMINATED}
)


def verdict_for(
    status: WorkflowExecutionStatus | None, *, cancelled_by: str | None
) -> tuple[Verdict, str]:
    """Decide what to do about one open row, from the orchestrator's answer alone.

    Pure, and deliberately so: the decision table is the part of the reconciler most
    likely to be wrong, and a pure function over an enum is the only version of it that
    can be proved exhaustively without a server, a database or a clock.

    Args:
        status: What the orchestrator reports for the row's workflow id, or ``None`` when
            it has no such workflow at all.
        cancelled_by: The row's ``cancelled_by``. A restart is refused when this is set,
            because a person stopped this work on purpose.

    Returns:
        The verdict and the human-readable reason to record with it. The reason is written
        to the row's ``error`` column for every non-``LEAVE`` verdict, because a job that
        changed state for a reason only the sweeper knew is a job nobody can support.

    Raises:
        ValueError: If the orchestrator reports a status this function does not classify.
            Failing is the point: a silently-defaulted unknown status would put rows into
            whichever branch happened to be last, and the substrate's whole claim is that
            no row is ever acted on for a reason nobody wrote down.
    """
    if status is None:
        return (
            Verdict.FAIL,
            "the orchestrator has no execution with this workflow id: its history was "
            "pruned, or the server was reset. Nothing will ever finish this run, so it "
            "is closed rather than left reading RUNNING forever.",
        )
    if status in _LIVE_STATUSES:
        return Verdict.LEAVE, ""
    if status in _FAILED_STATUSES:
        return (
            Verdict.FAIL,
            f"the workflow ended {status.name} in the orchestrator, but the run was never "
            "closed out in the record layer — the close-out activity did not commit.",
        )
    if status is WorkflowExecutionStatus.CANCELED:
        return (
            Verdict.CANCEL,
            "the workflow was cancelled in the orchestrator and the record layer had not "
            "caught up.",
        )
    if status in _RESUMABLE_STATUSES:
        if cancelled_by is not None:
            return (
                Verdict.CANCEL,
                f"the workflow ended {status.name} after being cancelled by "
                f"{cancelled_by!r}; a sweeper does not resurrect work a person stopped.",
            )
        return (
            Verdict.RESTART,
            f"the workflow ended {status.name} with the run still open in the record "
            "layer; it is re-started, which resumes after the last committed stage rather "
            "than repeating it.",
        )
    raise ValueError(
        f"workflow status {status!r} is not classified by the reconciler. Add it to the "
        "decision table deliberately: defaulting an unrecognised status would act on live "
        "tenant jobs for a reason nobody wrote down."
    )


async def _describe(client: Client, workflow_id: str) -> WorkflowExecutionStatus | None:
    """Ask the orchestrator what became of one workflow id.

    Args:
        client: The connected Temporal client.
        workflow_id: The id carried on our row.

    Returns:
        The execution's status, or ``None`` when the orchestrator has no such workflow.

    Raises:
        RPCError: Any failure other than ``NOT_FOUND``. Deliberately not caught: an
            unreachable orchestrator must abort the sweep, because treating "I could not
            ask" as "it is gone" would fail every live job on the platform the moment the
            server hiccuped.
    """
    try:
        description = await client.get_workflow_handle(workflow_id).describe()
    except RPCError as exc:
        if exc.status is RPCStatusCode.NOT_FOUND:
            return None
        raise
    return description.status


async def _restart_ingest(client: Client, row: JobRun) -> None:
    """Re-start an ingest workflow under its original id.

    Args:
        client: The connected Temporal client.
        row: The open ``job_runs`` row, whose ``payload`` carries the document id.

    Raises:
        KeyError: If the row's payload has no ``document_id``. Not defended against: a
            job row of type ``ingest`` without one is corrupt, and guessing a document to
            re-ingest would be worse than the exception.
    """
    await client.start_workflow(
        INGEST_WORKFLOW,
        IngestParams(tenant_id=row.tenant_id, document_id=row.payload["document_id"]),
        id=row.workflow_id,
        task_queue=DEFAULT_QUEUE,
    )


#: How each job type is re-started, keyed by ``job_runs.job_type``.
#:
#: A job type absent from this map is **failed with a reason naming it**, never silently
#: left: "we do not know how to restart this" is a fact an operator needs, and a row that
#: no branch touches is the stranded row this module exists to abolish. ``reindex`` is
#: deliberately absent — a re-index is driven by its own schedule and debounce window
#: (§3.5), so resurrecting one lost execution is the wrong repair when the next cadence
#: tick already covers it.
_RESTARTERS: dict[str, Callable[[Client, JobRun], Awaitable[None]]] = {
    "ingest": _restart_ingest,
}


async def _close_row(
    session: AsyncSession, row: JobRun, status: JobStatus, reason: str
) -> None:
    """Write a terminal state and its reason to the job row and its document.

    Args:
        session: The platform-scoped session.
        row: The row being closed.
        status: The terminal status to write.
        reason: Why, recorded on both rows so the tenant is told something rather than
            watching a status change for no visible cause.
    """
    await session.execute(
        update(JobRun)
        .where(JobRun.id == row.id, JobRun.status.is_distinct_from(status))
        .values(status=status, error=reason, finished_at=_now())
    )
    document_id = row.payload.get("document_id")
    if document_id is not None:
        await session.execute(
            update(Document)
            .where(Document.id == document_id, Document.status.is_distinct_from(status))
            .values(status=status, error=reason)
        )


@activity.defn(name=RECONCILE_STALE_RUNS)
@tenant_activity(allow_platform_scope=True)
async def reconcile_stale_runs(
    inp: ReconcileParams, *, session: AsyncSession
) -> ReconcileReport:
    """Sweep every tenant's open job rows and reconcile each against the orchestrator.

    The one activity in this package that runs with ``allow_platform_scope=True``, because
    a per-tenant sweeper is not a sweeper: the rows most in need of reconciliation belong
    to whichever tenant had the outage, and asking each tenant's scope in turn would need
    a list of tenants that this module would then have to keep in step with reality.

    The rows swept are ``RUNNING`` **and** ``RECONCILING``. Including the second is what
    makes the sweep self-healing rather than a source of a new stuck state: a row whose
    restart was ordered but whose orchestrator call then failed is examined again on the
    next pass, instead of resting in a status nothing looks at.

    Args:
        inp: The staleness threshold and the batch limit. ``tenant_id`` is ``None``.
        session: The platform-scoped session supplied by ``@tenant_activity``.

    Returns:
        Counts of what the sweep examined and did.

    Raises:
        RPCError: If the orchestrator is unreachable. The sweep aborts rather than
            treating "I could not ask" as "the workflow is gone" — which would close every
            live job on the platform.
        ValueError: If the orchestrator reports a status :func:`verdict_for` does not
            classify.
    """
    cutoff = _now() - timedelta(seconds=inp.stale_after_seconds)
    rows = list(
        (
            await session.execute(
                select(JobRun)
                .where(
                    JobRun.status.in_((JobStatus.RUNNING, JobStatus.RECONCILING)),
                    func.coalesce(JobRun.started_at, JobRun.created_at) < cutoff,
                )
                .order_by(JobRun.created_at)
                .limit(inp.limit)
            )
        ).scalars()
    )
    if not rows:
        return ReconcileReport(examined=0, left=0, failed=0, cancelled=0, restarted=0)

    client = await get_temporal_client()
    tally = dict.fromkeys(Verdict, 0)
    for row in rows:
        verdict, reason = verdict_for(
            await _describe(client, row.workflow_id), cancelled_by=row.cancelled_by
        )
        restarter = _RESTARTERS.get(row.job_type)
        if verdict is Verdict.RESTART and restarter is None:
            verdict = Verdict.FAIL
            reason = (
                f"the workflow is no longer running and job type {row.job_type!r} has no "
                "registered restart path, so this run cannot be resumed; it is closed "
                "with that reason rather than left reading RUNNING forever."
            )
        tally[verdict] += 1
        if verdict is Verdict.LEAVE:
            continue
        logger.warning(
            "reconciler: job_runs.id=%s (%s, workflow %s) -> %s: %s",
            row.id,
            row.job_type,
            row.workflow_id,
            verdict.value,
            reason,
        )
        if verdict is Verdict.FAIL:
            await _close_row(session, row, JobStatus.FAILED, reason)
        elif verdict is Verdict.CANCEL:
            await _close_row(session, row, JobStatus.CANCELLED, reason)
        else:
            await session.execute(
                update(JobRun)
                .where(JobRun.id == row.id)
                .values(status=JobStatus.RECONCILING, error=reason)
            )
            # After the row is marked, so that a start that succeeds while the
            # transaction later rolls back leaves the row RUNNING — re-claimed by the
            # execution just started — rather than RECONCILING with nothing behind it.
            await restarter(client, row)  # type: ignore[misc]  # None handled above
    return ReconcileReport(
        examined=len(rows),
        left=tally[Verdict.LEAVE],
        failed=tally[Verdict.FAIL],
        cancelled=tally[Verdict.CANCEL],
        restarted=tally[Verdict.RESTART],
    )


#: Every activity this module contributes to a worker's registration.
RECONCILE_ACTIVITIES = (reconcile_stale_runs,)
