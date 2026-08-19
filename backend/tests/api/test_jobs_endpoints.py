"""The job endpoints over HTTP: the visible 429, the workflow that never started, the 403.

The three claims §3.4's definition of done makes, each asserted through the real ASGI app
against the real scratch PostgreSQL served by the ``NOSUPERUSER NOBYPASSRLS`` role.

The Temporal client is a recording double rather than a live server, and that is the point
of the middle test: the guarantee is that a refused job **starts no workflow**, which can
only be shown by counting the calls a real start path would have made. A test that merely
asserted a 429 would pass on an implementation that started the workflow and then refused.

Nothing else is faked. The job rows, the documents, the tenants, the ledger, the budgets
and the settings are all real rows the routes read through their production code paths.
"""

from __future__ import annotations

import pgsupport
import pytest
from aegis.governance.models import Budget, BudgetScope, BudgetWindow, UsageLedger
from aegis.jobs import Document, JobRun, JobStatus
from aegis.settings.spec import spec_for

from app.api.schemas import Role
from app.core.security import TENANT_ADMIN, create_access_token
from app.data import Tenant, User, get_sessionmaker
from app.jobs.client import reset_temporal_client, set_temporal_client

pytestmark = pytest.mark.asyncio

_TENANT_A = 1
_TENANT_B = 2
_USER_A = 11
_USER_B = 22

#: Read from the catalogue rather than restated: these tests must keep meaning the same
#: thing when a platform admin changes the default from the dashboard.
_CAP: int = spec_for("jobs.max_inflight.ingest").default
#: The tenant's cap lives on a real ``budgets`` row and nowhere else, so the test writes
#: the row it asserts against rather than reading a catalogue default that no longer exists.
_USD_CAP: float = 100.0
_PER_MB: float = spec_for("jobs.estimated_cost_usd.ingest_per_mb").default


class _FakeHandle:
    """A workflow handle whose ``cancel`` records that it was called."""

    def __init__(self, workflow_id: str, cancelled: list[str]) -> None:
        self.workflow_id = workflow_id
        self._cancelled = cancelled

    async def cancel(self) -> None:
        """Record the cancellation request."""
        self._cancelled.append(self.workflow_id)


class _FakeTemporalClient:
    """A Temporal client double that records every call the routes make on it.

    Installed through :func:`app.jobs.client.set_temporal_client` — the shipped seam the
    durability tests already use — so the routes run unmodified.
    """

    def __init__(self) -> None:
        self.started: list[str] = []
        self.cancelled: list[str] = []

    async def start_workflow(self, *args: object, **kwargs: object) -> object:
        """Record a workflow start and return a handle-shaped object."""
        workflow_id = str(kwargs.get("id", ""))
        self.started.append(workflow_id)
        return _FakeHandle(workflow_id, self.cancelled)

    def get_workflow_handle(self, workflow_id: str) -> _FakeHandle:
        """Return a handle whose ``cancel`` records the request."""
        return _FakeHandle(workflow_id, self.cancelled)


@pytest.fixture
def temporal():
    """Install the recording Temporal double for one test, then clear it."""
    client = _FakeTemporalClient()
    set_temporal_client(client)  # type: ignore[arg-type] - a deliberate test double
    try:
        yield client
    finally:
        reset_temporal_client()


def _headers(*, tenant_id: int, username: str, user_id: int) -> dict[str, str]:
    """A tenant-admin bearer for one tenant."""
    token = create_access_token(
        user_id=user_id, username=username, role=TENANT_ADMIN, tenant_id=tenant_id
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_tenants() -> None:
    """Two tenants with one user each — the isolation subjects."""
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Tenant(id=_TENANT_A, name="Tenant A"),
            Tenant(id=_TENANT_B, name="Tenant B"),
            User(id=_USER_A, username="a-admin", role=Role.ADMIN, tenant_id=_TENANT_A),
            User(id=_USER_B, username="b-admin", role=Role.ADMIN, tenant_id=_TENANT_B),
            Budget(
                tenant_id=_TENANT_A,
                scope_type=BudgetScope.TENANT,
                scope_id=_TENANT_A,
                window=BudgetWindow.DAY,
                usd_cap=_USD_CAP,
            ),
        )
        await session.commit()


