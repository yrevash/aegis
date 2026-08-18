"""The reconciler's workflow — the thing the schedule actually starts.

A schedule cannot invoke an activity; it starts a *workflow*. This one exists only to be
that entry point, so it is the smallest workflow in the codebase: one activity call, no
branching, no state. Everything that could be wrong lives in :mod:`app.jobs.reconcile`,
where it is a pure function and a query rather than replayable workflow code.

The retry policy is deliberately weak — three attempts and then let the run fail. A sweep
that cannot reach the orchestrator will not be able to reach it by trying harder, and a
failed scheduled run is *visible* in the schedule's history, whereas an activity retrying
forever looks exactly like one that is working.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from aegis.jobs.stages import DEFAULT_QUEUE

    from app.jobs.flows.contracts import (
        RECONCILE_STALE_RUNS,
        ReconcileParams,
        ReconcileReport,
    )

__all__ = ["RECONCILE_WORKFLOW", "ReconcileWorkflow"]

#: The registered workflow name, as the schedule's action and the worker both spell it.
RECONCILE_WORKFLOW = "AegisReconcile"

#: Ceiling on one sweep. Generous against ``limit`` round-trips to the orchestrator, and
#: bounded at all so a wedged connection fails the scheduled run rather than pinning a
#: worker slot until somebody notices.
_SWEEP_TIMEOUT = timedelta(minutes=10)


@workflow.defn(name=RECONCILE_WORKFLOW)
class ReconcileWorkflow:
    """Run one reconciliation sweep."""

    @workflow.run
    async def run(self, params: ReconcileParams) -> ReconcileReport:
        """Sweep the open job rows once.

        Args:
            params: The staleness threshold and batch limit for this sweep.

        Returns:
            What the sweep examined and did — returned rather than only logged, so the
            schedule's own history is a readable record of whether the platform has been
            stranding rows.
        """
        return await workflow.execute_activity(
            RECONCILE_STALE_RUNS,
            params,
            task_queue=DEFAULT_QUEUE,
            start_to_close_timeout=_SWEEP_TIMEOUT,
            retry_policy=RetryPolicy(maximum_attempts=3),
            result_type=ReconcileReport,
        )
