"""The job substrate's record layer, proved against a real PostgreSQL under real RLS.

Four guarantees are checked here, and each one is checked in the only way that can fail:

1. **Both tables are governed.** Not "the name is in the registry tuple" — the
   ``tenant_isolation`` policy is read back out of ``pg_policies`` on the database the
   fixtures actually built, together with ``relrowsecurity``/``relforcerowsecurity``, since
   ENABLE without FORCE leaves the owner exempt and a registry entry with no policy behind
   it is exactly the silent gap :mod:`aegis.governance.rls` exists to catch.
2. **A tenant's job row round-trips over the unprivileged role**, with its enum, its
   ``jsonb`` payload and its server-side timestamp intact, and a second tenant's row bound
   to a different scope is invisible — with an owner-side count proving both rows are
   really there, so "sees one row" cannot be "the insert quietly failed".
3. **``RECONCILING`` exists in the database**, not merely in Python. The reconciler's whole
   purpose is to move a stranded row out of ``RUNNING``; if the label were dropped from the
   Postgres type that write would fail at the moment it is most needed.
4. **Identical bytes cannot be ingested twice for one tenant, and can be for two.** Both
   halves, because a constraint that is merely global would pass the first and silently
   deduplicate one tenant's document against another's — the leak the tenant boundary
   exists to prevent.

Everything runs over the ``LOGIN NOSUPERUSER NOBYPASSRLS`` role from ``tests/conftest.py``;
the owner engine appears only for catalog reads and non-vacuity counts, never for an
isolation assertion.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from aegis.data import AegisBase
from aegis.governance.rls import (
    _POLICY_NAME,
    _TENANT_COLUMN,
    _TENANT_SCOPED_TABLES,
    set_tenant_scope,
)
from aegis.jobs import Document, JobRun, JobStatus

from .._seed import ensure_tenants

#: The two tenants every assertion below is written against. Far outside the ranges the
#: rest of the suite seeds, so a stray row cannot be mistaken for one of these.
_TENANT_A = 70101
_TENANT_B = 70202

#: One document's bytes, uploaded by both tenants in the constraint tests.
_SHA = "b" * 64


def _jobs_tables() -> list:
    """Return every table :mod:`aegis.jobs.models` maps, read from the ORM registry.

    Derived rather than hand-listed so a third job table added later is swept by the
    registration test below instead of quietly escaping it.

    Returns:
        The mapped :class:`sqlalchemy.Table` objects declared by ``aegis.jobs.models``.
    """
    return [
        mapper.local_table
        for mapper in AegisBase.registry.mappers
        if mapper.class_.__module__ == "aegis.jobs.models"
    ]


def _document(tenant_id: int, *, sha: str = _SHA, filename: str = "filing.pdf") -> Document:
    """Build one uploaded-but-not-yet-parsed document row for ``tenant_id``.

    Args:
        tenant_id: The owning tenant.
        sha: The content digest — the idempotency anchor under test.
        filename: The upload's name.

    Returns:
        An unpersisted :class:`~aegis.jobs.Document`.
    """
    return Document(
        tenant_id=tenant_id,
        filename=filename,
        content_sha256=sha,
        mime_type="application/pdf",
        size_bytes=1024,
        status=JobStatus.PENDING,
    )


async def _write(sessionmaker, tenant_id: int, row) -> None:  # noqa: ANN001
    """Commit one row over the unprivileged role with ``tenant_id`` bound.

    The bind and the INSERT share a transaction deliberately: ``set_tenant_scope`` uses
    ``set_config(..., is_local => true)``, so a scope that outlived its transaction would
    mean the policy was not the thing admitting the write.

    Args:
        sessionmaker: The unprivileged ``async_sessionmaker``.
        tenant_id: The scope to bind.
        row: The ORM instance to persist.
    """
    async with sessionmaker() as session:
        await set_tenant_scope(session, tenant_id)
        session.add(row)
        await session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# 1. Registration and the policy the database actually carries
# ─────────────────────────────────────────────────────────────────────────────


def test_every_table_this_module_maps_is_tenant_scoped_and_registered():
    """An unregistered tenant table looks governed from outside and is not."""
    tables = _jobs_tables()
    assert {table.name for table in tables} == {"documents", "job_runs"}, (
        "aegis.jobs.models mapped a table this suite does not know about; if it carries "
        "tenant_id it also needs a line in aegis.governance.rls._TENANT_SCOPED_TABLES"
    )
    for table in tables:
        assert _TENANT_COLUMN in table.c, f"{table.name} carries no {_TENANT_COLUMN}"
        assert table.name in _TENANT_SCOPED_TABLES, (
            f"{table.name} has a {_TENANT_COLUMN} column but is not in "
            "_TENANT_SCOPED_TABLES, so bootstrap_rls installs no policy on it"
        )


async def test_bootstrap_installed_the_tenant_policy_on_both_job_tables(pg_owner_engine):
    """Read the policy back out of the catalog, not out of the DDL we hoped was emitted."""
    async with pg_owner_engine.connect() as conn:
        policies = {
            name: qual
            for name, qual in (
                await conn.execute(
                    text(
                        "SELECT tablename, qual FROM pg_policies "
                        "WHERE schemaname = 'public' AND policyname = :policy "
                        "AND tablename = ANY(:names)"
                    ),
                    {"policy": _POLICY_NAME, "names": ["documents", "job_runs"]},
                )
            ).all()
        }
        forced = {
            name: (enabled, force)
            for name, enabled, force in (
                await conn.execute(
                    text(
                        "SELECT relname, relrowsecurity, relforcerowsecurity "
                        "FROM pg_class WHERE relname = ANY(:names)"
                    ),
                    {"names": ["documents", "job_runs"]},
                )
            ).all()
        }

    missing = {"documents", "job_runs"} - set(policies)
    assert not missing, f"no {_POLICY_NAME} policy on {sorted(missing)} in the live catalog"
    for table, qual in policies.items():
        assert _TENANT_COLUMN in qual, f"{table}'s policy does not filter on {_TENANT_COLUMN}"
        assert "app.tenant_id" in qual, f"{table}'s policy reads no bound scope: {qual}"
    # ENABLE alone leaves the table owner exempt; FORCE is what makes the policy real.
    assert forced == {"documents": (True, True), "job_runs": (True, True)}


# ─────────────────────────────────────────────────────────────────────────────
# 2. A job row, written and read by the unprivileged role under a bound scope
# ─────────────────────────────────────────────────────────────────────────────


async def test_a_job_run_round_trips_under_a_bound_tenant_scope(pg_sessionmaker):
    """Every column shape the substrate depends on survives a real INSERT/SELECT."""
    await ensure_tenants(pg_sessionmaker, _TENANT_A)
    before = datetime.now(UTC)
    await _write(
        pg_sessionmaker,
        _TENANT_A,
        JobRun(
            tenant_id=_TENANT_A,
            job_type="ingest",
            workflow_id=f"ingest:{_TENANT_A}:doc-1",
            status=JobStatus.RUNNING,
            completed_stage="chunk",
            payload={"document_id": 7, "stages": ["parse", "chunk", "embed"]},
            cost_usd=0.25,
        ),
    )

    async with pg_sessionmaker() as session:
        await set_tenant_scope(session, _TENANT_A)
        row = (await session.execute(select(JobRun))).scalars().one()

        assert row.status is JobStatus.RUNNING
        assert row.completed_stage == "chunk"
        assert row.payload == {"document_id": 7, "stages": ["parse", "chunk", "embed"]}
        # Defaulted, not NULL: the reader of a fresh row gets an empty result document
        # rather than having to distinguish "no result yet" from "no result column".
        assert row.result == {}
        assert row.cost_usd == pytest.approx(0.25)
        assert row.error is None
        assert row.started_at is None and row.finished_at is None
        assert row.cancelled_by is None
        # The server clock filled created_at, and it is an aware UTC instant — a naive
        # one would raise on the very arithmetic the reaper does.
        assert row.created_at.tzinfo is not None
        assert before <= row.created_at <= datetime.now(UTC)


async def test_a_bound_scope_sees_only_its_own_job_run(pg_sessionmaker, pg_owner_engine):
    """Two tenants, one table: each scope reads exactly its own row and no other."""
    await ensure_tenants(pg_sessionmaker, _TENANT_A, _TENANT_B)
    for tenant in (_TENANT_A, _TENANT_B):
        await _write(
            pg_sessionmaker,
            tenant,
            JobRun(
                tenant_id=tenant,
                job_type="ingest",
                workflow_id=f"ingest:{tenant}:doc-1",
                status=JobStatus.PENDING,
            ),
        )

    # Non-vacuity, read as the owner (who bypasses RLS): both rows really are there, so a
    # scoped read of one row cannot be an insert that silently did nothing.
    async with pg_owner_engine.connect() as conn:
        total = (await conn.execute(select(func.count()).select_from(JobRun))).scalar_one()
    assert total == 2

    for tenant in (_TENANT_A, _TENANT_B):
        async with pg_sessionmaker() as session:
            await set_tenant_scope(session, tenant)
            visible = (await session.execute(select(JobRun.workflow_id))).scalars().all()
        assert visible == [f"ingest:{tenant}:doc-1"]


async def test_a_stranded_run_can_be_moved_to_reconciling(pg_sessionmaker):
    """RECONCILING must exist in the Postgres type, not only in the Python enum.

    It is written by the reconciler at the one moment the platform is already in trouble —
    a row saying RUNNING with no live execution behind it — so a label missing from the
    database would fail exactly then.
    """
    await ensure_tenants(pg_sessionmaker, _TENANT_A)
    workflow_id = f"ingest:{_TENANT_A}:stranded"
    await _write(
        pg_sessionmaker,
        _TENANT_A,
        JobRun(
            tenant_id=_TENANT_A,
            job_type="ingest",
            workflow_id=workflow_id,
            status=JobStatus.RUNNING,
        ),
    )

    async with pg_sessionmaker() as session:
        await set_tenant_scope(session, _TENANT_A)
        row = (await session.execute(select(JobRun))).scalars().one()
        row.status = JobStatus.RECONCILING
        row.error = "no workflow execution found for this run"
        await session.commit()

    async with pg_sessionmaker() as session:
        await set_tenant_scope(session, _TENANT_A)
        row = (await session.execute(select(JobRun))).scalars().one()
    assert row.status is JobStatus.RECONCILING
    assert row.workflow_id == workflow_id


# ─────────────────────────────────────────────────────────────────────────────
# 3. The idempotency anchor — both halves of it
# ─────────────────────────────────────────────────────────────────────────────


async def test_identical_bytes_cannot_be_ingested_twice_for_one_tenant(
    pg_sessionmaker, pg_owner_engine
):
    """The second upload of the same bytes is refused by the database, by name."""
    await ensure_tenants(pg_sessionmaker, _TENANT_A)
    await _write(pg_sessionmaker, _TENANT_A, _document(_TENANT_A))

    with pytest.raises(IntegrityError) as raised:
        # A different filename, the same bytes: the anchor is the content digest, not the
        # name the browser happened to send.
        await _write(pg_sessionmaker, _TENANT_A, _document(_TENANT_A, filename="copy.pdf"))
    assert "uq_documents_tenant_sha" in str(raised.value), (
        "a different constraint rejected the duplicate, so this test would keep passing "
        f"with the per-tenant digest constraint removed: {raised.value}"
    )

    # And the refusal left the original intact rather than replacing it.
    async with pg_owner_engine.connect() as conn:
        names = (await conn.execute(select(Document.filename))).scalars().all()
    assert names == ["filing.pdf"]


async def test_identical_bytes_are_permitted_for_a_different_tenant(
    pg_sessionmaker, pg_owner_engine
):
    """Two tenants uploading the same public filing are two independent documents."""
    await ensure_tenants(pg_sessionmaker, _TENANT_A, _TENANT_B)
    await _write(pg_sessionmaker, _TENANT_A, _document(_TENANT_A))
    # Would raise IntegrityError if the constraint were on content_sha256 alone.
    await _write(pg_sessionmaker, _TENANT_B, _document(_TENANT_B))

    async with pg_owner_engine.connect() as conn:
        rows = (
            await conn.execute(select(Document.tenant_id, Document.content_sha256))
        ).all()
    assert sorted(rows) == [(_TENANT_A, _SHA), (_TENANT_B, _SHA)]

    # …and neither tenant can see the other's copy of the same bytes.
    for tenant in (_TENANT_A, _TENANT_B):
        async with pg_sessionmaker() as session:
            await set_tenant_scope(session, tenant)
            owners = (await session.execute(select(Document.tenant_id))).scalars().all()
        assert owners == [tenant]