async def _seed_document(tenant_id: int, *, size_bytes: int, sha: str) -> int:
    """Insert one uploaded document for a tenant and return its id."""
    async with get_sessionmaker()() as session:
        document = Document(
            tenant_id=tenant_id,
            filename="filing.pdf",
            content_sha256=sha,
            mime_type="application/pdf",
            size_bytes=size_bytes,
            status=JobStatus.FAILED,
        )
        session.add(document)
        await session.commit()
        return document.id


async def _seed_job(
    tenant_id: int,
    *,
    document_id: int | None,
    status: JobStatus,
    workflow_id: str,
) -> int:
    """Insert one job row for a tenant and return its id."""
    async with get_sessionmaker()() as session:
        job = JobRun(
            tenant_id=tenant_id,
            job_type="ingest",
            workflow_id=workflow_id,
            status=status,
            payload={} if document_id is None else {"document_id": document_id},
        )
        session.add(job)
        await session.commit()
        return job.id


# ── (a) The (n+1)th job is refused with a visible 429 ────────────────────────


async def test_requeue_past_the_inflight_cap_returns_429_with_a_reason(
    client, db, temporal
) -> None:
    """A tenant at its cap gets a 429 naming the cap — not a silent enqueue.

    The job being re-queued is itself terminal, so it is not one of the ``_CAP`` rows
    holding the slots: what is refused is genuinely the *next* job.
    """
    await _seed_tenants()
    document_id = await _seed_document(_TENANT_A, size_bytes=1024, sha="a" * 64)
    for index in range(_CAP):
        await _seed_job(
            _TENANT_A,
            document_id=document_id,
            status=JobStatus.RUNNING,
            workflow_id=f"wf-live-{index}",
        )
    failed = await _seed_job(
        _TENANT_A,
        document_id=document_id,
        status=JobStatus.FAILED,
        workflow_id="wf-failed",
    )

    res = await client.post(
        f"/jobs/{failed}/requeue",
        headers=_headers(tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A),
    )

    assert res.status_code == 429
    body = res.json()
    assert "jobs.max_inflight.ingest" in body["detail"]
    assert str(_CAP) in body["detail"]
    assert res.headers["X-Admission-Gate"] == "concurrency"
    # Backpressure that started the work anyway would not be backpressure.
    assert temporal.started == []


async def test_requeue_inside_the_cap_starts_exactly_one_workflow(
    client, db, temporal
) -> None:
    """The positive control: without it, "zero starts" is consistent with a broken path."""
    await _seed_tenants()
    document_id = await _seed_document(_TENANT_A, size_bytes=1024, sha="a" * 64)
    failed = await _seed_job(
        _TENANT_A,
        document_id=document_id,
        status=JobStatus.FAILED,
        workflow_id="wf-failed",
    )

    res = await client.post(
        f"/jobs/{failed}/requeue",
        headers=_headers(tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A),
    )

    assert res.status_code == 200, res.text
    assert len(temporal.started) == 1
    assert temporal.started[0].startswith(f"ingest:{_TENANT_A}:{document_id}:")


@pytest.mark.parametrize(
    "status", [JobStatus.PENDING, JobStatus.RUNNING, JobStatus.RECONCILING]
)
async def test_requeueing_a_job_that_has_not_finished_is_refused_with_409(
    client, db, temporal, status
) -> None:
    """A re-queue *adds* an execution; it does not replace one.

    The new run gets a fresh ``workflow_id`` (it has to — the old one is unique and its row
    is the previous attempt's) and nothing here cancels the old execution. So re-queueing a
    live job puts **two** workflows on one document, each committing stages the other has
    already committed and each writing ``documents.completed_stage`` — which is the
    concurrency that can move that column out of order. The refusal is at the only place
    that creates the second execution, and the assertion that matters is that the
    orchestrator was never called.
    """
    await _seed_tenants()
    document_id = await _seed_document(_TENANT_A, size_bytes=1024, sha="a" * 64)
    live = await _seed_job(
        _TENANT_A,
        document_id=document_id,
        status=status,
        workflow_id="wf-live",
    )

    res = await client.post(
        f"/jobs/{live}/requeue",
        headers=_headers(tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A),
    )

    assert res.status_code == 409, res.text
    assert status.value in res.json()["detail"]
    assert temporal.started == [], (
        "a second workflow was started over a document a first is still walking"
    )


