"""Worker bootstrap — one implementation, two launch modes.

On demo day the worker is an ``asyncio`` task inside the API's own lifespan: one process,
nothing extra to start, nothing extra to forget. In a scaled deployment it is
``python -m app.jobs.worker``, one process per task queue. **Both go through
:func:`run_workers`**, which is the point — a second implementation for the standalone
mode is a second thing to keep in step, and the one that runs in production is invariably
the one nobody tested.

One worker per task queue, because concurrency is a property of the queue
------------------------------------------------------------------------

The SDK's ``max_concurrent_activities`` is a *worker* setting, so a single worker polling
three queues could not give ``aegis-cpu`` one slot while giving ``aegis-io`` thirty-two.
Running one worker per queue is what makes :data:`aegis.jobs.TASK_QUEUES` enforceable
rather than documentary — and it is why a Docling parse (2.2 GB resident at peak) can
never find a second parse already running on the same box.

Only the default queue's worker registers the **workflows**. A workflow task is cheap and
frequent; letting one occupy the single slot on the CPU queue would let a workflow wait
for a slot held by its own activity.

What this module deliberately does not do
-----------------------------------------

It does not catch connection errors. A worker that cannot reach the orchestrator must
fail visibly: the lifespan supervises the task and logs its death at ERROR, and the API
keeps serving with the substrate honestly *down* rather than present-looking and inert.
"""

from __future__ import annotations

import asyncio
import logging

from aegis.jobs.stages import CPU_QUEUE, TASK_QUEUES, QueueSpec, UnknownStageError, queue_spec
from temporalio.client import Client
from temporalio.worker import Worker

from app.config import get_settings
from app.ingestion import warm_parser
from app.jobs.activities import ALL_ACTIVITIES
from app.jobs.client import get_temporal_client
from app.jobs.flows import (
    IngestWorkflow,
    ReconcileWorkflow,
    ReindexCadenceWorkflow,
    ReindexWorkflow,
)
from app.jobs.reconcile import RECONCILE_ACTIVITIES
from app.jobs.reindex import REINDEX_ACTIVITIES
from app.jobs.schedules import (
    ensure_platform_schedules,
    ensure_tenant_reindex_schedules,
)

logger = logging.getLogger(__name__)

__all__ = [
    "build_workers",
    "configured_queues",
    "run_workers",
    "start_worker_task",
]

#: Every workflow this platform runs. Registered only on the queue whose
#: :attr:`aegis.jobs.QueueSpec.runs_workflows` is true.
_WORKFLOWS = (
    IngestWorkflow,
    ReconcileWorkflow,
    ReindexWorkflow,
    ReindexCadenceWorkflow,
)

#: Every activity a worker registers, from all three implementation modules.
#:
#: Composed here rather than in one of them because the alternative is an import cycle:
#: :mod:`app.jobs.reconcile` and :mod:`app.jobs.reindex` both build on
#: :mod:`app.jobs.activities`, so that module cannot in turn import them. The composition
#: is a tuple splat and not a loop precisely so that a module whose activities were left
#: out is visible here as a missing name rather than as work nothing ever picks up.
_ACTIVITIES = (*ALL_ACTIVITIES, *RECONCILE_ACTIVITIES, *REINDEX_ACTIVITIES)


def configured_queues() -> tuple[QueueSpec, ...]:
    """Return the queues this process should poll, validated against the declarations.

    ``TEMPORAL_TASK_QUEUES`` is a comma-separated list; empty means "every declared
    queue", which is the single-box posture. The validation is the reason this is a
    function rather than a ``split(",")`` at the call site: a typo'd queue name would
    otherwise start a worker polling a queue nothing ever schedules onto, while the real
    queue went unserved — and **nothing would raise**, because both halves of that
    mistake are silent. Work would simply stop happening.

    Returns:
        The :class:`aegis.jobs.QueueSpec` for each configured queue, in declaration
        order.

    Raises:
        UnknownStageError: If a configured name is not one :data:`aegis.jobs.TASK_QUEUES`
            declares. The message lists the declared names.
    """
    configured = get_settings().temporal_task_queues.strip()
    if not configured:
        return TASK_QUEUES
    wanted = [name.strip() for name in configured.split(",") if name.strip()]
    if not wanted:
        return TASK_QUEUES
    specs = [queue_spec(name) for name in wanted]
    return tuple(specs)


def build_workers(client: Client, queues: tuple[QueueSpec, ...] | None = None) -> list[Worker]:
    """Build one worker per queue, each carrying that queue's concurrency policy.

    Args:
        client: A connected Temporal client.
        queues: The queues to serve. Defaults to :func:`configured_queues`.

    Returns:
        One unstarted :class:`temporalio.worker.Worker` per queue.
    """
    workers: list[Worker] = []
    for spec in queues if queues is not None else configured_queues():
        workers.append(
            Worker(
                client,
                task_queue=spec.name,
                activities=list(_ACTIVITIES),
                workflows=list(_WORKFLOWS) if spec.runs_workflows else [],
                max_concurrent_activities=spec.max_concurrent_activities,
            )
        )
        logger.info(
            "Temporal worker built for %s: %d activity slot(s), workflows=%s (%s)",
            spec.name,
            spec.max_concurrent_activities,
            spec.runs_workflows,
            spec.rationale,
        )
    return workers


