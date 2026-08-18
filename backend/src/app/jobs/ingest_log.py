"""The ingest's entries in the durable run record — task 4.12's write side.

**There is no second log here, and that is the whole design.** Phase 3 §3.6 already
built the append-only, tenant-scoped, replayable record (``run_events`` plus the
regenerable ``runs`` header, :mod:`aegis.runs.record`), and the tenant watching a
document being read is watching *job progress*. So an ingest writes its stage
transitions into that record, through :func:`aegis.runs.record.record_events`, and
:mod:`app.ingestion.progress` projects them back out. Nothing in this module owns a
table, a stream or a vocabulary of its own beyond the two event type names below.

Three properties make the log unable to lie, and each is a line of code rather than a
convention:

**An event commits with the work it describes.** :func:`stage_committed` is called on the
session the stage handler wrote its rows on, inside the transaction that also carries the
``completed_stage`` bump. So "the log says ``chunk`` finished" and "the row says ``chunk``
finished" are the same commit: a worker hard-killed between them is not a state the
database can hold.

**Sequence numbers are a function of the pipeline, not of a counter.** A stage's ``seq``
is its index in :data:`aegis.jobs.INGEST_STAGES`, so a replay of an already-committed
stage would write the number it wrote before rather than appending a second, later-looking
entry. Combined with the substrate's replay short-circuit (which returns before the
handler runs at all) the effect is that a resumed run adds entries only for stages that
genuinely ran in it.

**A resume does not re-open the past.** The events of the stages an earlier attempt
committed stay exactly where they were, under that attempt's ``run_id``; the projection
reads every run of the document, so re-queueing produces a longer log rather than a
rewritten one.

``run_id`` is the ``workflow_id``, because that string already *is* the identity of this
execution everywhere else in the substrate — on ``job_runs``, on ``documents``, and in the
orchestrator's own UI. Both producers of one (``app.ingestion.upload`` writes
``ingest:{tenant}:{document}``, ``app.jobs.control`` appends a 12-hex suffix on a re-queue)
are far inside ``run_events.run_id``'s 64 characters; :func:`_run_id` refuses a longer one
rather than truncating it, because a truncated id would silently merge two runs' logs.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from aegis.jobs.stages import INGEST_STAGES, stage_names
from aegis.runs.record import RunPartitionMissingError, record_events
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

__all__ = [
    "INGEST_FINISHED_EVENT",
    "INGEST_STAGE_EVENT",
    "finished_seq",
    "ingest_run_id",
    "stage_committed",
    "stage_seq",
    "run_finished",
]

#: The event type one committed ingest stage is recorded under.
#:
#: A new type name rather than a reused agent one: :func:`aegis.runs.record.apply_event`
#: folds ``run_finished`` into ``runs.status``, which is a :class:`aegis.core.types.
#: RunStatus` — an *agent* run's terminal vocabulary, with no member meaning "cancelled"
#: and none meaning "succeeded with a low-confidence parse". Borrowing it would make the
#: header claim an outcome the ingest never had. Unknown types are stored and counted by
#: that fold and rejected by nothing, which is exactly the contract this needs.
INGEST_STAGE_EVENT = "ingest_stage"

#: The event type the close-out is recorded under. Written only when the close-out
#: actually moved the row, so a replayed ``finish_ingest`` adds nothing.
INGEST_FINISHED_EVENT = "ingest_finished"

#: The stage names, in pipeline order, indexed once.
_STAGE_ORDER: dict[str, int] = {name: index for index, name in enumerate(stage_names())}

#: ``run_events.run_id`` is ``String(64)``; see the module docstring for why a longer
#: workflow id is an error rather than a truncation.
_MAX_RUN_ID = 64


def stage_seq(stage: str) -> int:
    """Return the monotonic per-run sequence number one stage's event carries.

    Derived from the stage's position in :data:`aegis.jobs.INGEST_STAGES` rather than
    from a counter, so the number an event gets is a property of *which stage* it is and
    not of when it happened to be written. That is what makes the log replay-stable.

    Args:
        stage: The stage name.

    Returns:
        The sequence number, ``1``-based so the close-out's number is distinct from a
        stage's and every event of a run has a positive ``seq``.

    Raises:
        KeyError: If the pipeline declares no such stage. The caller has already
            validated the name against :func:`aegis.jobs.stages.stage_spec`, so reaching
            this would mean two disagreeing pipelines in one process.
    """
    return _STAGE_ORDER[stage] + 1


def finished_seq() -> int:
    """Return the sequence number the terminal ingest event carries.

    Returns:
        One past the last stage's, so the close-out sorts after every stage whether or
        not the run reached them all.
    """
    return len(INGEST_STAGES) + 1


def ingest_run_id(workflow_id: str) -> str:
    """Return the ``run_events.run_id`` for one ingest execution.

    Args:
        workflow_id: The orchestrator execution id, which is also the ``job_runs``
            idempotency key.

    Returns:
        The same string — the identity is not re-invented.

    Raises:
        ValueError: If it does not fit ``run_events.run_id``. Truncating would merge two
            executions' logs into one under a shared prefix, which is worse than not
            logging at all.
    """
    if len(workflow_id) > _MAX_RUN_ID:
        raise ValueError(
            f"workflow id {workflow_id!r} is {len(workflow_id)} characters; "
            f"run_events.run_id holds {_MAX_RUN_ID}. Truncating it would file two "
            "executions' events under one id."
        )
    return workflow_id


async def _append(
    session: AsyncSession,
    *,
    workflow_id: str,
    tenant_id: int | None,
    job_id: int | None,
    event: Mapping[str, Any],
) -> None:
    """Append one ingest event, and never fail the work it describes.

    The stage's rows and its ``completed_stage`` bump are the tenant's *data*; this entry
    is the account of it. If the record layer cannot take the entry — the most likely
    cause being a monthly partition that was never rolled forward — losing a log line is
    strictly better than rolling back a parse that succeeded, so the failure is logged
    loudly and swallowed. Every other exception propagates: an unexpected database error
    inside this transaction has already poisoned it, and pretending otherwise would
    commit a stage whose write failed.

    Args:
        session: The stage's own session, inside its own transaction.
        workflow_id: The execution this event belongs to.
        tenant_id: The owning tenant, which is also the scope bound on the session.
        job_id: The ``job_runs`` row, when it is known.
        event: The stamped wire event.
    """
    try:
        await record_events(
            session,
            run_id=ingest_run_id(workflow_id),
            events=[event],
            tenant_id=tenant_id,
            job_id=job_id,
        )
    except RunPartitionMissingError:
        logger.exception(
            "the ingest log entry for %s could not be written: run_events has no "
            "partition for now. The work itself committed; roll the partitions forward "
            "with aegis.runs.partitions.ensure_run_event_partitions()",
            workflow_id,
        )


async def stage_committed(
    session: AsyncSession,
    *,
    workflow_id: str,
    tenant_id: int | None,
    document_id: int,
    job_id: int | None,
    stage: str,
    queue: str,
    duration_ms: int,
    columns: Mapping[str, Any],
    facts: Mapping[str, Any],
) -> None:
    """Record that one stage ran and what it found.

    Args:
        session: The stage's session, inside the transaction its work commits in.
        workflow_id: The execution.
        tenant_id: The owning tenant.
        document_id: The document.
        job_id: The ``job_runs`` row this execution claimed.
        stage: The stage that committed.
        queue: The task queue it ran on, carried so the log can show *where* the time
            went without the reader having to know the pipeline's queue assignment.
        duration_ms: Wall clock inside the handler.
        columns: The ``documents`` columns the handler returned — the row-shaped half of
            what it discovered (``page_count``, ``chunk_count``, ``parse_confidence``).
        facts: What the handler reported through
            :func:`aegis.jobs.stages.report_stage_facts` — the evidence half, which has
            nowhere on a row to live.
    """
    await _append(
        session,
        workflow_id=workflow_id,
        tenant_id=tenant_id,
        job_id=job_id,
        event={
            "type": INGEST_STAGE_EVENT,
            "seq": stage_seq(stage),
            "stage": stage,
            "queue": queue,
            "document_id": document_id,
            "duration_ms": duration_ms,
            "columns": {key: value for key, value in columns.items()},
            "facts": dict(facts),
        },
    )


async def run_finished(
    session: AsyncSession,
    *,
    workflow_id: str,
    tenant_id: int | None,
    document_id: int,
    job_id: int | None,
    status: str,
    completed_stage: str | None,
    error: str | None,
) -> None:
    """Record that an ingest reached a terminal state.

    Args:
        session: The close-out's session, inside its transaction.
        workflow_id: The execution.
        tenant_id: The owning tenant.
        document_id: The document.
        job_id: The ``job_runs`` row, when known.
        status: The terminal :class:`aegis.jobs.JobStatus` value, as its string.
        completed_stage: How far the pipeline got, so a failure's entry says where it
            stopped rather than only that it stopped.
        error: The failure reason, or ``None``.
    """
    await _append(
        session,
        workflow_id=workflow_id,
        tenant_id=tenant_id,
        job_id=job_id,
        event={
            "type": INGEST_FINISHED_EVENT,
            "seq": finished_seq(),
            "document_id": document_id,
            "status": status,
            "completed_stage": completed_stage,
            "error": error,
        },
    )