async def test_a_cancelled_job_can_still_be_requeued(client, db, temporal) -> None:
    """The remedy the 409 points at has to actually work.

    Cancel-then-requeue is the supported path for a run a tenant wants restarted, so the
    terminal check must admit ``CANCELLED`` as readily as ``FAILED`` — otherwise the gate
    would have replaced a correctness bug with a dead end.
    """
    await _seed_tenants()
    document_id = await _seed_document(_TENANT_A, size_bytes=1024, sha="a" * 64)
    cancelled = await _seed_job(
        _TENANT_A,
        document_id=document_id,
        status=JobStatus.CANCELLED,
        workflow_id="wf-cancelled",
    )

    res = await client.post(
        f"/jobs/{cancelled}/requeue",
        headers=_headers(tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A),
    )

    assert res.status_code == 200, res.text
    assert len(temporal.started) == 1


# ── (b) A job over budget never starts a workflow ────────────────────────────


async def test_a_job_over_budget_starts_no_workflow_at_all(client, db, temporal) -> None:
    """The pre-authorisation is *pre*: the orchestrator records zero starts.

    The document is sized so the derived estimate cannot fit what is left of the tenant's
    cap, and the spend is a real ``usage_ledger`` row — the same table the gateway
    enforces against, so the two cannot disagree about what "already spent" means.
    """
    await _seed_tenants()
    # Big enough that the estimate alone exceeds the whole cap, before any spend.
    size_bytes = int(((_USD_CAP / _PER_MB) + 10) * 1024 * 1024)
    document_id = await _seed_document(_TENANT_A, size_bytes=size_bytes, sha="a" * 64)
    failed = await _seed_job(
        _TENANT_A,
        document_id=document_id,
        status=JobStatus.FAILED,
        workflow_id="wf-failed",
    )
    async with get_sessionmaker()() as session:
        session.add(UsageLedger(tenant_id=_TENANT_A, model="fake", cost_usd=1.0))
        await session.commit()

    res = await client.post(
        f"/jobs/{failed}/requeue",
        headers=_headers(tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A),
    )

    assert res.status_code == 429
    assert res.headers["X-Admission-Gate"] == "budget"
    assert "cap" in res.json()["detail"]
    assert temporal.started == [], "a workflow was started for a job that cannot be paid for"


async def test_a_tenant_admins_budget_row_binds_the_pre_authorisation(
    client, db, temporal
) -> None:
    """A $0.01 cap set on the budgets screen refuses the ingest, and starts nothing."""
    await _seed_tenants()
    document_id = await _seed_document(_TENANT_A, size_bytes=8 * 1024 * 1024, sha="a" * 64)
    failed = await _seed_job(
        _TENANT_A,
        document_id=document_id,
        status=JobStatus.FAILED,
        workflow_id="wf-failed",
    )
    async with get_sessionmaker()() as session:
        session.add(
            Budget(
                tenant_id=_TENANT_A,
                scope_type=BudgetScope.TENANT,
                scope_id=_TENANT_A,
                window=BudgetWindow.DAY,
                usd_cap=0.01,
            )
        )
        await session.commit()

    res = await client.post(
        f"/jobs/{failed}/requeue",
        headers=_headers(tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A),
    )

    assert res.status_code == 429
    assert temporal.started == []


# ── (c) A tenant cannot cancel another tenant's job ──────────────────────────


