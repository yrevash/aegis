"""Do the ingest handlers' RLS-only reads actually hold across a tenant boundary?

``app.ingestion.stages._document`` carries no ``WHERE tenant_id``. Its docstring says
"the scope is on the connection, so the ``tenant_isolation`` policy is what hides another
tenant's row — which makes this read part of the proof". No test in the suite runs a
stage handler for one tenant against another tenant's document, so that claim is
untested. This runs it.
"""

from __future__ import annotations

import pgsupport
import pytest
from aegis.governance.models import Budget, BudgetScope, BudgetWindow
from aegis.jobs import Document
from temporalio.exceptions import ApplicationError

from app.api.schemas import Role
from app.data import Tenant, User, get_sessionmaker
from app.jobs.activities import run_stage
from app.jobs.flows.contracts import StageInput

pytestmark = pytest.mark.asyncio

_TENANT_A = 7701
_TENANT_B = 7702


async def _seed() -> int:
    """Two tenants; tenant A owns one document. Return its id."""
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Tenant(id=_TENANT_A, name="Audit stage A"),
            Tenant(id=_TENANT_B, name="Audit stage B"),
            User(id=77011, username="stage-a", role=Role.ADMIN, tenant_id=_TENANT_A),
            User(id=77022, username="stage-b", role=Role.ADMIN, tenant_id=_TENANT_B),
            Budget(
                tenant_id=_TENANT_B,
                scope_type=BudgetScope.TENANT,
                scope_id=_TENANT_B,
                window=BudgetWindow.DAY,
                usd_cap=100.0,
            ),
        )
        await session.commit()
    async with get_sessionmaker()() as session:
        document = Document(
            tenant_id=_TENANT_A,
            filename="audit-stage-a.pdf",
            content_sha256=f"{_TENANT_A:064d}",
            mime_type="application/pdf",
            size_bytes=1024,
            status="RUNNING",
            completed_stage="parse",
        )
        session.add(document)
        await session.commit()
        return document.id


async def test_a_stage_run_for_one_tenant_cannot_touch_another_tenants_document(
    client, db, wired, store, temporal
) -> None:
    """Tenant B's ingest activity, pointed at tenant A's document id."""
    document_id = await _seed()

    with pytest.raises(ApplicationError) as excinfo:
        await run_stage(
            StageInput(
                tenant_id=_TENANT_B,
                workflow_id=f"ingest:{_TENANT_B}:{document_id}",
                document_id=document_id,
                stage="chunk",
            )
        )

    assert "not visible" in str(excinfo.value), str(excinfo.value)


async def test_a_reindex_for_one_tenant_does_not_rebuild_another_tenants_corpus(
    client, db, wired, store, temporal
) -> None:
    """``_reindexable_documents`` carries no ``WHERE tenant_id`` either.

    Its docstring says the policy on the connection "is part of the proof that the
    policy works". Nothing exercised that claim before this test: a re-index run for
    tenant B must find none of tenant A's SUCCEEDED documents.
    """
    from app.data import set_tenant_scope
    from app.ingestion.reindex import _reindexable_documents

    document_id = await _seed()
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, _TENANT_A)
        await session.execute(
            Document.__table__.update()
            .where(Document.id == document_id)
            .values(status="SUCCEEDED")
        )
        await session.commit()

    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, _TENANT_A)
        mine = await _reindexable_documents(session)
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, _TENANT_B)
        theirs = await _reindexable_documents(session)

    assert mine == [document_id], f"the owner cannot see its own document: {mine}"
    assert theirs == [], (
        f"a re-index for tenant {_TENANT_B} would rebuild tenant {_TENANT_A}'s "
        f"documents: {theirs}"
    )
