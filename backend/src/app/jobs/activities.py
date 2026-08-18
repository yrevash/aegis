"""The activity implementations — scoped, idempotent, one transaction per stage.

Three activities carry the whole ingest pipeline, and the shape is deliberate: the *work*
of a stage is not here. :func:`run_stage` owns the parts that must be identical for every
stage — the tenant scope, the "have I already done this" check, the single transaction,
the ``completed_stage`` bump — and delegates the domain work to the handler registered in
:mod:`aegis.jobs.stages`. Phase 4 registers a Docling parse against ``parse`` without
touching a line of orchestration, and cannot accidentally write a stage that commits
progress it did not make.

Every activity is decorated ``@tenant_activity``, so none of them can open a session:
they are handed one, already bound to the tenant on their own argument, already inside
the transaction their write will commit in. That is the phase's isolation guarantee
expressed structurally — see :mod:`aegis.jobs.scope`.

Idempotency, because replay is measured and not hypothetical
------------------------------------------------------------

§3.0 hard-killed a worker mid-run and watched the in-flight activity replay in a fresh
process. An activity can therefore commit to Postgres and die before the orchestrator
records its completion, and then run again. Both writing activities here are keyed on
``(workflow_id, stage)`` and guarded in SQL:

* the ``completed_stage`` bump is ``UPDATE ... WHERE completed_stage IS DISTINCT FROM
  :stage``, so a replay updates zero rows instead of double-counting;
* the ``job_runs`` insert is ``ON CONFLICT (workflow_id) DO NOTHING``, because
  ``workflow_id`` is unique precisely so that two rows claiming one execution is a state
  the database refuses to hold.

The guard is in the ``WHERE`` clause rather than in a Python ``if``, and that is not
style: a read-then-write with the decision in Python is the SELECT-then-guarded-UPDATE
pattern this substrate replaces, and it loses under concurrency.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Mapping
from datetime import UTC, datetime
from typing import Any

from aegis.jobs.models import Document, JobRun, JobStatus
from aegis.jobs.scope import tenant_activity
from aegis.jobs.stages import stage_handler, stage_spec
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.jobs.flows.contracts import (
    FINISH_INGEST,
    RUN_STAGE,
    START_INGEST,
    FinishInput,
    StageInput,
    StageOutcome,
    StartOutcome,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ALL_ACTIVITIES",
    "FINISH_INGEST",
    "RUN_STAGE",
    "START_INGEST",
    "finish_ingest",
    "run_stage",
    "start_ingest",
]

#: The ``documents`` columns a stage handler is permitted to set. A handler returns a
#: mapping of column updates and the substrate applies them, so this allow-list is what
#: stops a handler from writing ``tenant_id`` (moving a document between tenants) or
#: ``completed_stage`` (claiming progress the substrate did not verify).
_HANDLER_WRITABLE_COLUMNS = frozenset(
    {"page_count", "chunk_count", "mime_type", "filename", "size_bytes"}
)


def _now() -> datetime:
    """Return the current UTC instant as an aware datetime.

    Every timestamp column in the record layer is ``timestamptz``, so a naive value
    raises at bind time rather than being silently reinterpreted.

    Returns:
        The current time, UTC-aware.
    """
    return datetime.now(UTC)


def _run_id() -> str | None:
    """Return the orchestrator's run id for this activity, if there is one.

    Guarded on :func:`temporalio.activity.in_activity` so the activity bodies stay
    directly callable — which is what lets the isolation and idempotency tests invoke
    them twice with no server, no worker and no workflow in sight. An activity that could
    only be exercised through a running cluster would be an activity nobody exercises.

    Returns:
        The workflow *run* id (the attempt identifier under the workflow id), or ``None``
        when called outside an activity context.
    """
    if not activity.in_activity():
        return None
    return activity.info().workflow_run_id


async def _run_with_heartbeat(work: Awaitable[Any], interval_seconds: float) -> Any:  # noqa: ANN401
    """Await ``work``, reporting liveness to the orchestrator while it runs.

    A ``SIGKILL``ed worker tells the orchestrator nothing, so without this a stage
    interrupted mid-flight would stay "running" until its ``start_to_close_timeout`` —
    up to half an hour for a parse — before anything retried it. The heartbeat is what
    makes an interrupted run resumable in seconds, which is the difference between the
    phase's kill-and-recover demo working and appearing to hang.

    Outside an activity context it is a plain ``await``, so the activity bodies stay
    directly callable from a test.

    Args:
        work: The awaitable to run — the stage handler's coroutine.
        interval_seconds: How often to beat while it is still running. The SDK throttles
            outbound beats against the timeout, so a short interval costs little.

    Returns:
        Whatever ``work`` returned.

    Raises:
        BaseException: Whatever ``work`` raised, after the beating task is stopped.
    """
    if not activity.in_activity():
        return await work
    task = asyncio.ensure_future(work)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=interval_seconds)
            if done:
                break
            activity.heartbeat()
    except BaseException:
        # Includes cancellation: the orchestrator cancelling this activity must stop the
        # handler too, rather than leaving it writing into a transaction nobody will
        # commit.
        task.cancel()
        raise
    return task.result()


async def _load_document(session: AsyncSession, document_id: int) -> Document:
    """Load a document through the caller's tenant scope, or fail non-retryably.

    The read is a plain primary-key select with **no** ``WHERE tenant_id = ...``: the
    scope is bound on the connection, so Postgres' ``tenant_isolation`` policy is what
    hides another tenant's row. That is deliberate — it means this function is also the
    test of whether the policy is doing its job, rather than a Python filter that would
    pass whether or not RLS were enforced.

    Args:
        session: The scoped session supplied by ``@tenant_activity``.
        document_id: The document to load.

    Returns:
        The document row.

    Raises:
        ApplicationError: Non-retryable, when no such row is visible. "Deleted" and
            "belongs to another tenant" are indistinguishable here **by design**, and
            neither is fixed by trying again — retrying would burn the stage's whole
            attempt budget rediscovering the same absence.
    """
    document = (
        await session.execute(select(Document).where(Document.id == document_id))
    ).scalar_one_or_none()
    if document is None:
        raise ApplicationError(
            f"document {document_id} is not visible under this tenant scope: it does not "
            "exist, or it belongs to another tenant",
            type="DocumentNotVisible",
            non_retryable=True,
        )
    return document


@activity.defn(name=START_INGEST)
@tenant_activity
async def start_ingest(
    inp: StageInput, *, session: AsyncSession
) -> StartOutcome:
    """Claim the run: create or adopt its ``job_runs`` row and report where to resume.

    Reuses :class:`~app.jobs.flows.contracts.StageInput` rather than declaring a fourth
    argument type; its ``stage`` field carries the *job type* here, which keeps one shape
    on the wire for every activity in this module.

    The insert is ``ON CONFLICT (workflow_id) DO NOTHING`` and the document update is
    guarded, so calling this twice for one workflow — which a replay does — produces one
    job row and one claim.

    Args:
        inp: The tenant, workflow id, document and job type.
        session: The scoped session supplied by ``@tenant_activity``.

    Returns:
        The document id, the stage it has already completed (``None`` for a fresh
        upload), and the id of the ``job_runs`` row.

    Raises:
        ApplicationError: If the document is not visible under this tenant's scope.
    """
    document = await _load_document(session, inp.document_id)
    now = _now()
    await session.execute(
        pg_insert(JobRun)
        .values(
            tenant_id=inp.tenant_id,
            job_type=inp.stage,
            workflow_id=inp.workflow_id,
            run_id=_run_id(),
            status=JobStatus.RUNNING,
            completed_stage=document.completed_stage,
            payload={"document_id": inp.document_id},
            result={},
            started_at=now,
        )
        .on_conflict_do_nothing(index_elements=["workflow_id"])
    )
    job_run_id = (
        await session.execute(
            select(JobRun.id).where(JobRun.workflow_id == inp.workflow_id)
        )
    ).scalar_one()
    await session.execute(
        update(Document)
        .where(Document.id == inp.document_id)
        .values(status=JobStatus.RUNNING, workflow_id=inp.workflow_id)
    )
    return StartOutcome(
        document_id=inp.document_id,
        completed_stage=document.completed_stage,
        job_run_id=job_run_id,
    )


def _validated_updates(stage: str, updates: Mapping[str, Any]) -> dict[str, Any]:
    """Check a handler's column updates against the allow-list.

    Args:
        stage: The stage whose handler produced them, for the error message.
        updates: The mapping the handler returned.

    Returns:
        The updates, as a plain dict ready to hand to ``update().values()``.

    Raises:
        ApplicationError: Non-retryable, if the handler tried to write a column outside
            :data:`_HANDLER_WRITABLE_COLUMNS`. Retrying cannot help — the handler will
            return the same mapping every time — and the two columns this refuses most
            firmly are ``tenant_id`` (which would move a document between tenants) and
            ``completed_stage`` (which would let a handler claim progress the substrate
            never verified).
    """
    forbidden = sorted(set(updates) - _HANDLER_WRITABLE_COLUMNS)
    if forbidden:
        raise ApplicationError(
            f"stage {stage!r} handler tried to write {forbidden} on documents; a handler "
            f"may set only {sorted(_HANDLER_WRITABLE_COLUMNS)}. completed_stage and "
            "tenant_id are the substrate's to write, never a handler's.",
            type="ForbiddenStageWrite",
            non_retryable=True,
        )
    return dict(updates)


@activity.defn(name=RUN_STAGE)
@tenant_activity
async def run_stage(inp: StageInput, *, session: AsyncSession) -> StageOutcome:
    """Run one stage and commit its output and its progress in one transaction.

    The order is the contract:

    1. validate the stage name against :data:`aegis.jobs.INGEST_STAGES` — an unknown name
       would be written to ``completed_stage`` and break every future resume of the row;
    2. load the document *through the tenant scope*, so another tenant's document is
       simply not there;
    3. return early if this stage already committed — the idempotency short-circuit that
       makes a replay free;
    4. run the registered handler, which does its own writes on this same session;
    5. apply the handler's column updates **and** the ``completed_stage`` bump in one
       guarded ``UPDATE``.

    The decorator commits once, after this returns. So a stage that "finished" but whose
    output rolled back is not a state the schema can reach — which is what makes each
    stage individually correct, on top of the resumability the orchestrator gives between
    stages.

    Args:
        inp: The tenant, workflow id, document and stage name.
        session: The scoped session supplied by ``@tenant_activity``.

    Returns:
        The outcome, whose ``committed`` flag says whether this call did the work or
        found it already done.

    Raises:
        ApplicationError: Non-retryable, if the stage name is not one this pipeline
            declares, if the document is not visible under this tenant's scope, or if no
            handler is registered for the stage. None of the three is fixed by retrying.
    """
    try:
        spec = stage_spec(inp.stage)
    except LookupError as exc:
        raise ApplicationError(
            str(exc), type="UnknownStage", non_retryable=True
        ) from exc
    document = await _load_document(session, inp.document_id)
    if document.completed_stage == inp.stage:
        logger.info(
            "stage %s already committed for document %s (workflow %s) — replay, no-op",
            inp.stage,
            inp.document_id,
            inp.workflow_id,
        )
        return StageOutcome(stage=inp.stage, document_id=inp.document_id, committed=False)
    try:
        handler = stage_handler(inp.stage)
    except LookupError as exc:
        raise ApplicationError(
            str(exc), type="UnregisteredStage", non_retryable=True
        ) from exc
    updates = await _run_with_heartbeat(
        handler(
            session,
            tenant_id=inp.tenant_id,
            document_id=inp.document_id,
            stage=inp.stage,
        ),
        spec.heartbeat_seconds,
    )
    result = await session.execute(
        update(Document)
        .where(
            Document.id == inp.document_id,
            Document.completed_stage.is_distinct_from(inp.stage),
        )
        .values(**_validated_updates(inp.stage, updates), completed_stage=inp.stage)
    )
    await session.execute(
        update(JobRun)
        .where(JobRun.workflow_id == inp.workflow_id)
        .values(completed_stage=inp.stage)
    )
    return StageOutcome(
        stage=inp.stage,
        document_id=inp.document_id,
        committed=bool(result.rowcount),
    )


@activity.defn(name=FINISH_INGEST)
@tenant_activity
async def finish_ingest(inp: FinishInput, *, session: AsyncSession) -> None:
    """Record the run's terminal state on both the document and the job row.

    This exists so a finished run is finished **in our tables**, not only in the
    orchestrator's history. A row left in ``RUNNING`` with no live execution behind it is
    exactly the silent-stranding failure this substrate replaces, and the reconciler
    (task 3.3) is the backstop for the case where even this activity never gets to run.

    Args:
        inp: The tenant, workflow id, document, terminal status and failure reason.
        session: The scoped session supplied by ``@tenant_activity``.

    Raises:
        ApplicationError: Non-retryable, if ``status`` is not a terminal
            :class:`aegis.jobs.JobStatus` value. Writing ``RUNNING`` here would leave the
            row stranded by the very call meant to un-strand it.
    """
    try:
        status = JobStatus(inp.status)
    except ValueError as exc:
        raise ApplicationError(
            f"{inp.status!r} is not a JobStatus", type="UnknownJobStatus", non_retryable=True
        ) from exc
    if status not in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
        raise ApplicationError(
            f"{status} is not a terminal status; finishing a run into it would strand "
            "the row this activity exists to close",
            type="NonTerminalStatus",
            non_retryable=True,
        )
    now = _now()
    await session.execute(
        update(Document)
        .where(Document.id == inp.document_id)
        .values(status=status, error=inp.error)
    )
    await session.execute(
        update(JobRun)
        .where(JobRun.workflow_id == inp.workflow_id)
        .values(status=status, error=inp.error, finished_at=now)
    )


#: Every activity a worker registers. Derived once, here, so adding an activity is one
#: edit rather than one edit plus a worker change somebody forgets.
ALL_ACTIVITIES = (start_ingest, run_stage, finish_ingest)