async def test_a_tenant_cannot_cancel_another_tenants_job(client, db, temporal) -> None:
    """403, the neighbour's workflow untouched, and its row still running."""
    await _seed_tenants()
    document_id = await _seed_document(_TENANT_B, size_bytes=1024, sha="b" * 64)
    theirs = await _seed_job(
        _TENANT_B,
        document_id=document_id,
        status=JobStatus.RUNNING,
        workflow_id="wf-tenant-b",
    )

    res = await client.post(
        f"/jobs/{theirs}/cancel",
        headers=_headers(tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A),
    )

    assert res.status_code == 403
    assert temporal.cancelled == [], "tenant B's workflow was cancelled by tenant A"
    listed = await client.get(
        "/jobs", headers=_headers(tenant_id=_TENANT_B, username="b-admin", user_id=_USER_B)
    )
    assert [row["status"] for row in listed.json()["rows"]] == ["running"]


async def test_a_tenant_cannot_requeue_another_tenants_job(client, db, temporal) -> None:
    """The same guard on the start path — a cross-tenant re-queue starts nothing."""
    await _seed_tenants()
    document_id = await _seed_document(_TENANT_B, size_bytes=1024, sha="b" * 64)
    theirs = await _seed_job(
        _TENANT_B,
        document_id=document_id,
        status=JobStatus.FAILED,
        workflow_id="wf-tenant-b",
    )

    res = await client.post(
        f"/jobs/{theirs}/requeue",
        headers=_headers(tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A),
    )

    assert res.status_code == 403
    assert temporal.started == []


async def test_a_tenant_cancels_its_own_job_and_the_row_records_who(
    client, db, temporal
) -> None:
    """The owning tenant's cancel reaches the orchestrator and lands on the row."""
    await _seed_tenants()
    document_id = await _seed_document(_TENANT_A, size_bytes=1024, sha="a" * 64)
    mine = await _seed_job(
        _TENANT_A,
        document_id=document_id,
        status=JobStatus.RUNNING,
        workflow_id="wf-tenant-a",
    )

    res = await client.post(
        f"/jobs/{mine}/cancel",
        headers=_headers(tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A),
    )

    assert res.status_code == 200, res.text
    assert temporal.cancelled == ["wf-tenant-a"]
    job = res.json()["job"]
    assert job["status"] == "cancelled"
    assert job["cancelled_by"] == "a-admin"
    assert job["finished_at"] is not None


async def test_cancelling_a_finished_job_is_a_409(client, db, temporal) -> None:
    """A terminal row is refused rather than overwritten with a cancellation."""
    await _seed_tenants()
    document_id = await _seed_document(_TENANT_A, size_bytes=1024, sha="a" * 64)
    done = await _seed_job(
        _TENANT_A,
        document_id=document_id,
        status=JobStatus.SUCCEEDED,
        workflow_id="wf-done",
    )

    res = await client.post(
        f"/jobs/{done}/cancel",
        headers=_headers(tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A),
    )

    assert res.status_code == 409
    assert temporal.cancelled == []


# ── The list surface ─────────────────────────────────────────────────────────


async def test_the_jobs_list_is_tenant_scoped(client, db, temporal) -> None:
    """Each tenant sees only its own rows — the read the console renders."""
    await _seed_tenants()
    doc_a = await _seed_document(_TENANT_A, size_bytes=1024, sha="a" * 64)
    doc_b = await _seed_document(_TENANT_B, size_bytes=1024, sha="b" * 64)
    await _seed_job(
        _TENANT_A, document_id=doc_a, status=JobStatus.RUNNING, workflow_id="wf-a"
    )
    await _seed_job(
        _TENANT_B, document_id=doc_b, status=JobStatus.RUNNING, workflow_id="wf-b"
    )

    a = await client.get(
        "/jobs", headers=_headers(tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A)
    )
    b = await client.get(
        "/jobs", headers=_headers(tenant_id=_TENANT_B, username="b-admin", user_id=_USER_B)
    )

    assert [row["workflow_id"] for row in a.json()["rows"]] == ["wf-a"]
    assert [row["workflow_id"] for row in b.json()["rows"]] == ["wf-b"]
