"""The re-index cadence passes the same gates an upload does (task 9.6).

The hole this closes. Upload and re-queue both call :func:`aegis.jobs.admit` before any
execution exists, so a tenant at its cap is refused with a visible 429 and nothing is
left in the orchestrator. The **scheduled** re-index called nothing: a Temporal Schedule
fired, an activity asked for a re-index, and a workflow started that re-runs every stage
but ``parse`` over every document the tenant has — the largest embedding bill this
platform generates, on a timer, with neither gate in front of it.

Two tests, because the gate has two halves and they refuse for unrelated reasons:
a budget refusal needs an administrator, a concurrency refusal clears by waiting.

Nothing here reaches a model or an orchestrator: the refusal happens strictly before
:func:`app.jobs.reindex.request_reindex` is called, which is exactly the property under
test — a refused tick leaves nothing behind to reconcile.
"""

from __future__ import annotations

import pytest
from aegis.governance import Budget, BudgetScope, BudgetWindow, UsageLedger
from aegis.jobs import Document, JobRun, JobStatus
from temporalio.exceptions import ApplicationError
from tests import pgsupport

from app.jobs.flows.contracts import ReindexTickInput
from app.jobs.reindex import REINDEX_JOB_TYPE, request_tenant_reindex

from .conftest import TENANT_A, seed_tenants

pytestmark = pytest.mark.asyncio


async def _seed_indexed_corpus(db, tenant_id: int, *rows) -> None:
    """Give the tenant one ingested document, plus whatever governance rows are passed."""
    async with db() as session:
        await pgsupport.seed(
            session,
            Document(
                tenant_id=tenant_id,
                filename="filing.pdf",
                content_sha256="c" * 64,
                mime_type="application/pdf",
                size_bytes=4 * 1024 * 1024,
                status=JobStatus.SUCCEEDED,
            ),
            *rows,
        )
        await session.commit()


async def test_a_cadence_tick_a_tenant_cannot_afford_is_refused_before_it_starts(
    wired_jobs, monkeypatch
):
    """The scheduled re-index meets the budget gate, and says which gate refused it."""
    started: list[object] = []

    async def _never(*args, **kwargs):
        started.append(kwargs)

    monkeypatch.setattr("app.jobs.reindex.request_reindex", _never)

    await seed_tenants(wired_jobs, TENANT_A)
    await _seed_indexed_corpus(
        wired_jobs,
        TENANT_A,
        Budget(
            tenant_id=TENANT_A,
            scope_type=BudgetScope.TENANT,
            scope_id=TENANT_A,
            window=BudgetWindow.DAY,
            usd_cap=0.10,
        ),
        UsageLedger(tenant_id=TENANT_A, cost_usd=0.09),
    )

    with pytest.raises(ApplicationError) as raised:
        await request_tenant_reindex(
            ReindexTickInput(
                tenant_id=TENANT_A, workflow_id="reindex-tick:7", reason="cadence"
            )
        )

    assert raised.value.type == "BudgetExceededError"
    assert raised.value.non_retryable is True
    assert started == [], "a refused tick must leave no execution in the orchestrator"


async def test_a_cadence_tick_is_refused_while_a_re_index_is_already_in_flight(
    wired_jobs, monkeypatch
):
    """The concurrency cap counts re-index rows, so a schedule cannot stack them.

    ``jobs.max_inflight.reindex`` defaults to one. Without this the cadence would happily
    start a second full-corpus rebuild on top of a first that was still running — two
    passes writing the same content-addressed keys, at twice the embedding cost.
    """
    started: list[object] = []

    async def _never(*args, **kwargs):
        started.append(kwargs)

    monkeypatch.setattr("app.jobs.reindex.request_reindex", _never)

    await seed_tenants(wired_jobs, TENANT_A)
    await _seed_indexed_corpus(
        wired_jobs,
        TENANT_A,
        JobRun(
            tenant_id=TENANT_A,
            job_type=REINDEX_JOB_TYPE,
            workflow_id="reindex:7",
            status=JobStatus.RUNNING,
        ),
    )

    with pytest.raises(ApplicationError) as raised:
        await request_tenant_reindex(
            ReindexTickInput(
                tenant_id=TENANT_A, workflow_id="reindex-tick:7", reason="cadence"
            )
        )

    assert raised.value.type == "AdmissionDeniedError"
    assert "jobs.max_inflight.reindex" in str(raised.value)
    assert started == []


async def test_an_affordable_cadence_tick_still_requests_its_re_index(
    wired_jobs, monkeypatch
):
    """The mutation guard: the gate must refuse the two cases above and nothing else."""

    class _Handle:
        id = "reindex:7"

    async def _ok(*args, **kwargs):
        return _Handle()

    monkeypatch.setattr("app.jobs.reindex.request_reindex", _ok)

    await seed_tenants(wired_jobs, TENANT_A)
    await _seed_indexed_corpus(wired_jobs, TENANT_A)

    assert (
        await request_tenant_reindex(
            ReindexTickInput(
                tenant_id=TENANT_A, workflow_id="reindex-tick:7", reason="cadence"
            )
        )
        == "reindex:7"
    )
