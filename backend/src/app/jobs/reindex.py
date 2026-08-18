"""The re-index activity, its handler registry, and the one way to ask for a re-index.

Yash's own requirement is what puts this in the platform spine rather than in Phase 4:
*"re indexing pipeline can be structured in a way that it runs in set duration and in the
meantime user own db take care."* Two halves follow from that sentence, and this module is
the second of them:

* the **cadence** is a Temporal Schedule (:mod:`app.jobs.schedules`), not a table anybody
  maintains — no ``next_run_at`` column, no sweeper reading it, no clock of our own;
* the **burst** is debounced (:mod:`app.jobs.flows.reindex`), so ten documents uploaded in
  a minute produce one re-index rather than ten.

:func:`request_reindex` is the only supported way to ask for one, and it is a
signal-with-start against the per-tenant workflow id. That is what makes the fold
structural: a caller cannot express "start a second re-index for this tenant", because the
id is derived rather than passed.

Why the work itself is a registered handler
-------------------------------------------

Exactly as with :func:`aegis.jobs.stages.register_stage_handler`, and for the same reason:
the substrate owns the transaction, the tenant scope and the record row, while the domain
work — walking a tenant's chunks and rebuilding a vector and FTS index — is Phase 4's.
There is deliberately **no default handler**. A re-index with nothing registered raises
rather than recording a successful run that did nothing, because a ``job_runs`` row saying
``succeeded`` for work that never happened is worse than no row at all: it is the platform
lying to the tenant about the freshness of their own index.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from aegis.jobs.models import Document, JobRun, JobStatus
from aegis.jobs.scope import tenant_activity
from aegis.jobs.stages import DEFAULT_QUEUE
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity
from temporalio.client import Client, WorkflowHandle

from app.config import get_settings
from app.jobs.activities import _now, _run_id
from app.jobs.client import get_temporal_client
from app.jobs.flows.contracts import (
    REQUEST_REINDEX,
    RUN_REINDEX,
    ReindexInput,
    ReindexParams,
    ReindexRequest,
    ReindexResult,
    ReindexTickInput,
)
from app.jobs.flows.reindex import REINDEX_SIGNAL, REINDEX_WORKFLOW, reindex_workflow_id

logger = logging.getLogger(__name__)

__all__ = [
    "REINDEX_ACTIVITIES",
    "REQUEST_REINDEX",
    "RUN_REINDEX",
    "ReindexHandler",
    "UnregisteredReindexError",
    "clear_reindex_handler",
    "register_reindex_handler",
    "reindex_handler",
    "request_reindex",
    "request_tenant_reindex",
    "run_reindex",
]

#: The ``job_runs.job_type`` a re-index is recorded under. A constant because the console
#: groups by it and :mod:`app.jobs.reconcile` keys its restart table on it.
REINDEX_JOB_TYPE = "reindex"


class UnregisteredReindexError(LookupError):
    """A re-index ran with no handler registered to perform it.

    Deliberately not a no-op: see the module docstring. A successful-looking row for work
    that never happened is a lie about index freshness, and index freshness is the only
    thing a re-index exists to guarantee.
    """


class ReindexHandler(Protocol):
    """The domain work of a re-index, as the substrate calls it.

    The substrate has opened the session, bound the tenant scope and opened the single
    transaction the record row will be written in. The handler rebuilds the index on that
    same session — a second session would be a second transaction, and then a re-index
    that "succeeded" could have its work rolled back while its row survived.
    """

    async def __call__(
        self,
        session: AsyncSession,
        *,
        tenant_id: int | None,
        folded: int,
        reasons: tuple[str, ...],
    ) -> Mapping[str, Any]:
        """Rebuild the tenant's index and return what it did.

        Args:
            session: The scoped session, inside the run's single transaction.
            tenant_id: The tenant whose corpus to re-index.
            folded: How many requests this run stands for.
            reasons: Those requests' reasons, in arrival order.

        Returns:
            A JSON-serialisable mapping recorded on ``job_runs.result`` — how many chunks
            were re-embedded, how long the index build took, whatever the implementation
            can honestly report.
        """
        ...


_HANDLER: ReindexHandler | None = None


def register_reindex_handler(handler: ReindexHandler) -> None:
    """Register the work a re-index performs.

    Args:
        handler: The coroutine function implementing it.
    """
    global _HANDLER
    _HANDLER = handler


def reindex_handler() -> ReindexHandler:
    """Return the registered re-index handler.

    Returns:
        The handler installed by :func:`register_reindex_handler`.

    Raises:
        UnregisteredReindexError: If nothing is registered.
    """
    if _HANDLER is None:
        raise UnregisteredReindexError(
            "no re-index handler is registered, so this run has nothing to perform. "
            "Register one with app.jobs.reindex.register_reindex_handler; recording a "
            "successful run that did no work would misreport the tenant's index as fresh."
        )
    return _HANDLER


def clear_reindex_handler() -> None:
    """Drop the registered handler.

    For tests, which register a real handler per case and must not leak it into the next
    one, and for a host that rebuilds its wiring.
    """
    global _HANDLER
    _HANDLER = None


@activity.defn(name=RUN_REINDEX)
@tenant_activity
async def run_reindex(inp: ReindexInput, *, session: AsyncSession) -> ReindexResult:
    """Re-index one tenant's corpus and record the run, in one transaction.

    The ``job_runs`` write is an **upsert on ``workflow_id``**, and here that is not
    belt-and-braces: the debounced workflow id is *reused for every window*, so the second
    burst's run necessarily meets the first burst's row. A bare insert would violate the
    unique constraint and fail every re-index after the first; ``DO NOTHING`` would leave
    the row describing a run that finished hours ago. The upsert is the only shape that
    keeps ``job_runs`` a truthful record of the latest run under that id.

    Args:
        inp: The tenant, the workflow id, and the folded requests this run stands for.
        session: The scoped session supplied by ``@tenant_activity``.

    Returns:
        The tenant, the fold count, and the ``job_runs`` row recording it.

    Raises:
        UnregisteredReindexError: If no handler is registered to do the work.
    """
    handler = reindex_handler()
    result = await handler(
        session, tenant_id=inp.tenant_id, folded=inp.folded, reasons=inp.reasons
    )
    now = _now()
    record = pg_insert(JobRun).values(
        tenant_id=inp.tenant_id,
        job_type=REINDEX_JOB_TYPE,
        workflow_id=inp.workflow_id,
        run_id=_run_id(),
        status=JobStatus.SUCCEEDED,
        payload={"folded": inp.folded, "reasons": list(inp.reasons)},
        result=dict(result),
        started_at=now,
        finished_at=now,
    )
    await session.execute(
        record.on_conflict_do_update(
            index_elements=["workflow_id"],
            set_={
                "run_id": record.excluded.run_id,
                "status": record.excluded.status,
                "payload": record.excluded.payload,
                "result": record.excluded.result,
                "error": None,
                "started_at": record.excluded.started_at,
                "finished_at": record.excluded.finished_at,
            },
        )
    )
    job_run_id = (
        await session.execute(
            select(JobRun.id).where(JobRun.workflow_id == inp.workflow_id)
        )
    ).scalar_one()
    logger.info(
        "re-indexed tenant %s: %d request(s) folded into one run (job_runs.id=%s)",
        inp.tenant_id,
        inp.folded,
        job_run_id,
    )
    return ReindexResult(
        tenant_id=inp.tenant_id, folded=inp.folded, job_run_id=job_run_id
    )


async def request_reindex(
    client: Client,
    *,
    tenant_id: int | None,
    reason: str,
    debounce_seconds: int | None = None,
    max_wait_seconds: int | None = None,
) -> WorkflowHandle[Any, Any]:
    """Ask for a re-index, folding into any window already open for this tenant.

    Signal-with-start, against :func:`app.jobs.flows.reindex.reindex_workflow_id`. The
    orchestrator either starts the workflow and delivers this request as its first signal,
    or — if one is already open for this tenant — delivers the signal to it and starts
    nothing. **That choice is made server-side, atomically**, which is why there is no
    check-then-start race here to get wrong, and why ten concurrent callers cannot produce
    two executions no matter how they interleave.

    Args:
        client: A connected Temporal client.
        tenant_id: Whose corpus to re-index.
        reason: Why, recorded on the run so a tenant reading "re-indexed at 14:02" can
            find out what caused it.
        debounce_seconds: Override the configured window. Only the *first* request of a
            window decides it, since the later ones start no workflow.
        max_wait_seconds: Override the configured ceiling on folding, likewise.

    Returns:
        The handle to the folding execution — the same handle for every request in one
        window, which is itself the evidence that they folded.
    """
    settings = get_settings()
    return await client.start_workflow(
        REINDEX_WORKFLOW,
        ReindexParams(
            tenant_id=tenant_id,
            debounce_seconds=(
                settings.temporal_reindex_debounce_seconds
                if debounce_seconds is None
                else debounce_seconds
            ),
            max_wait_seconds=(
                settings.temporal_reindex_max_wait_seconds
                if max_wait_seconds is None
                else max_wait_seconds
            ),
        ),
        id=reindex_workflow_id(tenant_id),
        task_queue=DEFAULT_QUEUE,
        result_type=ReindexResult,
        start_signal=REINDEX_SIGNAL,
        start_signal_args=[ReindexRequest(tenant_id=tenant_id, reason=reason)],
    )


@activity.defn(name=REQUEST_REINDEX)
@tenant_activity
async def request_tenant_reindex(
    inp: ReindexTickInput, *, session: AsyncSession
) -> str | None:
    """Turn one cadence tick into a re-index request, if the tenant has anything indexed.

    The visibility check is not a micro-optimisation. A schedule outlives the tenant it
    was created for — nothing deletes a schedule when a trial ends — so without it the
    platform would keep waking a worker every cadence for corpora that no longer exist,
    and every one of those runs would record a ``succeeded`` re-index of nothing. The
    count is read **through the tenant's own scope**, so "this tenant has no documents"
    and "this tenant is gone" are the same answer, which is the correct reading: neither
    has anything to re-index.

    Args:
        inp: The tenant, this tick's own workflow id, and the reason to record.
        session: The scoped session supplied by ``@tenant_activity``.

    Returns:
        The workflow id of the execution this request folded into, or ``None`` when the
        tenant has no ingested document to re-index.
    """
    indexed = await session.scalar(
        select(func.count())
        .select_from(Document.__table__)
        .where(Document.status == JobStatus.SUCCEEDED)
    )
    if not indexed:
        logger.info(
            "re-index cadence for tenant %s: no ingested document is visible, so nothing "
            "is requested rather than recording a re-index of an empty corpus",
            inp.tenant_id,
        )
        return None
    handle = await request_reindex(
        await get_temporal_client(), tenant_id=inp.tenant_id, reason=inp.reason
    )
    return handle.id


#: Every activity this module contributes to a worker's registration.
REINDEX_ACTIVITIES: tuple[Callable[..., Awaitable[Any]], ...] = (
    run_reindex,
    request_tenant_reindex,
)
