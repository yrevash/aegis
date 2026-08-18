"""The stage runner: one transaction, one commit, idempotent, and scoped to one tenant.

These call the activity **bodies directly**, with no server and no worker. That is
deliberate and it is what makes them sharp: every assertion is about a row in PostgreSQL
after the call, read back over the same ``NOSUPERUSER NOBYPASSRLS`` role the application
serves on.

What is proved here, and why each one is the failure it is:

* **The stage commit rule.** The handler's output column and the ``completed_stage`` bump
  land together. A stage that "finished" while its output rolled back is the exact bug the
  single-transaction design exists to prevent, so the failing-handler test reads *both*
  back and demands neither is there.
* **Idempotency, by invoking twice.** §3.0 hard-killed a worker and watched the in-flight
  activity replay, so this is a measured hazard rather than a hypothetical one. The test
  is parametrised over :data:`aegis.jobs.INGEST_STAGES`, so a stage added later cannot
  quietly skip it.
* **Cross-tenant invisibility, through the policy rather than a Python filter.** The
  activity's document read carries no ``WHERE tenant_id``; the scope on the connection is
  what hides the row. Running it for tenant 8 against tenant 7's document must fail.
"""

from __future__ import annotations

import asyncio

import pytest
from aegis.jobs import Document, JobRun, JobStatus
from aegis.jobs.stages import INGEST_STAGES, register_stage_handler
from sqlalchemy import func, select
from temporalio.exceptions import ApplicationError

from app.jobs.activities import finish_ingest, run_stage, start_ingest
from app.jobs.flows.contracts import FinishInput, StageInput

from .conftest import (
    TENANT_A,
    TENANT_B,
    register_recording_handlers,
    seed_document,
    seed_tenants,
)

_SHA_A = "a" * 64
_SHA_B = "b" * 64


async def _document(db, document_id: int) -> Document:
    """Read a document back over the serving role with no scope bound."""
    async with db() as session:
        return (
            await session.execute(select(Document).where(Document.id == document_id))
        ).scalar_one()


# ─────────────────────────────────────────────────────────────────────────────
# The stage commit rule
# ─────────────────────────────────────────────────────────────────────────────


async def test_a_stage_commits_its_output_and_its_progress_together(
    wired_jobs, stage_log
):
    await seed_tenants(wired_jobs, TENANT_A)
    document_id = await seed_document(wired_jobs, TENANT_A, sha=_SHA_A)
    register_recording_handlers(stage_log)

    outcome = await run_stage(
        StageInput(
            tenant_id=TENANT_A,
            workflow_id="ingest:7:1",
            document_id=document_id,
            stage="parse",
        )
    )

    assert outcome.committed is True
    document = await _document(wired_jobs, document_id)
    assert document.completed_stage == "parse"
    # The handler's own output, in the same transaction as the progress bump.
    assert document.page_count == 11


