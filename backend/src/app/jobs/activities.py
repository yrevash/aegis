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
* the ``job_runs`` insert is ``ON CONFLICT (workflow_id) DO UPDATE``, because
  ``workflow_id`` is unique precisely so that two rows claiming one execution is a state
  the database refuses to hold. ``DO NOTHING`` was wrong here: a reconciler restart left
  the row reading ``RECONCILING`` for the whole of the run that was by then genuinely
  under way. The conflict path re-opens ``run_id``/``status`` and clears
  ``error``/``finished_at``, but deliberately does not re-stamp ``started_at`` — the
  execution began when it began.

The guard is in the ``WHERE`` clause rather than in a Python ``if``, and that is not
style: a read-then-write with the decision in Python is the SELECT-then-guarded-UPDATE
pattern this substrate replaces, and it loses under concurrency.

What the tenant watches (task 4.12)
-----------------------------------

Both writing activities also append to the durable run record — Phase 3 §3.6's
``run_events``, through :mod:`app.jobs.ingest_log`, **in the same transaction as the
write they describe**. That is the whole of the live ingest log's write side: there is no
second channel, no in-memory progress and nothing to reconstruct after a restart. An
entry is written only when the guarded ``UPDATE`` above actually changed a row, so a
replay records nothing, and because the entry and the ``completed_stage`` bump commit
together a worker killed between them is not a state the database can hold. The read side
is :mod:`app.ingestion.progress`, which is a projection and owns no state of its own.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Mapping
from datetime import UTC, datetime
from typing import Any

