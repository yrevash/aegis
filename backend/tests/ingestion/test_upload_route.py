"""``POST /documents``: one row per distinct document, one ingest, and never a second.

The upload is the front door to everything the ingestion phase builds, and three of its
promises are the kind that pass by accident if they are asserted loosely:

* **Idempotent on the bytes.** ``uq_documents_tenant_sha`` makes a duplicate row
  impossible; what this file asserts is the *expensive* half — that a re-upload also
  starts no second workflow. A parse is CPU-bound at roughly a second a page on a
  single-slot queue and the embed stage is billed, so a duplicate that merely failed to
  insert a row while still queueing the work would cost exactly what the constraint was
  supposed to save.
* **Admission runs before anything starts.** A tenant over budget must leave the
  orchestrator with *zero* ``start_workflow`` calls, which is only visible by counting
  them: a test that asserted the 429 alone would pass on an implementation that started
  the workflow and refused afterwards.
* **A document belongs to one tenant.** Asserted over the scratch cluster's
  ``NOSUPERUSER NOBYPASSRLS`` role, so the ``tenant_isolation`` policy is genuinely
  enforced against the connection doing the reading rather than being a ``WHERE`` clause
  the test could have written itself.

The Temporal client is a recording double — the shipped
:func:`app.jobs.client.set_temporal_client` seam, installed by the ``temporal`` fixture in
``conftest.py``. Everything else is real: real rows in a real PostgreSQL, the real ASGI
app, the real document store on a temporary directory.
"""

from __future__ import annotations

import pgsupport
import pytest
from aegis.governance.models import Budget, BudgetScope, BudgetWindow, UsageLedger
from aegis.jobs import Document, JobRun, JobStatus
from aegis.settings.spec import spec_for
from sqlalchemy import select

from app.api.schemas import Role
from app.core.security import TENANT_ADMIN, create_access_token
from app.data import Tenant, User, get_sessionmaker, set_tenant_scope
from app.ingestion.store import sha256_of

pytestmark = pytest.mark.asyncio

_TENANT_A = 1
_TENANT_B = 2
_USER_A = 11
_USER_B = 22

#: The tenant's cap lives on a real ``budgets`` row and nowhere else.
_USD_CAP = 100.0
_PER_MB: float = spec_for("jobs.estimated_cost_usd.ingest_per_mb").default

#: A tiny but structurally honest PDF: the magic number the upload sniffs, then enough
#: bytes to be a distinct document. Nothing here parses it — these tests are about the
#: upload path, and the real fixture PDFs are parsed in ``test_stage_handlers.py``.
_PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"
_OTHER_PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog /Note (other) >>\nendobj\n%%EOF\n"


def _headers(*, tenant_id: int, username: str, user_id: int) -> dict[str, str]:
    """A tenant-admin bearer for one tenant."""
    token = create_access_token(
        user_id=user_id, username=username, role=TENANT_ADMIN, tenant_id=tenant_id
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_tenants(*, cap_usd: float = _USD_CAP) -> None:
    """Two tenants with one admin each, and a real budget row for tenant A."""
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
                usd_cap=cap_usd,
            ),
        )
        await session.commit()


def _upload(client, *, tenant_id: int, username: str, user_id: int, data: bytes, **form):
    """Issue one multipart upload as a tenant admin."""
    return client.post(
        "/documents",
        files={"file": ("filing.pdf", data, "application/pdf")},
        data=form,
        headers=_headers(tenant_id=tenant_id, username=username, user_id=user_id),
    )


async def _documents(tenant_id: int | None) -> list[Document]:
    """Read the documents visible to ``tenant_id`` over the **serving** role.

    The scope is bound and no ``WHERE tenant_id`` is written, so what comes back is what
    the ``tenant_isolation`` policy allows this connection to see — which is the claim
    being tested, rather than the claim a hand-written filter would make.
    """
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, tenant_id)
        return list(
            (await session.execute(select(Document).order_by(Document.id))).scalars().all()
        )


# ── The document is created, stored, and exactly one ingest starts ───────────


