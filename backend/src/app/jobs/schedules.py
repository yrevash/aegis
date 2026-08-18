"""Temporal Schedules — the platform's recurring work, with no clock of our own.

Two things in this phase recur: the reconciliation sweep (§3.3) and each tenant's
re-index cadence (§3.5). Neither is a table.

That is the whole design decision, and it is worth stating plainly because the obvious
alternative is what this repository already does elsewhere: a ``next_run_at`` column, a
sweeper polling it, a "did I already run this tick?" guard, and a clock that stops when the
process does. Every one of those is a thing to get wrong, and the memory-consolidation job
demonstrates the failure mode — a row claimed by a worker that died is claimed forever,
because the only thing that would have noticed is the sweeper that is not running. A
Schedule has no such row. It is the orchestrator's own durable timer: it survives restarts,
it records every fire in a history an operator can read, and a missed tick is visible
rather than inferred.

Ensuring rather than creating
-----------------------------

:func:`ensure_schedule` creates the schedule or updates the existing one to match. Every
worker bootstrap calls it, so the schedules are **declared by the code that needs them**
rather than by a runbook step somebody performs once on a machine that is later replaced.
The update path matters as much as the create path: a schedule created by last month's
build with last month's interval would otherwise persist forever, silently overriding the
setting an operator has since changed.

Failures are not swallowed. A schedule that cannot be created means reconciliation and
re-indexing will never run, and a substrate that quietly stops maintaining itself is
exactly the silence this phase exists to end.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from aegis.jobs.stages import DEFAULT_QUEUE
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleUpdate,
    ScheduleUpdateInput,
)

from app.config import get_settings
from app.jobs.flows.contracts import ReconcileParams, ReindexCadenceParams
from app.jobs.flows.reconcile import RECONCILE_WORKFLOW
from app.jobs.flows.reindex import REINDEX_CADENCE_WORKFLOW

logger = logging.getLogger(__name__)

__all__ = [
    "RECONCILE_SCHEDULE_ID",
    "ensure_platform_schedules",
    "ensure_reindex_schedule",
    "ensure_schedule",
    "ensure_tenant_reindex_schedules",
    "reindex_schedule_id",
]

#: The reconciler's schedule id. One per namespace, not one per tenant: the sweep is
#: platform work that reads every tenant's open rows, and a schedule per tenant would fan
#: one query out into N.
RECONCILE_SCHEDULE_ID = "aegis-reconcile"


def reindex_schedule_id(tenant_id: int | None) -> str:
    """Return the schedule id for one tenant's re-index cadence.

    Args:
        tenant_id: The tenant, or ``None`` for the platform's own corpus.

    Returns:
        The schedule id, e.g. ``"aegis-reindex:7"``.
    """
    return f"aegis-reindex:{'platform' if tenant_id is None else tenant_id}"


async def ensure_schedule(client: Client, schedule_id: str, schedule: Schedule) -> str:
    """Create the schedule, or bring an existing one into line with it.

    Args:
        client: A connected Temporal client.
        schedule_id: The schedule's id.
        schedule: The desired schedule.

    Returns:
        ``"created"`` or ``"updated"``, so a caller can log which happened rather than
        guessing.

    Raises:
        RPCError: Propagated from the SDK. Deliberately not caught — see the module
            docstring.
    """
    try:
        await client.create_schedule(schedule_id, schedule)
    except ScheduleAlreadyRunningError:
        handle = client.get_schedule_handle(schedule_id)

        def _replace(_current: ScheduleUpdateInput) -> ScheduleUpdate:
            """Replace the whole schedule with the desired one.

            The current schedule is ignored on purpose: this function is the declaration
            of what the schedule *should* be, and merging in whatever a previous build
            left behind is how a stale interval outlives the build that set it.
            """
            return ScheduleUpdate(schedule=schedule)

        await handle.update(_replace)
        return "updated"
    return "created"


def _reconcile_schedule() -> Schedule:
    """Build the reconciliation sweep's schedule from settings.

    Returns:
        The schedule: one :class:`app.jobs.flows.reconcile.ReconcileWorkflow` per
        interval, with overlapping runs skipped.
    """
    settings = get_settings()
    return Schedule(
        action=ScheduleActionStartWorkflow(
            RECONCILE_WORKFLOW,
            ReconcileParams(
                tenant_id=None,
                workflow_id=RECONCILE_SCHEDULE_ID,
                stale_after_seconds=settings.temporal_reconcile_stale_after_seconds,
                limit=settings.temporal_reconcile_batch,
            ),
            id=RECONCILE_SCHEDULE_ID,
            task_queue=DEFAULT_QUEUE,
        ),
        spec=ScheduleSpec(
            intervals=[
                ScheduleIntervalSpec(
                    every=timedelta(
                        seconds=settings.temporal_reconcile_interval_seconds
                    )
                )
            ]
        ),
        # SKIP, not BUFFER_ONE: a sweep that is still running has not finished examining
        # the rows the next one would examine, and queueing a second pass behind it would
        # make a slow orchestrator look like a stuck platform by piling up work whose only
        # purpose is to notice stuck work.
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    )


def _reindex_schedule(tenant_id: int | None, interval_seconds: int) -> Schedule:
    """Build one tenant's re-index cadence.

    Args:
        tenant_id: The tenant whose corpus this refreshes.
        interval_seconds: How often to fire.

    Returns:
        The schedule: one :class:`app.jobs.flows.reindex.ReindexCadenceWorkflow` per
        interval. It points at the *cadence tick* rather than at the re-index itself —
        see that class for why a schedule cannot start the debounced workflow directly.
    """
    return Schedule(
        action=ScheduleActionStartWorkflow(
            REINDEX_CADENCE_WORKFLOW,
            ReindexCadenceParams(tenant_id=tenant_id),
            id=reindex_schedule_id(tenant_id),
            task_queue=DEFAULT_QUEUE,
        ),
        spec=ScheduleSpec(
            intervals=[ScheduleIntervalSpec(every=timedelta(seconds=interval_seconds))]
        ),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    )


async def ensure_tenant_reindex_schedules(client: Client) -> dict[str, str]:
    """Declare a re-index cadence for every tenant that currently exists.

    Called from the worker bootstrap, which is the only place that reliably runs again
    after a tenant is created: a schedule declared only at tenant-creation time would be
    missing for every tenant that predates the feature, and nothing would ever notice.
    Re-declaring an existing one is an update, so running this on every boot converges
    rather than accumulating.

    Args:
        client: A connected Temporal client.

    Returns:
        ``{schedule_id: "created" | "updated"}``, empty when there are no tenants yet.

    Raises:
        SQLAlchemyError: If the tenant list cannot be read. Not caught: a worker that
            silently skipped this would leave the platform with no re-index cadence at
            all, and the absence of a cadence is invisible by construction.
    """
    from aegis.governance.models import Tenant  # noqa: PLC0415 - local: keeps the ORM off
    from sqlalchemy import select  # noqa: PLC0415 - this module's import path

    from app.data.session import get_sessionmaker  # noqa: PLC0415 - local

    async with get_sessionmaker()() as session:
        tenant_ids = list((await session.execute(select(Tenant.id))).scalars())
    return {
        reindex_schedule_id(tenant_id): await ensure_reindex_schedule(
            client, tenant_id=tenant_id
        )
        for tenant_id in tenant_ids
    }


async def ensure_platform_schedules(client: Client) -> dict[str, str]:
    """Declare every schedule that is not per-tenant.

    Called from the worker bootstrap, so the reconciler exists wherever a worker does. A
    deployment that forgot to create it by hand would otherwise have a substrate with no
    backstop — and the absence would be invisible, because a sweeper that never runs
    reports nothing.

    Args:
        client: A connected Temporal client.

    Returns:
        ``{schedule_id: "created" | "updated"}``.
    """
    outcome = {
        RECONCILE_SCHEDULE_ID: await ensure_schedule(
            client, RECONCILE_SCHEDULE_ID, _reconcile_schedule()
        )
    }
    settings = get_settings()
    logger.info(
        "Temporal schedules ensured: %s (reconciler every %ds, examining runs open "
        "longer than %ds)",
        outcome,
        settings.temporal_reconcile_interval_seconds,
        settings.temporal_reconcile_stale_after_seconds,
    )
    return outcome


async def ensure_reindex_schedule(
    client: Client, *, tenant_id: int | None, interval_seconds: int | None = None
) -> str:
    """Declare one tenant's re-index cadence.

    Per tenant rather than one schedule for all of them, because the cadence is a
    per-tenant setting: a tenant whose corpus changes hourly and one whose corpus is a
    fixed archive should not share a clock, and §3.7's settings catalogue is where that
    number will come from.

    Args:
        client: A connected Temporal client.
        tenant_id: The tenant to schedule.
        interval_seconds: How often. Defaults to
            ``TEMPORAL_REINDEX_INTERVAL_SECONDS``.

    Returns:
        ``"created"`` or ``"updated"``.
    """
    every = (
        get_settings().temporal_reindex_interval_seconds
        if interval_seconds is None
        else interval_seconds
    )
    schedule_id = reindex_schedule_id(tenant_id)
    outcome = await ensure_schedule(
        client, schedule_id, _reindex_schedule(tenant_id, every)
    )
    logger.info(
        "re-index cadence %s for tenant %s: every %ds", outcome, tenant_id, every
    )
    return outcome