def _wire_session_factory() -> None:
    """Give :mod:`aegis.jobs.scope` the **serving** session factory.

    This is the single line that decides whether every activity in this process is
    subject to Row-Level Security. :func:`app.data.session.get_sessionmaker` is built on
    the serving engine — the ``NOSUPERUSER NOBYPASSRLS`` role — so binding it here is what
    makes ``@tenant_activity``'s scope an enforced filter rather than a decorative one.
    Handing it :func:`~app.data.session.get_admin_engine` instead would silently disable
    tenant isolation for all background work while every test still passed, which is
    precisely the failure the owner/serving split exists to prevent.
    """
    from aegis.jobs.scope import set_activity_session_factory  # noqa: PLC0415 - local

    from app.data.session import get_sessionmaker  # noqa: PLC0415 - local

    set_activity_session_factory(get_sessionmaker())


def _report_unhandled_stages() -> None:
    """Say at startup which stages this worker could not actually perform.

    A stage with no registered handler fails its activity non-retryably the first time a
    document reaches it — correct, because the substrate must never advance
    ``completed_stage`` past work it did not do, but discovered far too late. Naming the
    gap at boot turns "ingestion mysteriously fails at parse" into a line in the log
    before the first document is uploaded.

    A WARNING and not a refusal to start: a worker deliberately dedicated to one queue
    legitimately has no handler for stages that run elsewhere, and a fatal check here
    would make that ordinary deployment impossible.
    """
    from aegis.jobs.stages import INGEST_STAGES, stage_handler  # noqa: PLC0415 - local

    missing = []
    for spec in INGEST_STAGES:
        try:
            stage_handler(spec.name)
        except LookupError:
            missing.append(spec.name)
    if missing:
        logger.warning(
            "No stage handler registered for: %s. A document reaching one of those "
            "stages will fail its activity rather than silently record progress it did "
            "not make — register handlers with aegis.jobs.register_stage_handler.",
            ", ".join(missing),
        )


def _report_unregistered_reindex() -> None:
    """Say at startup if nothing can perform a re-index.

    The same reasoning as :func:`_report_unhandled_stages`, for the other registry: a
    re-index with no handler fails its activity — correctly, because recording a
    ``succeeded`` run for work that never happened would misreport the tenant's index as
    fresh — but the first time anyone learns that is a cadence tick hours later. A line at
    boot turns it into something an operator sees before the schedule does.
    """
    from app.jobs.reindex import reindex_handler  # noqa: PLC0415 - local

    try:
        reindex_handler()
    except LookupError as exc:
        logger.warning("%s", exc)


async def run_workers(stop: asyncio.Event | None = None) -> None:
    """Run every configured worker until ``stop`` is set or the task is cancelled.

    The one code path both launch modes use. Workers run concurrently and are shut down
    together: :meth:`temporalio.worker.Worker.shutdown` lets an in-flight activity finish
    rather than tearing its transaction out from under it, which matters because that
    transaction is a stage's whole output.

    Args:
        stop: Set this event to shut the workers down. ``None`` (the standalone mode)
            means "run until cancelled" — the process is stopped by a signal.

    Raises:
        RuntimeError: Propagated from the SDK if the orchestrator is unreachable. Not
            caught here; see the module docstring.
    """
    _wire_session_factory()
    _report_unhandled_stages()
    _report_unregistered_reindex()
    queues = configured_queues()
    if not queues:
        raise RuntimeError(
            "no task queues configured, so this worker would poll nothing and every "
            "job submitted to it would sit unclaimed forever; check TEMPORAL_TASK_QUEUES"
        )
    client = await get_temporal_client()
    if any(spec.runs_workflows for spec in queues):
        # Only the process that can actually execute them declares them. A worker pinned
        # to the CPU queue creating the reconciler's schedule would be declaring recurring
        # work it cannot run, and the schedule would fire into a queue this process does
        # not serve.
        await ensure_platform_schedules(client)
        await ensure_tenant_reindex_schedules(client)
    workers = build_workers(client, queues)
    async with asyncio.TaskGroup() as group:
        if any(spec.name == CPU_QUEUE for spec in queues):
            # D4: the models load in a thread while the workers start, not on the first
            # upload of the day. Only the process serving the CPU queue runs the parse
            # stage, so only it pays; ``warm_parser`` is best-effort and never raises,
            # which matters inside a TaskGroup that would otherwise cancel the workers.
            group.create_task(warm_parser())
        for worker in workers:
            group.create_task(worker.run())
        if stop is not None:
            await stop.wait()
        else:
            await asyncio.Event().wait()
        for worker in workers:
            group.create_task(worker.shutdown())


def start_worker_task(stop: asyncio.Event) -> asyncio.Task[None]:
    """Launch the workers as a background task — the in-process launch mode.

    Args:
        stop: The lifespan's shutdown event, shared with the other background tasks.

    Returns:
        The task running :func:`run_workers`. The caller supervises it; a worker that
        dies must be an ERROR in the log and not a substrate that silently stopped.
    """
    return asyncio.create_task(run_workers(stop), name="temporal-worker")


def main() -> None:
    """Entry point for ``python -m app.jobs.worker`` — the standalone launch mode.

    Identical to what the lifespan runs, minus the shutdown event: the process is ended
    by a signal, and ``asyncio.run`` cancels :func:`run_workers` on the way out.
    """
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    logger.info(
        "Starting Aegis job workers against %s (namespace %s), queues: %s",
        settings.temporal_address,
        settings.temporal_namespace,
        ", ".join(spec.name for spec in configured_queues()),
    )
    try:
        asyncio.run(run_workers())
    except KeyboardInterrupt:
        logger.info("Worker stopped by signal.")
    except UnknownStageError:
        logger.critical(
            "TEMPORAL_TASK_QUEUES names a queue this platform does not declare; the "
            "worker would poll a queue nothing schedules onto while the real one went "
            "unserved.",
            exc_info=True,
        )
        raise


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess, not imported
    main()