async def test_a_failing_stage_commits_neither_its_output_nor_its_progress(
    wired_jobs, stage_log
):
    await seed_tenants(wired_jobs, TENANT_A)
    document_id = await seed_document(wired_jobs, TENANT_A, sha=_SHA_A)

    async def exploding(session, *, tenant_id, document_id, stage):
        # Write first, then fail: this is the shape that leaves a half-finished stage
        # behind if the transaction boundary is wrong.
        document = (
            await session.execute(select(Document).where(Document.id == document_id))
        ).scalar_one()
        document.page_count = 999
        await session.flush()
        raise RuntimeError("the parser died on page 999")

    register_stage_handler("parse", exploding)

    with pytest.raises(RuntimeError, match="page 999"):
        await run_stage(
            StageInput(
                tenant_id=TENANT_A,
                workflow_id="ingest:7:1",
                document_id=document_id,
                stage="parse",
            )
        )

    document = await _document(wired_jobs, document_id)
    assert document.completed_stage is None
    assert document.page_count is None, (
        "the handler's write survived a failed stage: its output and its progress are "
        "not in one transaction"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Idempotency — by running it twice
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("stage", [spec.name for spec in INGEST_STAGES])
async def test_running_a_stage_twice_commits_once(wired_jobs, stage_log, stage):
    await seed_tenants(wired_jobs, TENANT_A)
    document_id = await seed_document(wired_jobs, TENANT_A, sha=_SHA_A)
    register_recording_handlers(stage_log)
    argument = StageInput(
        tenant_id=TENANT_A,
        workflow_id="ingest:7:1",
        document_id=document_id,
        stage=stage,
    )

    first = await run_stage(argument)
    second = await run_stage(argument)

    assert first.committed is True
    assert second.committed is False
    # And the handler was not run a second time, so a stage whose work is expensive or
    # billed is not paid for twice on a replay.
    assert stage_log.stages() == [stage]
    document = await _document(wired_jobs, document_id)
    assert document.completed_stage == stage


async def test_claiming_a_run_twice_produces_one_job_row(wired_jobs, stage_log):
    await seed_tenants(wired_jobs, TENANT_A)
    document_id = await seed_document(wired_jobs, TENANT_A, sha=_SHA_A)
    argument = StageInput(
        tenant_id=TENANT_A,
        workflow_id="ingest:7:1",
        document_id=document_id,
        stage="ingest",
    )

    first = await start_ingest(argument)
    second = await start_ingest(argument)

    assert first.job_run_id == second.job_run_id
    async with wired_jobs() as session:
        rows = await session.scalar(select(func.count()).select_from(JobRun.__table__))
    assert rows == 1


async def test_claiming_a_partly_ingested_document_reports_where_to_resume(
    wired_jobs, stage_log
):
    # The across-executions resume path: a brand-new workflow for a document that already
    # parsed must not re-parse it, and this is the value the workflow feeds to
    # ``remaining_stages``.
    await seed_tenants(wired_jobs, TENANT_A)
    document_id = await seed_document(wired_jobs, TENANT_A, sha=_SHA_A)
    register_recording_handlers(stage_log)
    for stage in ("parse", "chunk"):
        await run_stage(
            StageInput(
                tenant_id=TENANT_A,
                workflow_id="ingest:7:1",
                document_id=document_id,
                stage=stage,
            )
        )

    outcome = await start_ingest(
        StageInput(
            tenant_id=TENANT_A,
            workflow_id="ingest:7:1:retry",
            document_id=document_id,
            stage="ingest",
        )
    )

    assert outcome.completed_stage == "chunk"


# ─────────────────────────────────────────────────────────────────────────────
# Tenant isolation, enforced by the database and not by a Python filter
# ─────────────────────────────────────────────────────────────────────────────


async def test_a_tenants_activity_cannot_touch_another_tenants_document(
    wired_jobs, stage_log
):
    await seed_tenants(wired_jobs, TENANT_A, TENANT_B)
    document_id = await seed_document(wired_jobs, TENANT_A, sha=_SHA_A)
    register_recording_handlers(stage_log)

    with pytest.raises(ApplicationError) as raised:
        await run_stage(
            StageInput(
                tenant_id=TENANT_B,
                workflow_id="ingest:8:1",
                document_id=document_id,
                stage="parse",
            )
        )

    assert raised.value.type == "DocumentNotVisible"
    assert raised.value.non_retryable is True
    # The positive control: the same document, the same stage, its own tenant — so the
    # refusal above is the policy working and not a broken query.
    assert stage_log.calls == []
    outcome = await run_stage(
        StageInput(
            tenant_id=TENANT_A,
            workflow_id="ingest:7:1",
            document_id=document_id,
            stage="parse",
        )
    )
    assert outcome.committed is True


async def test_each_tenants_document_is_ingested_independently(wired_jobs, stage_log):
    await seed_tenants(wired_jobs, TENANT_A, TENANT_B)
    document_a = await seed_document(wired_jobs, TENANT_A, sha=_SHA_A)
    document_b = await seed_document(wired_jobs, TENANT_B, sha=_SHA_B)
    register_recording_handlers(stage_log)

    await run_stage(
        StageInput(
            tenant_id=TENANT_A,
            workflow_id="ingest:7:1",
            document_id=document_a,
            stage="parse",
        )
    )

    assert (await _document(wired_jobs, document_a)).completed_stage == "parse"
    assert (await _document(wired_jobs, document_b)).completed_stage is None


# ─────────────────────────────────────────────────────────────────────────────
# Refusals
# ─────────────────────────────────────────────────────────────────────────────


async def test_a_stage_with_no_handler_fails_rather_than_recording_progress(
    wired_jobs, stage_log
):
    await seed_tenants(wired_jobs, TENANT_A)
    document_id = await seed_document(wired_jobs, TENANT_A, sha=_SHA_A)

    with pytest.raises(ApplicationError) as raised:
        await run_stage(
            StageInput(
                tenant_id=TENANT_A,
                workflow_id="ingest:7:1",
                document_id=document_id,
                stage="parse",
            )
        )

    assert raised.value.type == "UnregisteredStage"
    assert raised.value.non_retryable is True
    assert (await _document(wired_jobs, document_id)).completed_stage is None


async def test_an_unknown_stage_name_is_never_written_to_the_row(wired_jobs, stage_log):
    await seed_tenants(wired_jobs, TENANT_A)
    document_id = await seed_document(wired_jobs, TENANT_A, sha=_SHA_A)
    register_recording_handlers(stage_log)

    with pytest.raises(ApplicationError) as raised:
        await run_stage(
            StageInput(
                tenant_id=TENANT_A,
                workflow_id="ingest:7:1",
                document_id=document_id,
                stage="ocr",
            )
        )

    assert raised.value.type == "UnknownStage"
    # An unrecognised value in ``completed_stage`` would break every future resume of the
    # row, so it must never be written even once.
    assert (await _document(wired_jobs, document_id)).completed_stage is None


async def test_a_handler_cannot_move_a_document_to_another_tenant(wired_jobs, stage_log):
    await seed_tenants(wired_jobs, TENANT_A, TENANT_B)
    document_id = await seed_document(wired_jobs, TENANT_A, sha=_SHA_A)

    async def greedy(session, *, tenant_id, document_id, stage):
        return {"tenant_id": TENANT_B, "completed_stage": "graph"}

    register_stage_handler("parse", greedy)

    with pytest.raises(ApplicationError) as raised:
        await run_stage(
            StageInput(
                tenant_id=TENANT_A,
                workflow_id="ingest:7:1",
                document_id=document_id,
                stage="parse",
            )
        )

    assert raised.value.type == "ForbiddenStageWrite"
    document = await _document(wired_jobs, document_id)
    assert document.tenant_id == TENANT_A
    assert document.completed_stage is None


async def test_finishing_a_run_into_a_non_terminal_status_is_refused(
    wired_jobs, stage_log
):
    await seed_tenants(wired_jobs, TENANT_A)
    document_id = await seed_document(wired_jobs, TENANT_A, sha=_SHA_A)
    await start_ingest(
        StageInput(
            tenant_id=TENANT_A,
            workflow_id="ingest:7:1",
            document_id=document_id,
            stage="ingest",
        )
    )

    with pytest.raises(ApplicationError) as raised:
        await finish_ingest(
            FinishInput(
                tenant_id=TENANT_A,
                workflow_id="ingest:7:1",
                document_id=document_id,
                status="running",
            )
        )

    assert raised.value.type == "NonTerminalStatus"


async def test_finishing_a_run_records_its_terminal_state_on_both_rows(
    wired_jobs, stage_log
):
    await seed_tenants(wired_jobs, TENANT_A)
    document_id = await seed_document(wired_jobs, TENANT_A, sha=_SHA_A)
    await start_ingest(
        StageInput(
            tenant_id=TENANT_A,
            workflow_id="ingest:7:1",
            document_id=document_id,
            stage="ingest",
        )
    )

    await finish_ingest(
        FinishInput(
            tenant_id=TENANT_A,
            workflow_id="ingest:7:1",
            document_id=document_id,
            status="failed",
            error="the parser died",
        )
    )

    document = await _document(wired_jobs, document_id)
    assert document.status is JobStatus.FAILED
    assert document.error == "the parser died"
    async with wired_jobs() as session:
        job = (
            await session.execute(
                select(JobRun).where(JobRun.workflow_id == "ingest:7:1")
            )
        ).scalar_one()
    assert job.status is JobStatus.FAILED
    assert job.finished_at is not None


async def test_two_concurrent_attempts_of_one_stage_run_the_handler_once(
    wired_jobs, stage_log
):
    """The idempotency short-circuit must be a lock, not a read-then-decide.

    A hard-killed worker is not the only way one stage gets two attempts: a heartbeat
    timeout can start the retry while the original attempt is still alive on a machine
    that merely went quiet. Both would read ``completed_stage`` before either wrote it, so
    a Python comparison alone lets both run the handler — and for ``embed`` that is the
    provider's bill, paid twice, with nothing in any log to say why.
    """
    await seed_tenants(wired_jobs, TENANT_A)
    document_id = await seed_document(wired_jobs, TENANT_A, sha=_SHA_A)
    # A delay long enough that a second attempt reading before the first commits is a
    # certainty rather than a race the test might lose.
    register_recording_handlers(stage_log, delays={"parse": 0.5})
    argument = StageInput(
        tenant_id=TENANT_A,
        workflow_id="ingest:7:1",
        document_id=document_id,
        stage="parse",
    )

    first, second = await asyncio.gather(run_stage(argument), run_stage(argument))

    assert sorted([first.committed, second.committed]) == [False, True]
    assert stage_log.stages() == ["parse"], (
        "both attempts ran the handler: the 'already committed?' check is a read-then-"
        f"decide race rather than a serialisation point. Handlers ran: {stage_log.stages()}"
    )
    document = await _document(wired_jobs, document_id)
    assert document.completed_stage == "parse"
    assert document.page_count == 11


async def test_finishing_a_run_twice_does_not_move_its_finished_at(wired_jobs, stage_log):
    """A replayed close-out must be a no-op, not a second close-out.

    ``finished_at`` is the one column where a double write never looks like an error: the
    row still says ``failed``, the reason is still right, and only the duration is quietly
    wrong by however long the replay took.
    """
    await seed_tenants(wired_jobs, TENANT_A)
    document_id = await seed_document(wired_jobs, TENANT_A, sha=_SHA_A)
    argument = StageInput(
        tenant_id=TENANT_A,
        workflow_id="ingest:7:1",
        document_id=document_id,
        stage="ingest",
    )
    await start_ingest(argument)
    finish = FinishInput(
        tenant_id=TENANT_A,
        workflow_id="ingest:7:1",
        document_id=document_id,
        status="succeeded",
    )

    await finish_ingest(finish)
    async with wired_jobs() as session:
        first = (
            await session.execute(select(JobRun).where(JobRun.workflow_id == "ingest:7:1"))
        ).scalar_one()
        first_finished_at = first.finished_at
    await finish_ingest(finish)

    async with wired_jobs() as session:
        second = (
            await session.execute(select(JobRun).where(JobRun.workflow_id == "ingest:7:1"))
        ).scalar_one()
    assert second.status is JobStatus.SUCCEEDED
    assert second.finished_at == first_finished_at


async def test_claiming_a_reconciled_run_re_opens_its_row(wired_jobs, stage_log):
    """The reconciler's restart path, from the claiming end.

    The reconciler marks a row ``RECONCILING`` and starts a fresh execution under the same
    workflow id. If the claim were an insert that did nothing on conflict, that row would
    stay ``RECONCILING`` for the whole of the run that is now genuinely under way — a
    status nothing else sweeps and no tenant can interpret.
    """
    await seed_tenants(wired_jobs, TENANT_A)
    document_id = await seed_document(wired_jobs, TENANT_A, sha=_SHA_A)
    argument = StageInput(
        tenant_id=TENANT_A,
        workflow_id="ingest:7:1",
        document_id=document_id,
        stage="ingest",
    )
    await start_ingest(argument)
    await finish_ingest(
        FinishInput(
            tenant_id=TENANT_A,
            workflow_id="ingest:7:1",
            document_id=document_id,
            status="failed",
            error="the orchestrator lost this workflow",
        )
    )

    outcome = await start_ingest(argument)

    async with wired_jobs() as session:
        row = (
            await session.execute(select(JobRun).where(JobRun.workflow_id == "ingest:7:1"))
        ).scalar_one()
    assert row.id == outcome.job_run_id
    assert row.status is JobStatus.RUNNING
    assert row.error is None
    assert row.finished_at is None, (
        "the re-opened run still carries the previous attempt's end time, so its duration "
        "is a number about a run that is not this one"
    )
