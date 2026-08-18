"""Ten re-index requests inside the window produce one run.

**Debounce is not idempotency**, and the distinction is the whole point of this file. An
idempotency key says "this exact work is already queued, return it" — and it cannot
express what is needed here, because ten uploads are ten *different* pieces of work. What
must happen instead is that work *of this kind* already pending for this tenant absorbs the
new request and pushes its run time out.

So the assertion is not "one row was written". A broken debounce would also write one row,
by upserting ten times over the same key. What is asserted is that **the work ran once**:
the handler is a real coroutine that records every call, and one call is the only outcome
consistent with the fold actually happening.

Everything is real: a Temporal dev server, the platform's own
:func:`app.jobs.worker.start_worker_task` (the same call the API lifespan makes), the real
:class:`app.jobs.flows.reindex.ReindexWorkflow`, and the scratch PostgreSQL as the
``NOSUPERUSER NOBYPASSRLS`` role. Nothing here is stubbed except the *domain* work of
re-indexing, which is Phase 4's and is registered here exactly the way Phase 4 will
register it.

The negative control at the bottom is what stops the headline being true for the wrong
reason: two requests **separated by more than the window** must produce two runs. Without
it, "one execution" would be equally consistent with a workflow that ignored every signal —
or with one that could never run twice at all.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest
from aegis.jobs import JobRun, JobStatus
from sqlalchemy import select
from temporalio.testing import WorkflowEnvironment

from app.jobs.client import reset_temporal_client, set_temporal_client
from app.jobs.flows.contracts import ReindexResult
from app.jobs.flows.reindex import reindex_workflow_id
from app.jobs.reindex import (
    REINDEX_JOB_TYPE,
    clear_reindex_handler,
    register_reindex_handler,
    request_reindex,
)
from app.jobs.worker import start_worker_task

from .conftest import (
    TENANT_A,
    free_port,
    seed_tenants,
    skip_without_temporal,
    temporal_cli_path,
)

#: The debounce window these tests use. Long enough that ten requests issued back to back
#: land well inside it on any machine, short enough that the negative control's "wait for
#: the window to close, then ask again" costs a few seconds rather than a minute.
_WINDOW = 3

#: The ceiling on folding. Far above the window, so nothing here is testing the ceiling by
#: accident — a run that happened because ``max_wait`` expired would prove nothing about
#: the timer reset.
_MAX_WAIT = 120

#: How long to wait for a folded run to finish: the window, plus room for the activity and
#: for a slow machine.
_RESULT_TIMEOUT = _WINDOW + 30


@dataclass
class _ReindexLog:
    """A record of every re-index the handler actually performed.

    Attributes:
        calls: One entry per handler invocation, carrying how many requests that run stood
            for and the reasons they gave.
    """

    calls: list[tuple[int, tuple[str, ...]]] = field(default_factory=list)


@pytest.fixture
def reindex_log():
    """Register a real re-index handler for one test, and unregister it afterwards.

    The handler is a coroutine on the substrate's own session — the shape
    :func:`app.jobs.reindex.register_reindex_handler` is documented against — so what runs
    here is the contract Phase 4's real index rebuild will be written to.
    """
    clear_reindex_handler()
    log = _ReindexLog()

    async def handler(
        session: Any,  # noqa: ANN401 - AsyncSession
        *,
        tenant_id: int | None,
        folded: int,
        reasons: tuple[str, ...],
    ) -> Mapping[str, Any]:
        log.calls.append((folded, reasons))
        return {"chunks_reindexed": folded * 10}

    register_reindex_handler(handler)
    yield log
    clear_reindex_handler()


@pytest.fixture
async def reindex_env(wired_jobs):
    """A dev server with the platform's own workers running against it.

    Goes through :func:`app.jobs.worker.start_worker_task` rather than building a worker
    here, so the debounce is proved on the shipped bootstrap and not on a test-local
    imitation of it.
    """
    binary = temporal_cli_path()
    if binary is None:
        skip_without_temporal(
            "that ten re-index requests inside the debounce window fold into a single "
            "run, and that two requests outside it do not."
        )
    env = await WorkflowEnvironment.start_local(
        dev_server_existing_path=binary, port=free_port(), ui=False
    )
    set_temporal_client(env.client)
    stop = asyncio.Event()
    worker = start_worker_task(stop)
    try:
        yield env
    finally:
        stop.set()
        await asyncio.wait_for(worker, timeout=30)
        reset_temporal_client()
        await env.shutdown()


async def _reindex_rows(db) -> list[JobRun]:
    """Read every re-index job row back over the serving role."""
    async with db() as session:
        return list(
            (
                await session.execute(
                    select(JobRun).where(JobRun.job_type == REINDEX_JOB_TYPE)
                )
            ).scalars()
        )


async def test_ten_requests_inside_the_window_produce_one_run(
    reindex_log, reindex_env, wired_jobs
):
    await seed_tenants(wired_jobs, TENANT_A)

    handles = []
    run_ids = set()
    for index in range(10):
        handle = await request_reindex(
            reindex_env.client,
            tenant_id=TENANT_A,
            reason=f"document {index} ingested",
            debounce_seconds=_WINDOW,
            max_wait_seconds=_MAX_WAIT,
        )
        handles.append(handle)
        # Asked of the orchestrator, once per request, rather than read off the handle:
        # signal-with-start does not report a run id on the returned handle, and an
        # assertion over ten ``None``s would be vacuous in exactly the case it is meant to
        # catch.
        run_ids.add((await handle.describe()).run_id)
    result: ReindexResult = await asyncio.wait_for(
        handles[-1].result(), timeout=_RESULT_TIMEOUT
    )

    # ── The fold is structural: one workflow id, therefore one execution. ────
    assert {handle.id for handle in handles} == {reindex_workflow_id(TENANT_A)}
    assert len(run_ids) == 1, (
        "the ten requests started more than one execution, so the per-tenant workflow id "
        f"is not doing the folding it exists to do; run ids seen: {run_ids}"
    )
    # ── And the work itself ran once, which is what "one run" actually means. ──
    assert len(reindex_log.calls) == 1, (
        f"the re-index handler ran {len(reindex_log.calls)} times for one burst; a row "
        "upserted ten times would look identical from the database alone"
    )
    folded, reasons = reindex_log.calls[0]
    assert folded == 10
    assert reasons == tuple(f"document {index} ingested" for index in range(10)), (
        "the folded run must carry every request's reason, or a tenant cannot find out "
        "what caused the re-index they are looking at"
    )
    assert result.folded == 10

    rows = await _reindex_rows(wired_jobs)
    assert len(rows) == 1
    assert rows[0].status is JobStatus.SUCCEEDED
    assert rows[0].tenant_id == TENANT_A
    assert rows[0].result == {"chunks_reindexed": 100}
    assert rows[0].payload["folded"] == 10


async def test_two_requests_outside_the_window_produce_two_runs(
    reindex_log, reindex_env, wired_jobs
):
    await seed_tenants(wired_jobs, TENANT_A)

    first = await request_reindex(
        reindex_env.client,
        tenant_id=TENANT_A,
        reason="first burst",
        debounce_seconds=_WINDOW,
        max_wait_seconds=_MAX_WAIT,
    )
    first_run_id = (await first.describe()).run_id
    await asyncio.wait_for(first.result(), timeout=_RESULT_TIMEOUT)
    second = await request_reindex(
        reindex_env.client,
        tenant_id=TENANT_A,
        reason="second burst",
        debounce_seconds=_WINDOW,
        max_wait_seconds=_MAX_WAIT,
    )
    second_run_id = (await second.describe()).run_id
    await asyncio.wait_for(second.result(), timeout=_RESULT_TIMEOUT)

    # The control for the test above: a request that arrives after the window closed
    # genuinely starts a new execution and genuinely re-indexes. Without this, "one
    # execution" would be equally consistent with signals that do nothing at all.
    assert first_run_id != second_run_id
    assert [call[1] for call in reindex_log.calls] == [
        ("first burst",),
        ("second burst",),
    ]
    # Still one row, because the debounced workflow id is reused for every window and the
    # record write is an upsert on it. A bare insert would have raised on the unique
    # constraint here, and ``DO NOTHING`` would have left the row describing the first run
    # forever.
    rows = await _reindex_rows(wired_jobs)
    assert len(rows) == 1
    assert rows[0].payload["reasons"] == ["second burst"]
    assert rows[0].result == {"chunks_reindexed": 10}