async def test_an_upload_creates_a_document_stores_the_bytes_and_starts_one_ingest(
    client, db, temporal, store
) -> None:
    await _seed_tenants()

    res = await _upload(
        client,
        tenant_id=_TENANT_A,
        username="a-admin",
        user_id=_USER_A,
        data=_PDF,
        doc_type="policy",
        doc_date="2019-04-01",
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["created"] is True
    assert body["content_sha256"] == sha256_of(_PDF)
    assert body["size_bytes"] == len(_PDF)
    assert body["doc_type"] == "policy"
    # Never the upload date: the document is from 2019 and says so.
    assert body["doc_date"] == "2019-04-01"
    assert body["title"] is None, "a title before the parse would be a guess"

    rows = await _documents(_TENANT_A)
    assert len(rows) == 1
    assert rows[0].tenant_id == _TENANT_A
    assert rows[0].status is JobStatus.PENDING
    assert rows[0].workflow_id == f"ingest:{_TENANT_A}:{rows[0].id}"
    assert store.read(tenant_id=_TENANT_A, sha256=body["content_sha256"]) == _PDF
    assert temporal.started == [f"ingest:{_TENANT_A}:{rows[0].id}"]


# ── Re-uploading the same bytes: no second row, and no second ingest ─────────


async def test_re_uploading_identical_bytes_starts_no_second_ingest(
    client, db, temporal, store
) -> None:
    """The idempotency that matters is the one that stops the *work*, not the row.

    A parse is minutes of a single-slot queue and the embed stage is billed, so a second
    workflow over bytes already ingested is real money spent twice. The row constraint
    alone would not have prevented it.
    """
    await _seed_tenants()

    first = await _upload(
        client, tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A, data=_PDF
    )
    second = await _upload(
        client, tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A, data=_PDF
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert second.json()["document_id"] == first.json()["document_id"]
    assert "no second ingest" in second.json()["detail"]

    assert len(await _documents(_TENANT_A)) == 1
    assert len(temporal.started) == 1, (
        "the duplicate upload started a second ingest: the same document would be "
        "parsed and embedded twice"
    )


async def test_two_tenants_uploading_the_same_bytes_get_their_own_document(
    client, db, temporal, store
) -> None:
    """The positive control for the test above, and the reason dedup is *per tenant*.

    Two tenants uploading the same public filing are two documents with independent
    lifecycles. Deduplicating across the boundary would leak the existence of one
    tenant's data into the other's.
    """
    await _seed_tenants()

    a = await _upload(
        client, tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A, data=_PDF
    )
    b = await _upload(
        client, tenant_id=_TENANT_B, username="b-admin", user_id=_USER_B, data=_PDF
    )

    assert a.json()["created"] is True
    assert b.json()["created"] is True
    assert a.json()["document_id"] != b.json()["document_id"]
    assert len(temporal.started) == 2


# ── Over budget: a visible 429, and zero workflows started ───────────────────


async def test_an_upload_over_budget_starts_no_workflow_at_all(
    client, db, temporal, store
) -> None:
    """The pre-authorisation is *pre*: the orchestrator records nothing.

    The cap is a real ``budgets`` row and the spend a real ``usage_ledger`` row — the same
    tables the gateway enforces against, so the upload gate and the model gateway cannot
    disagree about what this tenant has left.
    """
    await _seed_tenants(cap_usd=0.01)
    async with get_sessionmaker()() as session:
        session.add(UsageLedger(tenant_id=_TENANT_A, model="fake", cost_usd=0.01))
        await session.commit()

    res = await _upload(
        client, tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A, data=_PDF
    )

    assert res.status_code == 429
    assert res.headers["X-Admission-Gate"] == "budget"
    assert "cap" in res.json()["detail"]
    assert temporal.started == [], "an ingest was started for a tenant that cannot pay"
    assert await _documents(_TENANT_A) == [], (
        "a refused upload left a document row behind, which a reconciler would never "
        "close because no job run was ever created for it"
    )


async def test_the_inflight_cap_refuses_the_next_upload_and_starts_nothing(
    client, db, temporal, store
) -> None:
    """The other gate, and the other reason: one tenant may not hold every worker slot."""
    await _seed_tenants()
    cap: int = spec_for("jobs.max_inflight.ingest").default
    from aegis.jobs import JobRun

    async with get_sessionmaker()() as session:
        for index in range(cap):
            session.add(
                JobRun(
                    tenant_id=_TENANT_A,
                    job_type="ingest",
                    workflow_id=f"wf-live-{index}",
                    status=JobStatus.RUNNING,
                    payload={},
                )
            )
        await session.commit()

    res = await _upload(
        client, tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A, data=_PDF
    )

    assert res.status_code == 429
    assert res.headers["X-Admission-Gate"] == "concurrency"
    assert "jobs.max_inflight.ingest" in res.json()["detail"]
    assert temporal.started == []


# ── One tenant's document is invisible to another ────────────────────────────


async def test_one_tenants_document_is_invisible_to_another_tenant(
    client, db, temporal, store
) -> None:
    """Read back over the unprivileged role: the policy hides the row, not a filter.

    Both tenants upload, and each is then read through a session bound to its own scope
    with no ``WHERE tenant_id`` anywhere. On the scratch cluster's ``NOSUPERUSER
    NOBYPASSRLS`` role that read is governed by the ``tenant_isolation`` policy — which is
    the only version of this assertion that would fail if the policy were dropped.
    """
    await _seed_tenants()
    a = await _upload(
        client, tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A, data=_PDF
    )
    b = await _upload(
        client,
        tenant_id=_TENANT_B,
        username="b-admin",
        user_id=_USER_B,
        data=_OTHER_PDF,
    )
    assert a.status_code == 200 and b.status_code == 200

    seen_by_a = await _documents(_TENANT_A)
    seen_by_b = await _documents(_TENANT_B)

    assert [row.id for row in seen_by_a] == [a.json()["document_id"]]
    assert [row.id for row in seen_by_b] == [b.json()["document_id"]]
    assert all(row.tenant_id == _TENANT_A for row in seen_by_a)
    assert all(row.tenant_id == _TENANT_B for row in seen_by_b)


# ── The two refusals at the door ─────────────────────────────────────────────


async def test_bytes_that_are_not_a_pdf_are_refused_before_anything_is_stored(
    client, db, temporal, store
) -> None:
    """A declared content type is a claim; the magic number is the fact.

    Refusing here rather than at the parse matters because the parse happens minutes
    later on the queue every other tenant's document is waiting behind.
    """
    await _seed_tenants()

    res = await _upload(
        client,
        tenant_id=_TENANT_A,
        username="a-admin",
        user_id=_USER_A,
        data=b"PK\x03\x04 this is a zip pretending to be a pdf",
    )

    assert res.status_code == 415
    assert "%PDF-" in res.json()["detail"]
    assert await _documents(_TENANT_A) == []
    assert temporal.started == []


async def test_a_malformed_document_date_is_refused_rather_than_guessed(
    client, db, temporal, store
) -> None:
    """The date is embedded into every chunk of the document, so it is not interpreted."""
    await _seed_tenants()

    res = await _upload(
        client,
        tenant_id=_TENANT_A,
        username="a-admin",
        user_id=_USER_A,
        data=_PDF,
        doc_date="last April",
    )

    assert res.status_code == 400
    assert "ISO date" in res.json()["detail"]
    assert temporal.started == []


# ── The way back for a document whose ingest never started (audit C, C3) ─────


class _RefusingTemporalClient:
    """A Temporal that is not there — the state a cold demo box is actually in."""

    def __init__(self) -> None:
        self.started: list[str] = []

    async def start_workflow(self, *args: object, **kwargs: object) -> object:
        """Refuse every start, the way an unreachable orchestrator does."""
        raise RuntimeError("the durable-job orchestrator (Temporal) is not reachable")


async def test_a_document_whose_ingest_never_started_can_be_started_by_re_uploading(
    client, db, temporal, store
) -> None:
    """One flaky moment at upload time must not kill the file permanently.

    With Temporal down, ``POST /documents`` stores the bytes, fails to start the workflow
    and returns 503. Afterwards the document was unreachable by every route the platform
    has: the ``(tenant_id, content_sha256)`` dedup refused the identical bytes,
    ``GET /jobs`` showed no row (no execution ever claimed it), and
    ``POST /jobs/{id}/requeue`` therefore had nothing to act on. FAILED forever, short of
    editing the database.
    """
    from app.jobs.client import set_temporal_client

    await _seed_tenants()

    # The orchestrator is down at the moment of upload.
    set_temporal_client(_RefusingTemporalClient())  # type: ignore[arg-type]
    refused = await _upload(
        client, tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A, data=_PDF
    )
    assert refused.status_code == 503, refused.text
    assert "not reachable" in refused.json()["detail"]

    rows = await _documents(_TENANT_A)
    assert len(rows) == 1
    assert rows[0].status is JobStatus.FAILED
    document_id = rows[0].id

    # Temporal comes back. The tenant re-sends the same file — the only remedy a person
    # holding the document can reach without being told about a new endpoint.
    set_temporal_client(temporal)  # type: ignore[arg-type]
    again = await _upload(
        client, tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A, data=_PDF
    )

    assert again.status_code == 200, again.text
    body = again.json()
    assert body["document_id"] == document_id, "no second row: this is the same document"
    assert body["created"] is False
    assert body["restarted"] is True
    assert body["status"] == "pending"
    assert temporal.started == [f"ingest:{_TENANT_A}:{document_id}"], (
        "the stuck document's ingest must actually have been started"
    )

    rows = await _documents(_TENANT_A)
    assert len(rows) == 1
    assert rows[0].status is JobStatus.PENDING
    assert rows[0].error is None, "the stale failure reason must not outlive the restart"


async def test_the_restart_is_idempotent_and_forks_no_second_execution(
    client, db, temporal, store
) -> None:
    """A second re-upload while the restarted ingest is live must start nothing.

    The guard is durable state — FAILED *and* no ``job_runs`` row — and starting the
    execution is what stops it holding. Reusing the same workflow id rather than minting a
    fresh nonce is the second lock: the orchestrator refuses a duplicate rather than
    letting two workflows walk one document.
    """
    from app.jobs.client import set_temporal_client

    await _seed_tenants()

    set_temporal_client(_RefusingTemporalClient())  # type: ignore[arg-type]
    await _upload(
        client, tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A, data=_PDF
    )

    set_temporal_client(temporal)  # type: ignore[arg-type]
    first = await _upload(
        client, tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A, data=_PDF
    )
    second = await _upload(
        client, tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A, data=_PDF
    )

    assert first.json()["restarted"] is True
    assert second.json()["restarted"] is False, "the second call must be an ordinary dedup"
    assert "no second ingest" in second.json()["detail"]
    assert len(temporal.started) == 1, "a second execution was forked over one document"
    assert len(await _documents(_TENANT_A)) == 1


async def test_a_healthy_document_is_never_re_ingested_by_a_duplicate_upload(
    client, db, temporal, store
) -> None:
    """The escape hatch must not become a way to re-parse a working document.

    Parsing is CPU-bound at a second a page and embedding is billed. The guard is
    deliberately narrow — only a FAILED document that owns no job run at all — and this
    is the assertion that keeps it narrow.
    """
    await _seed_tenants()

    await _upload(
        client, tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A, data=_PDF
    )
    rows = await _documents(_TENANT_A)
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, _TENANT_A)
        document = (
            await session.execute(select(Document).where(Document.id == rows[0].id))
        ).scalar_one()
        # A stage died: FAILED, but with a job run behind it, so ``/jobs`` shows it and
        # ``POST /jobs/{id}/requeue`` is the right remedy.
        document.status = JobStatus.FAILED
        document.error = "the embed stage failed: RBAC: access denied"
        session.add(
            JobRun(
                tenant_id=_TENANT_A,
                job_type="ingest",
                workflow_id=document.workflow_id,
                status=JobStatus.FAILED,
                payload={"document_id": document.id},
            )
        )
        await session.commit()

    again = await _upload(
        client, tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A, data=_PDF
    )

    assert again.json()["restarted"] is False
    assert len(temporal.started) == 1, (
        "a failed *stage* is re-queued through /jobs, not by re-uploading the file"
    )


# ── GET /documents: the corpus, tenant-scoped (audit C, C7) ──────────────────


async def test_a_tenant_can_list_the_documents_it_has_ingested(
    client, db, temporal, store
) -> None:
    """"Show me what you have ingested" must have an endpoint behind it.

    The route table had ``POST /documents`` and ``GET /documents/{id}/ingest`` and
    nothing between them, so the only way to look at a document was to already know its
    id — which a jury asking the question does not.
    """
    await _seed_tenants()

    first = await _upload(
        client,
        tenant_id=_TENANT_A,
        username="a-admin",
        user_id=_USER_A,
        data=_PDF,
        doc_type="policy",
        doc_date="2019-04-01",
    )
    second = await _upload(
        client, tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A, data=_OTHER_PDF
    )

    listed = await client.get(
        "/documents",
        headers=_headers(tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A),
    )

    assert listed.status_code == 200, listed.text
    rows = listed.json()["rows"]
    assert {row["document_id"] for row in rows} == {
        first.json()["document_id"],
        second.json()["document_id"],
    }
    by_id = {row["document_id"]: row for row in rows}
    row = by_id[first.json()["document_id"]]
    assert row["filename"] == "filing.pdf"
    assert row["status"] == "pending"
    assert row["doc_type"] == "policy"
    assert row["doc_date"] == "2019-04-01"
    assert row["size_bytes"] == len(_PDF)
    # Not guessed before the parse runs, exactly as the upload response reports them.
    assert row["title"] is None
    assert row["page_count"] is None


async def test_the_corpus_listing_never_crosses_a_tenant_boundary(
    client, db, temporal, store
) -> None:
    """Tenant B must not see tenant A's documents, over the unprivileged role.

    Asserted against the scratch cluster's ``NOSUPERUSER NOBYPASSRLS`` connection, so
    ``tenant_isolation`` is genuinely enforced against the reader rather than being a
    ``WHERE`` clause the test wrote for itself.
    """
    await _seed_tenants()

    await _upload(
        client, tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A, data=_PDF
    )

    theirs = await client.get(
        "/documents",
        headers=_headers(tenant_id=_TENANT_B, username="b-admin", user_id=_USER_B),
    )

    assert theirs.status_code == 200
    assert theirs.json()["rows"] == [], "tenant B saw tenant A's corpus"


async def test_an_untenanted_principal_gets_an_empty_corpus_not_everyones(
    client, db, temporal, store
) -> None:
    """The `None`-conflation that caused five cross-tenant leaks must not come back.

    ``None if admin else auth.tenant_id`` reaches the platform admin's unrestricted value
    down the *unprivileged* branch for any principal whose ``users.tenant_id`` is NULL —
    the shape ``app.seed`` mints for the "client" platform principal. The sealed
    ``TenantScope`` separates the two, and this asserts the separation on the new route.
    """
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Tenant(id=_TENANT_A, name="Tenant A"),
            User(id=_USER_A, username="a-admin", role=Role.ADMIN, tenant_id=_TENANT_A),
            User(id=99, username="rogue", role=Role.CLIENT, tenant_id=None),
            Budget(
                tenant_id=_TENANT_A,
                scope_type=BudgetScope.TENANT,
                scope_id=_TENANT_A,
                window=BudgetWindow.DAY,
                usd_cap=_USD_CAP,
            ),
        )
        await session.commit()

    await _upload(
        client, tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A, data=_PDF
    )

    token = create_access_token(
        user_id=99, username="rogue", role="client", tenant_id=None
    )
    rogue = await client.get(
        "/documents", headers={"Authorization": f"Bearer {token}"}
    )

    assert rogue.status_code == 200
    assert rogue.json()["rows"] == [], (
        "a principal bound to no tenant read every tenant's corpus"
    )
