"""The ingestion workflow — the only place that decides what runs and in what order.

It contains no database access, no model call and no ``asyncio`` primitive of its own.
That is not minimalism; it is the requirement. A workflow's body is **re-executed from
history** on every replay, so it must be deterministic, and the way to be deterministic is
to own the decisions and delegate every effect to an activity.

What this buys, concretely, and what §3.0 measured on a hard-killed worker: ``parse``,
``chunk`` and ``enrich`` did **not** re-run; only the in-flight activity replayed. The
loop below is the code that property belongs to — each stage is one activity call, so the
orchestrator's history records each completed stage and a resumed run skips straight past
them.

Two different resume mechanisms meet here, and conflating them causes real bugs:

* **Within one execution**, the orchestrator's replay skips activities whose completion is
  already in history. Nothing in this file participates; it happens above it.
* **Across executions** — a brand-new workflow started for a document that was already
  partly ingested, which is what the reconciler does — the resume point comes from *our*
  row. :func:`~app.jobs.activities.start_ingest` reads ``completed_stage`` and
  :func:`aegis.jobs.stages.remaining_stages` turns it into the stages left to run.

Every activity is invoked **by name** with an explicit ``task_queue``, because the stage
decides which queue it runs on and the queue carries the concurrency policy — a parse
routed to the default queue would run alongside another parse and take the box down.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

# The sandbox re-imports this module on every workflow task. These are pure, stdlib-only
# declarations (see :mod:`aegis.jobs.stages`), but passing them through shares the
# already-imported modules rather than re-executing their import graph — which for the
# contracts means not re-importing SQLAlchemy inside the sandbox on every task.
with workflow.unsafe.imports_passed_through():
    from aegis.jobs.stages import (
        DEFAULT_QUEUE,
        HEARTBEAT_TIMEOUT_FACTOR,
        INGEST_STAGES,
        remaining_stages,
    )

    from app.jobs.flows.contracts import (
        FINISH_INGEST,
        RUN_STAGE,
        START_INGEST,
        FinishInput,
        IngestParams,
        IngestResult,
        StageInput,
        StageOutcome,
        StartOutcome,
    )

__all__ = ["INGEST_WORKFLOW", "IngestWorkflow"]

#: The registered workflow name. A constant because the client starts the workflow by
#: this string and the worker registers it by this string, and a mismatch between them
#: surfaces only as a workflow task nothing ever polls for.
INGEST_WORKFLOW = "AegisIngest"

#: The job type recorded on the ``job_runs`` row. Distinct from the workflow name so the
#: console can group runs by what they *are* without depending on an orchestrator's
#: naming.
_JOB_TYPE = "ingest"

#: Timeout for the two bookkeeping activities. They are single-statement writes against
#: the record layer, so a minute is generous; the point of bounding them at all is that
#: an unbounded ``start_to_close`` on a wedged connection hangs the whole run with no
#: signal anywhere.
_BOOKKEEPING_TIMEOUT = timedelta(seconds=60)

#: Retry policy for those same two activities. Three attempts: a transient database blip
#: is worth retrying, while a genuine failure to claim a run should surface fast rather
#: than after a long backoff on work that has not started.
_BOOKKEEPING_RETRY = RetryPolicy(maximum_attempts=3)

#: How deep to walk an exception's ``__cause__`` chain looking for the real message. A
#: bound, not a guess: the chain here is at most ``ActivityError → ApplicationError →
#: (rarely) its own cause``, and an unbounded walk would hang on a self-referential chain
#: rather than fail.
_CAUSE_DEPTH = 5


def _root_reason(exc: BaseException) -> str:
    """Return the deepest non-empty message in ``exc``'s cause chain.

    **Why this exists.** ``str(exc)`` on the exception a failed activity raises into a
    workflow is Temporal's own wrapper text: ``"Activity task failed"``. Every word true,
    and it is the *only* thing the tenant was told — the actual cause
    (``litellm.APIError: RBAC: access denied``) lived one link down the ``__cause__``
    chain and reached no row, no log line and no screen. A caller who cannot see why an
    ingest failed cannot fix it, and the platform's own table-summary failures already do
    this properly (``"the summary call failed: …"``); this is the stage-level equivalent.

    Deterministic and stdlib-only, so it is safe inside a workflow body.

    Args:
        exc: The exception the stage loop raised.

    Returns:
        The most specific message available, falling back to the outermost one when the
        chain carries nothing better.
    """
    best = str(exc).strip() or type(exc).__name__
    cursor: BaseException | None = exc
    for _ in range(_CAUSE_DEPTH):
        cursor = getattr(cursor, "cause", None) or getattr(cursor, "__cause__", None)
        if cursor is None:
            break
        message = str(cursor).strip()
        if message:
            best = message
    return best


@workflow.defn(name=INGEST_WORKFLOW)
class IngestWorkflow:
    """Ingest one tenant's document, stage by stage, resumably.

    The tenant travels on every activity argument this workflow constructs, which is what
    lets :func:`aegis.jobs.scope.tenant_activity` bind a scope in a worker process that
    knows nothing about the request the upload came from.
    """

    @workflow.run
    async def run(self, params: IngestParams) -> IngestResult:
        """Claim the run, execute the remaining stages in order, then close it out.

        Args:
            params: The tenant, document and uploading user.

        Returns:
            The document id and the stages this execution actually ran — empty when the
            document was already fully ingested, which is a successful outcome and not an
            error.

        Raises:
            Exception: Whatever an activity failed with, re-raised after the run is
                recorded ``FAILED`` with that reason. The record write comes first
                deliberately: a run whose failure exists only in the orchestrator's
                history is a run the tenant cannot be told anything about, and it is
                indistinguishable on our side from one still in flight. Note this catches
                ``Exception`` and not ``BaseException``: a *cancelled* workflow arrives as
                ``CancelledError``, and scheduling another activity inside a cancelled
                scope would itself be cancelled — cancellation is task 3.4's, with its own
                shielded close-out path.
        """
        workflow_id = workflow.info().workflow_id
        start: StartOutcome = await workflow.execute_activity(
            START_INGEST,
            StageInput(
                tenant_id=params.tenant_id,
                workflow_id=workflow_id,
                document_id=params.document_id,
                stage=_JOB_TYPE,
            ),
            task_queue=DEFAULT_QUEUE,
            start_to_close_timeout=_BOOKKEEPING_TIMEOUT,
            retry_policy=_BOOKKEEPING_RETRY,
            result_type=StartOutcome,
        )

        stages_run: list[str] = []
        # Which stage is being attempted right now. The ``except`` below is outside the
        # loop, so without this the workflow knows *that* a stage failed and not *which*,
        # and the close-out could only name ``completed_stage`` — the last stage that
        # **succeeded**. That is how "ingest failed at enrich" came to be written about a
        # run in which enrich succeeded and embed died.
        attempting: str | None = None
        try:
            for spec in remaining_stages(start.completed_stage, INGEST_STAGES):
                attempting = spec.name
                outcome: StageOutcome = await workflow.execute_activity(
                    RUN_STAGE,
                    StageInput(
                        tenant_id=params.tenant_id,
                        workflow_id=workflow_id,
                        document_id=params.document_id,
                        stage=spec.name,
                    ),
                    # The stage names its queue and the queue carries the concurrency
                    # policy: this line is how a Docling parse ends up on a worker with
                    # one activity slot while an embed call runs on one with thirty-two.
                    task_queue=spec.task_queue,
                    start_to_close_timeout=timedelta(seconds=spec.timeout_seconds),
                    # Without this a hard-killed worker's stage sits "running" until
                    # ``start_to_close_timeout`` — half an hour for a parse — because a
                    # SIGKILL tells the orchestrator nothing. The heartbeat is what turns
                    # "the worker died" into a retry in seconds, and it is the mechanism
                    # behind the phase's demo of killing a worker mid-ingest.
                    heartbeat_timeout=timedelta(
                        seconds=spec.heartbeat_seconds * HEARTBEAT_TIMEOUT_FACTOR
                    ),
                    retry_policy=RetryPolicy(maximum_attempts=spec.max_attempts),
                    result_type=StageOutcome,
                )
                stages_run.append(outcome.stage)
        except Exception as exc:
            # The two things the tenant actually needs, in the one string that reaches
            # ``documents.error``, the ``job_runs`` row and the ingest log: *which* stage
            # died, and *why*. Shaped like the per-table failures the same response body
            # already renders well ("the summary call failed: …"), because that shape was
            # the one part of this surface a reader could act on.
            stage = attempting or start.completed_stage or "an unnamed stage"
            await self._finish(
                params,
                workflow_id,
                "failed",
                f"the {stage} stage failed: {_root_reason(exc)}",
            )
            raise

        await self._finish(params, workflow_id, "succeeded", None)
        return IngestResult(document_id=params.document_id, stages_run=tuple(stages_run))

    async def _finish(
        self, params: IngestParams, workflow_id: str, status: str, error: str | None
    ) -> None:
        """Write the run's terminal state to the record layer.

        Args:
            params: The workflow's own argument, for the tenant and document.
            workflow_id: This execution's id, the link between our row and the history.
            status: The terminal :class:`aegis.jobs.JobStatus` value, as its string.
            error: The failure reason, or ``None``.
        """
        await workflow.execute_activity(
            FINISH_INGEST,
            FinishInput(
                tenant_id=params.tenant_id,
                workflow_id=workflow_id,
                document_id=params.document_id,
                status=status,
                error=error,
            ),
            task_queue=DEFAULT_QUEUE,
            start_to_close_timeout=_BOOKKEEPING_TIMEOUT,
            retry_policy=_BOOKKEEPING_RETRY,
        )
