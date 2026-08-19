"""The substrate end to end: a real server, real workers, real rows.

Nothing here is stubbed. A Temporal dev server is started, the platform's own
:func:`app.jobs.worker.run_workers` is launched **through its in-process launch mode** —
the same call the API's lifespan makes — and a real :class:`app.jobs.flows.IngestWorkflow`
drives the real activities against the scratch PostgreSQL database as the ``NOSUPERUSER
NOBYPASSRLS`` role.

Two claims are proved that nothing cheaper could prove:

* **The wiring holds.** The workflow reaches the activities, the activities find a scoped
  session, the stages commit in order, and the document ends ``SUCCEEDED`` with
  ``completed_stage`` at the last stage. A unit test of each part would pass with the
  queue routing broken.
* **A CPU-bound parse queue with one slot never runs two parses at once.** Two documents
  are ingested concurrently and the handler records the greatest number of parses in
  flight at any instant. If ``max_concurrent_activities`` were not honoured — or if
  ``parse`` were routed to the wide queue by a one-word mistake — that number would be
  two, and a 16 GB box would be out of memory rather than merely failing a test.

The concurrency measurement is a *maximum over the whole run*, not a snapshot: a snapshot
taken at the wrong instant would pass with the limit removed.
"""

from __future__ import annotations

import asyncio

import pytest
from aegis.jobs import Document, JobRun, JobStatus
from aegis.jobs.stages import CPU_QUEUE, DEFAULT_QUEUE
from sqlalchemy import select
from temporalio.testing import WorkflowEnvironment

from app.jobs.client import reset_temporal_client, set_temporal_client
from app.jobs.flows import INGEST_WORKFLOW
from app.jobs.flows.contracts import IngestParams, IngestResult
from app.jobs.worker import start_worker_task

from .conftest import (
    TENANT_A,
    TENANT_B,
    free_port,
    register_recording_handlers,
    seed_document,
    seed_tenants,
    skip_without_temporal,
    temporal_cli_path,
)

#: How long a ``parse`` handler occupies its slot. Long enough that two overlapping
#: parses would be observed with certainty if the single-slot limit were not enforced,
#: short enough that the test costs a couple of seconds of wall clock.
_PARSE_SECONDS = 0.4

#: How long an ``enrich`` handler occupies its slot. ``enrich`` is on the *wide* queue,
#: and this number is chosen so the two documents' enrich calls genuinely overlap — the
#: positive control. Without it, "no two parses overlapped" would be equally consistent
#: with a worker pool that never runs anything concurrently at all.
_ENRICH_SECONDS = 1.0


async def _run_workflows(env: WorkflowEnvironment, params: list[IngestParams]):
    """Start the workers, run the given ingests concurrently, then shut down cleanly.

    Goes through :func:`app.jobs.worker.start_worker_task` — the in-process launch mode
    the API lifespan uses — rather than constructing workers here, so what is under test
    is the shipped bootstrap and not a test-local imitation of it.

    Args:
        env: The started test environment, whose client the workers use.
        params: One :class:`IngestParams` per document to ingest.

    Returns:
        The workflows' results, in the order the params were given.
    """
    set_temporal_client(env.client)
    stop = asyncio.Event()
    worker = start_worker_task(stop)
    try:
        results = await asyncio.gather(
            *[
                env.client.execute_workflow(
                    INGEST_WORKFLOW,
                    param,
                    id=f"ingest:{param.tenant_id}:{param.document_id}",
                    task_queue="aegis-default",
                    result_type=IngestResult,
                )
                for param in params
            ]
        )
    finally:
        stop.set()
        await asyncio.wait_for(worker, timeout=30)
        reset_temporal_client()
    return results


@pytest.fixture
async def temporal_env():
    """A real Temporal dev server for one test, on a port nothing else holds."""
    binary = temporal_cli_path()
    if binary is None:
        skip_without_temporal(
            "that a real workflow drives the real activities to completion, and that a "
            "single-slot CPU queue serialises two parses."
        )
    env = await WorkflowEnvironment.start_local(
        dev_server_existing_path=binary,
        port=free_port(),
        ui=False,
    )
    try:
        yield env
    finally:
        await env.shutdown()