from aegis.jobs.facts import collect_stage_facts
from aegis.jobs.models import Document, JobRun, JobStatus
from aegis.jobs.scope import tenant_activity
from aegis.jobs.stages import stage_handler, stage_spec
from aegis.retrieval.corpus import bump_corpus_version
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
from app.jobs.ingest_log import run_finished, stage_committed

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
#:
#: ``title`` is here and ``doc_type``/``doc_date`` deliberately are **not**. The title is
#: *derived* — the parse stage reads it off the document's first heading — so a stage is
#: exactly the right writer for it. The other two can only be supplied by the tenant at
#: upload (see the correction under D7): nothing in the bytes states them, so a stage that
#: could write them could only ever be writing a guess, and a guessed document date is
#: indistinguishable from a real one once it is embedded into every chunk's prefix.
#:
#: ``parse_confidence`` is here for the same reason ``title`` is — it is measured by the
#: parse, against the bytes, and nothing else in the system is in a position to know it.
_HANDLER_WRITABLE_COLUMNS = frozenset(
    {
        "page_count",
        "chunk_count",
        "mime_type",
        "filename",
        "size_bytes",
        "title",
        "parse_confidence",
    }
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


async def _load_document(
    session: AsyncSession, document_id: int, *, lock: bool = False
) -> Document:
    """Load a document through the caller's tenant scope, or fail non-retryably.

    The read is a plain primary-key select with **no** ``WHERE tenant_id = ...``: the
    scope is bound on the connection, so Postgres' ``tenant_isolation`` policy is what
    hides another tenant's row. That is deliberate — it means this function is also the
    test of whether the policy is doing its job, rather than a Python filter that would
    pass whether or not RLS were enforced.

    Args:
        session: The scoped session supplied by ``@tenant_activity``.
        document_id: The document to load.
        lock: Take a ``FOR UPDATE`` row lock. :func:`run_stage` sets it, because the
            "has this stage already committed?" decision it makes next is a *read* that
            a second concurrent attempt would make identically — and two attempts of one
            stage running the handler twice is the double-billing this substrate exists
            to prevent. The lock is what turns that Python comparison from a race into a
            serialisation point; readers are unaffected, since ``FOR UPDATE`` blocks only
            other writers of the same row.

    Returns:
        The document row.

    Raises:
        ApplicationError: Non-retryable, when no such row is visible. "Deleted" and
            "belongs to another tenant" are indistinguishable here **by design**, and
            neither is fixed by trying again — retrying would burn the stage's whole
            attempt budget rediscovering the same absence.
    """
    statement = select(Document).where(Document.id == document_id)
    if lock:
        statement = statement.with_for_update()
    document = (await session.execute(statement)).scalar_one_or_none()
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

    The write is an **upsert on ``workflow_id``**, never a bare insert, and the columns it
    updates on conflict are exactly the ones that make a *re-claim* honest:

    * ``run_id`` — the orchestrator's new attempt id, so a support engineer taking
      ``(workflow_id, run_id)`` to the UI lands on the execution that is actually running;
    * ``status`` back to ``RUNNING`` — this is the path the reconciler's restart takes,
      and a row it left in ``RECONCILING`` must not stay there once a real execution has
      picked the work back up;
    * ``error`` and ``finished_at`` cleared — a row that has been re-opened is not a row
      that finished, and leaving a stale terminal timestamp on it would make every
      duration this platform reports wrong for that job.

    ``started_at`` is deliberately **not** re-stamped: it is when this job first began,
    which is the number a queue-wait or total-duration figure is measured from.

    Because those updates are computed from the argument and the orchestrator's context
    rather than accumulated, a replay writes the same values it wrote before — so calling
    this twice for one execution produces one job row and one claim.

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
    claim = pg_insert(JobRun).values(
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
    await session.execute(
        claim.on_conflict_do_update(
            index_elements=["workflow_id"],
            set_={
                "run_id": claim.excluded.run_id,
                "status": claim.excluded.status,
                "completed_stage": claim.excluded.completed_stage,
                "error": None,
                "finished_at": None,
            },
        )
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
    2. load the document *through the tenant scope* and **lock its row**, so another
       tenant's document is simply not there and a second concurrent attempt of this same
       stage waits rather than racing;
    3. return early if this stage already committed — the idempotency short-circuit that
       makes a replay free, and which the lock in step 2 is what makes safe: without it,
       two attempts would both read "not yet done" and both run the handler;
    4. run the registered handler, which does its own writes on this same session;
    5. apply the handler's column updates **and** the ``completed_stage`` bump in one
       guarded ``UPDATE``;
    6. append the stage's entry to the durable run record — but **only if step 5 changed a
       row**, so the log records work and never a replay.

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
    document = await _load_document(session, inp.document_id, lock=True)
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
    started = time.monotonic()
    # The handler's second, narrow channel out (:func:`aegis.jobs.report_stage_facts`):
    # what it *found*, as opposed to what it returns, which is what it *set*. Opened
    # here so a handler never has to know whether the substrate, a test or the re-index
    # loop is calling it — outside this scope reporting is a no-op.
    with collect_stage_facts() as facts:
        updates = await _run_with_heartbeat(
            handler(
                session,
                tenant_id=inp.tenant_id,
                document_id=inp.document_id,
                stage=inp.stage,
            ),
            spec.heartbeat_seconds,
        )
    duration_ms = int((time.monotonic() - started) * 1000)
    columns = _validated_updates(inp.stage, updates)
    result = await session.execute(
        update(Document)
        .where(
            Document.id == inp.document_id,
            Document.completed_stage.is_distinct_from(inp.stage),
        )
        .values(**columns, completed_stage=inp.stage)
    )
    job_id = (
        await session.execute(
            update(JobRun)
            .where(JobRun.workflow_id == inp.workflow_id)
            .values(completed_stage=inp.stage)
            .returning(JobRun.id)
        )
    ).scalar_one_or_none()
    if result.rowcount:
        # Task 4.12. In *this* transaction, so the entry and the stage bump it describes
        # commit together: a worker killed between them is not a state that exists, and
        # the log therefore cannot claim a stage the row does not.
        await stage_committed(
            session,
            workflow_id=inp.workflow_id,
            tenant_id=inp.tenant_id,
            document_id=inp.document_id,
            job_id=job_id,
            stage=inp.stage,
            queue=spec.task_queue,
            duration_ms=duration_ms,
            columns=columns,
            facts=facts,
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
    exactly the silent-stranding failure this substrate replaces, and
    :mod:`app.jobs.reconcile` is the backstop for the case where even this activity never
    gets to run.

    Both writes are guarded ``WHERE status IS DISTINCT FROM :status``, which is what makes
    a replay a no-op rather than a second close-out. Without the guard the second call
    would re-stamp ``finished_at`` with a later instant, and every duration this platform
    reports for that job would silently include the replay gap — the "double count" the
    guarded-update rule exists to prevent, in the one column where it would never look
    like an error.

    Where the corpus version is bumped, and why it is here (task 4.8)
    ----------------------------------------------------------------

    This activity is the **only** place an ingest bumps the tenant's
    :func:`aegis.retrieval.corpus.corpus_version`, and the position is the decision.

    Both caches in front of the agent key on that counter, so a bump makes every entry
    cached over the *old* corpus unreachable. Bumping it any earlier than here would
    invalidate the caches while the ingest is still running — and the requests that then
    miss the cache would be answered from a corpus that is half built: chunks written but
    not enriched, enriched but not embedded, embedded but not published to the dense
    index. Every one of those states answers questions, plausibly and wrongly, and the
    only thing worse than a stale answer is a confidently incomplete one. The other end —
    bumping when the ``documents`` row is *created* — is worse still: it invalidates the
    cache before a single byte has been parsed.

    So the bump happens once the run is terminal, in the same transaction that records it
    as terminal. Three properties follow, and each is the reason for a line below:

    * **It is guarded by the same ``WHERE`` the status write is.** ``RETURNING`` reports
      the rows that update actually changed, so a replayed close-out changes nothing and
      bumps nothing. Under-bumping is the dangerous direction (a stale cache is silent),
      but double-bumping on every replay would throw a tenant's whole cache away for
      free, and neither is necessary.
    * **A failed or cancelled run bumps too, if it got as far as writing chunks.**
      ``chunk_count`` is the honest test: it is ``NULL`` until the ``chunk`` stage
      commits, and from that moment the tenant's ``chunks`` rows — and therefore the
      keyword arm that searches them — are genuinely different from what any cached
      answer was computed over. Bumping only on success would leave exactly that case
      silently stale.
    * **It runs before the commit, not after.** The decorator commits when this returns,
      so a bump on a transaction that then rolls back is possible; it costs the tenant a
      cache miss. The reverse ordering would risk a committed ingest with no bump, which
      costs the tenant a wrong answer. The asymmetry decides the order.

    The counter is process-local (see :mod:`aegis.retrieval.corpus`), which is correct for
    the in-process worker the platform runs by default and is stated there rather than
    assumed here.

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
    # ``completed_stage`` comes back with the guard so the terminal log entry can say
    # *where* a failed run stopped, and ``id`` so "the guard matched no row" (a replayed
    # close-out) is distinguishable from "it matched a row whose chunk_count is NULL".
    # Reading ``chunk_count`` alone could not tell those apart, and only one of them is
    # a reason to write anything at all.
    row = (
        await session.execute(
            update(Document)
            .where(
                Document.id == inp.document_id,
                Document.status.is_distinct_from(status),
            )
            .values(status=status, error=inp.error)
            .returning(Document.id, Document.chunk_count, Document.completed_stage)
        )
    ).first()
    closed = row[1] if row is not None else None
    job_id = (
        await session.execute(
            update(JobRun)
            .where(
                JobRun.workflow_id == inp.workflow_id,
                JobRun.status.is_distinct_from(status),
            )
            .values(status=status, error=inp.error, finished_at=now)
            .returning(JobRun.id)
        )
    ).scalar_one_or_none()
    if row is not None:
        # Task 4.12's terminal entry, guarded by the same ``WHERE`` the status write is:
        # a replayed close-out changed no row and therefore records no second ending.
        await run_finished(
            session,
            workflow_id=inp.workflow_id,
            tenant_id=inp.tenant_id,
            document_id=inp.document_id,
            job_id=job_id,
            status=status.value,
            completed_stage=row[2],
            error=inp.error,
        )
    if closed:
        # ``closed`` is the row's ``chunk_count``, and it is ``None`` for a run that never
        # reached the ``chunk`` stage and ``None`` again when the guard above matched no
        # row (a replayed close-out). Either way nothing about the tenant's searchable
        # corpus changed on this call, so neither way is a bump.
        version = bump_corpus_version(inp.tenant_id)
        logger.info(
            "ingest of document %s finished %s with %d chunk(s); corpus version for "
            "tenant %s is now %d, so every answer cached over the previous corpus is "
            "unreachable",
            inp.document_id,
            status.value,
            closed,
            inp.tenant_id,
            version,
        )


#: Every activity a worker registers. Derived once, here, so adding an activity is one
#: edit rather than one edit plus a worker change somebody forgets.
ALL_ACTIVITIES = (start_ingest, run_stage, finish_ingest)
