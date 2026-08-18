"""The debounced re-index workflow — one run per burst, not one run per request.

**Debounce is not idempotency, and conflating them is the mistake this file exists to
avoid.** Idempotency says *"this exact work is already queued; return the existing
handle"*. Ten documents uploaded in a minute are not the same work — each upload is
legitimately different — so no idempotency key can express what actually needs to happen,
which is: *"work of this kind is already pending for this tenant; fold this request into
it and push the run time out."*

The mechanism is two things together, and neither alone is enough:

1. **A per-tenant workflow id**, ``reindex:{tenant_id}``. The orchestrator permits one
   open execution per id, so the second request cannot start a second run — it is
   delivered as a signal to the first, via signal-with-start
   (:func:`app.jobs.reindex.request_reindex`). This is what makes the fold *structural*:
   there is no window in which two runs exist and one has to notice the other.
2. **A timer this workflow resets**, below. Without it the per-tenant id would merely
   *serialise* the requests — the second would queue behind the first and still run. The
   reset is what makes the second request join the first instead.

Per **tenant** and not globally, deliberately: one tenant's upload burst must never delay
another tenant's re-index, and a global debounce would make the busiest tenant on the
platform the scheduler for everyone else.

The ceiling, which is not optional
----------------------------------

``max_wait_seconds`` bounds how far the deadline can be pushed out. A tenant uploading one
document every twenty seconds would otherwise reset a thirty-second window forever and be
re-indexed *never* — debounce without a ceiling is a starvation bug, and the tenant it
starves is by definition the most active one.

After the run
-------------

Requests that arrive while the re-index activity is in flight are not dropped: the
workflow loops and opens a new window for them. That keeps this execution alive across
successive bursts, and the cost is honest to state — history grows by roughly a dozen
events per cycle, and each cycle costs a whole debounce window, so a continuously-loaded
tenant reaches the orchestrator's history limit after tens of thousands of cycles rather
than in any plausible day's work. A workflow terminated for that reason still leaves a
``job_runs`` row, and :mod:`app.jobs.reconcile` sweeps it.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from aegis.jobs.stages import DEFAULT_QUEUE

    from app.jobs.flows.contracts import (
        REQUEST_REINDEX,
        RUN_REINDEX,
        ReindexCadenceParams,
        ReindexInput,
        ReindexParams,
        ReindexRequest,
        ReindexResult,
        ReindexTickInput,
    )

__all__ = [
    "REINDEX_CADENCE_WORKFLOW",
    "REINDEX_SIGNAL",
    "REINDEX_WORKFLOW",
    "ReindexCadenceWorkflow",
    "ReindexWorkflow",
    "reindex_workflow_id",
]

#: The registered workflow name.
REINDEX_WORKFLOW = "AegisReindex"

#: The registered name of the cadence tick — see :class:`ReindexCadenceWorkflow` for why
#: the schedule does not point at :data:`REINDEX_WORKFLOW` directly.
REINDEX_CADENCE_WORKFLOW = "AegisReindexCadence"

#: The signal name a folded request arrives on. Part of the wire contract exactly as an
#: activity name is: the sender spells it, the workflow registers it, and a mismatch is
#: not an import error but a signal delivered to an execution that ignores it.
REINDEX_SIGNAL = "reindex_requested"

#: Ceiling on the re-index activity itself.
_REINDEX_TIMEOUT = timedelta(hours=1)

#: Ceiling on one cadence tick: a visibility read and one signal-with-start.
_TICK_TIMEOUT = timedelta(minutes=2)


def reindex_workflow_id(tenant_id: int | None) -> str:
    """Return the one workflow id a tenant's re-indexing runs under.

    The single most load-bearing line in the debounce: the orchestrator allows one open
    execution per workflow id, so this function *is* the "fold, do not queue" guarantee.
    Deriving it rather than formatting it at each call site means a caller cannot
    accidentally spell a per-request id and turn the debounce off without any error.

    Args:
        tenant_id: The tenant, or ``None`` for the platform's own corpus.

    Returns:
        The workflow id, e.g. ``"reindex:7"``.
    """
    return f"reindex:{'platform' if tenant_id is None else tenant_id}"


@workflow.defn(name=REINDEX_WORKFLOW)
class ReindexWorkflow:
    """Fold a burst of re-index requests for one tenant into a single run."""

    def __init__(self) -> None:
        """Start with an empty window.

        The signal handler writes only these two fields, and the run loop derives the
        deadline from them. That split is deliberate: a handler that computed the deadline
        itself would depend on the run method having already initialised it, and a signal
        delivered in the very first workflow task — which is exactly what
        signal-with-start does — is the case where that assumption is least safe.
        """
        self._reasons: list[str] = []
        self._last_request_at: datetime | None = None

    @workflow.signal(name=REINDEX_SIGNAL)
    def requested(self, request: ReindexRequest) -> None:
        """Fold one more request into the pending window.

        Args:
            request: The tenant and the reason it was asked for. The reason is kept
                because ten folded requests carrying ten different reasons is the evidence
                that the fold happened; a bare counter is not.
        """
        self._reasons.append(request.reason)
        self._last_request_at = workflow.now()

    @workflow.run
    async def run(self, params: ReindexParams) -> ReindexResult:
        """Wait out the debounce window, re-index once, and repeat if more arrived.

        Args:
            params: The tenant, the debounce window and the ceiling on folding.

        Returns:
            The last run's outcome, including how many requests it stood for.
        """
        window = timedelta(seconds=params.debounce_seconds)
        handled = 0
        while True:
            await self._settle(window, params.max_wait_seconds)
            folded = self._reasons[handled:]
            handled = len(self._reasons)
            result = await workflow.execute_activity(
                RUN_REINDEX,
                ReindexInput(
                    tenant_id=params.tenant_id,
                    workflow_id=workflow.info().workflow_id,
                    folded=len(folded),
                    reasons=tuple(folded),
                ),
                task_queue=DEFAULT_QUEUE,
                start_to_close_timeout=_REINDEX_TIMEOUT,
                retry_policy=RetryPolicy(maximum_attempts=3),
                result_type=ReindexResult,
            )
            if len(self._reasons) == handled:
                return result

    async def _settle(self, window: timedelta, max_wait_seconds: int) -> None:
        """Block until no request has arrived for a whole window, or the ceiling is hit.

        Each pass sleeps to the current deadline. A request that lands during the sleep
        wakes the wait, and the next pass recomputes the deadline from *its* arrival time
        — that recomputation is the timer reset, and it is why a burst produces one run.

        Args:
            window: How long quiet must last before the run happens.
            max_wait_seconds: How far the deadline may be pushed out in total, measured
                from the moment this window opened. Without it an active tenant resets the
                timer forever and is never re-indexed at all.
        """
        opened = workflow.now()
        ceiling = opened + timedelta(seconds=max_wait_seconds)
        while True:
            seen = len(self._reasons)
            base = self._last_request_at if self._last_request_at is not None else opened
            remaining = min(base + window, ceiling) - workflow.now()
            if remaining <= timedelta(0):
                return
            try:
                await workflow.wait_condition(
                    lambda seen=seen: len(self._reasons) > seen, timeout=remaining
                )
            except TimeoutError:
                # The window elapsed with nothing new: the burst is over.
                return


@workflow.defn(name=REINDEX_CADENCE_WORKFLOW)
class ReindexCadenceWorkflow:
    """The cadence tick — a scheduled re-index request, not a scheduled re-index.

    This exists because of one detail of how schedules work: the orchestrator appends the
    nominal fire time to the workflow id of every scheduled run, so a schedule pointed
    straight at :class:`ReindexWorkflow` would start executions under
    ``reindex:7-2026-08-18T…`` and each one would be its **own** debounce window. The
    per-tenant fold would then be silently off for exactly the runs nobody watches.

    So the tick does not start a re-index; it *asks* for one, through the same
    signal-with-start door an upload uses. A cadence tick that lands mid-burst therefore
    folds into the burst instead of racing it, and there is one door for every re-index
    request on the platform rather than two that must be kept in step.
    """

    @workflow.run
    async def run(self, params: ReindexCadenceParams) -> str | None:
        """Ask for a re-index of one tenant's corpus.

        Args:
            params: Which tenant's schedule fired.

        Returns:
            The workflow id of the folding execution, or ``None`` when the tenant has
            nothing indexed to refresh — returned rather than only logged, so the
            schedule's own history says which ticks did anything.
        """
        return await workflow.execute_activity(
            REQUEST_REINDEX,
            ReindexTickInput(
                tenant_id=params.tenant_id,
                workflow_id=workflow.info().workflow_id,
                reason="scheduled re-index cadence",
            ),
            task_queue=DEFAULT_QUEUE,
            start_to_close_timeout=_TICK_TIMEOUT,
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