async def test_a_workflow_ingests_a_document_through_every_stage(
    temporal_env, wired_jobs, stage_log
):
    await seed_tenants(wired_jobs, TENANT_A)
    document_id = await seed_document(wired_jobs, TENANT_A, sha="a" * 64)
    register_recording_handlers(stage_log)

    (result,) = await _run_workflows(
        temporal_env, [IngestParams(tenant_id=TENANT_A, document_id=document_id)]
    )

    assert result.stages_run == ("parse", "chunk", "enrich", "embed", "index", "graph")
    assert stage_log.stages() == list(result.stages_run)
    async with wired_jobs() as session:
        document = (
            await session.execute(select(Document).where(Document.id == document_id))
        ).scalar_one()
        job = (
            await session.execute(
                select(JobRun).where(JobRun.workflow_id == f"ingest:{TENANT_A}:{document_id}")
            )
        ).scalar_one()
    assert document.status is JobStatus.SUCCEEDED
    assert document.completed_stage == "graph"
    # The handlers' own output, committed alongside the progress it belongs to.
    assert (document.page_count, document.chunk_count) == (11, 42)
    assert job.status is JobStatus.SUCCEEDED
    assert job.tenant_id == TENANT_A
    assert job.finished_at is not None
    # The orchestrator's run id reached our row, which is what a support engineer takes
    # to the Temporal UI.
    assert job.run_id


async def test_two_parses_never_run_at_the_same_time(
    temporal_env, wired_jobs, stage_log
):
    await seed_tenants(wired_jobs, TENANT_A, TENANT_B)
    first = await seed_document(wired_jobs, TENANT_A, sha="a" * 64)
    second = await seed_document(wired_jobs, TENANT_B, sha="b" * 64)
    register_recording_handlers(
        stage_log, delays={"parse": _PARSE_SECONDS, "enrich": _ENRICH_SECONDS}
    )

    await _run_workflows(
        temporal_env,
        [
            IngestParams(tenant_id=TENANT_A, document_id=first),
            IngestParams(tenant_id=TENANT_B, document_id=second),
        ],
    )

    parses = [call for call in stage_log.calls if call[0] == "parse"]
    assert len(parses) == 2, "both documents must actually have been parsed"
    assert stage_log.peak_by_queue[CPU_QUEUE] == 1, (
        "two activities ran concurrently on a queue declared with one slot: a Docling "
        f"parse peaks at ~2.2 GB, so this is an out-of-memory box. Observed "
        f"{stage_log.peak_by_queue[CPU_QUEUE]} in flight on {CPU_QUEUE}."
    )
    # The positive control. Without it, "no two parses overlapped" would be equally
    # consistent with a worker pool that never runs two things at once for any reason —
    # in which case the single-slot declaration would be proving nothing.
    assert stage_log.peak_by_queue[DEFAULT_QUEUE] >= 2, (
        "nothing ran concurrently anywhere, so the CPU-queue assertion above is vacuous"
    )


async def test_a_failed_stage_is_recorded_by_name_with_its_real_cause(
    temporal_env, wired_jobs, stage_log
):
    """The row must say *which* stage died and *why* — audit C, C2.

    Measured on the cold demo path with ``embed`` failing: ``documents.error`` and the
    tenant-visible ingest log carried ``"Activity task failed"``, Temporal's own wrapper.
    The real cause (``litellm.APIError: RBAC: access denied``) was one link down the
    ``__cause__`` chain and reached nothing, and the only stage named anywhere was
    ``enrich`` — the last one that **succeeded** — so the log read "ingest failed at
    enrich" about a run in which enrich was fine.
    """
    from aegis.jobs.stages import register_stage_handler
    from temporalio.exceptions import ApplicationError

    await seed_tenants(wired_jobs, TENANT_A)
    document_id = await seed_document(wired_jobs, TENANT_A, sha="c" * 64)
    register_recording_handlers(stage_log)

    async def _embed_denied(session, *, tenant_id, document_id, stage):  # noqa: ANN001
        raise ApplicationError(
            "litellm.APIError: RBAC: access denied", type="APIError", non_retryable=True
        )

    register_stage_handler("embed", _embed_denied)

    with pytest.raises(Exception):  # noqa: B017 - the workflow re-raises whatever failed
        await _run_workflows(
            temporal_env, [IngestParams(tenant_id=TENANT_A, document_id=document_id)]
        )

    async with wired_jobs() as session:
        document = (
            await session.execute(select(Document).where(Document.id == document_id))
        ).scalar_one()

    assert document.status is JobStatus.FAILED
    # The three stages before embed really did commit, so the failure is genuinely
    # *between* enrich and embed and the naming below is not trivially right.
    assert document.completed_stage == "enrich"
    error = document.error or ""
    assert "the embed stage failed" in error, error
    assert "RBAC: access denied" in error, error
    assert error != "Activity task failed"
