"""The ingest stages, on a real PDF: upload → parse → chunk → enrich → keyword hit.

This is the file that says the pipeline *runs*. Everything before task 4.5 built pieces —
a parser, a chunker, a job substrate, a lexical arm — and each was tested against its own
inputs. What was never asserted anywhere is the sentence a jury would ask for: **a
document a tenant uploaded can be found by searching for words that are in it.**

So nothing here is a fake. The PDF is one of the phase's real fixtures (the 15-page
single-column paper — the *control* fixture, chosen because its structure is not the
thing under test here). It is uploaded through the real HTTP route, parsed by real
Docling, chunked by the real chunker, written to a real PostgreSQL through the real
stage-runner activity, and then found by the real corpus-wide keyword arm over the
scratch cluster's ``NOSUPERUSER NOBYPASSRLS`` role — so the ``tenant_isolation`` policy
applies to the search that finds it and to the search that must not.

Three properties beyond "it works", each of which fails silently if it is not asserted:

* **The stage handler is idempotent.** Running ``chunk`` twice must leave exactly one set
  of chunks. The substrate's own guarantee covers replay; it does not cover an attempt
  that wrote its rows and was retried for an unrelated reason, which is why the handler's
  write is delete-then-insert rather than a bare insert.
* **The prefix reaches the indexed text.** D7's whole measured value (Context@5 33.3% →
  55.0%) comes from the prefix being *in the embedded text*, not beside it in metadata —
  the mistake LangChain's header splitter makes. Here that is checkable: the chunk's
  ``content`` is what PostgreSQL generates ``search_vector`` from.
* **A document's chunks are one tenant's.** Asserted by having the other tenant search
  for a phrase that is unambiguously in the first tenant's document.

The two large fixtures (67 and 126 pages) are minutes of parsing each and are not used
here; ``AEGIS_DOCLING_SLOW_FIXTURES=1`` in the ``aegis`` suite is where they live.
"""

from __future__ import annotations

import asyncio

import pgsupport
import pytest
from aegis.governance.models import Budget, BudgetScope, BudgetWindow
from aegis.jobs import Chunk, Document
from aegis.retrieval.lightrag_backend import LightRAGBackend
from aegis.retrieval.types import RetrievalScope
from sqlalchemy import func, select

from app.api.schemas import Role
from app.core.security import TENANT_ADMIN, create_access_token
from app.data import Tenant, User, get_sessionmaker, set_tenant_scope
from app.jobs.activities import run_stage
from app.jobs.flows.contracts import StageInput

from .conftest import FIXTURE, fixture_pdf

pytestmark = pytest.mark.asyncio

_TENANT_A = 1
_TENANT_B = 2
_USER_A = 11
_USER_B = 22

#: A phrase this paper certainly contains and this repository otherwise does not, so a
#: hit is unambiguous evidence that the uploaded document's own text was indexed.
_QUERY = "multi-head attention"


def _headers(*, tenant_id: int, username: str, user_id: int) -> dict[str, str]:
    """A tenant-admin bearer for one tenant."""
    token = create_access_token(
        user_id=user_id, username=username, role=TENANT_ADMIN, tenant_id=tenant_id
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_tenants() -> None:
    """Two tenants with an admin each, and a budget generous enough to admit an ingest."""
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
                usd_cap=100.0,
            ),
            Budget(
                tenant_id=_TENANT_B,
                scope_type=BudgetScope.TENANT,
                scope_id=_TENANT_B,
                window=BudgetWindow.DAY,
                usd_cap=100.0,
            ),
        )
        await session.commit()


async def _upload(client, data: bytes, *, tenant_id: int, username: str, user_id: int):
    """Upload one document through the real route and return the response body."""
    res = await client.post(
        "/documents",
        files={"file": (FIXTURE, data, "application/pdf")},
        data={"doc_type": "research paper", "doc_date": "2017-06-12"},
        headers=_headers(tenant_id=tenant_id, username=username, user_id=user_id),
    )
    assert res.status_code == 200, res.text
    return res.json()


async def _run(stage: str, *, tenant_id: int, document_id: int) -> None:
    """Run one stage through the real activity, exactly as the workflow would."""
    await run_stage(
        StageInput(
            tenant_id=tenant_id,
            workflow_id=f"ingest:{tenant_id}:{document_id}",
            document_id=document_id,
            stage=stage,
        )
    )


async def _document(document_id: int, *, tenant_id: int) -> Document:
    """Read one document back over the serving role, under its own tenant scope."""
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, tenant_id)
        return (
            await session.execute(select(Document).where(Document.id == document_id))
        ).scalar_one()


async def _chunks(document_id: int, *, tenant_id: int) -> list[Chunk]:
    """Read a document's chunks back over the serving role, in insertion order."""
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, tenant_id)
        return list(
            (
                await session.execute(
                    select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.id)
                )
            )
            .scalars()
            .all()
        )


def _keyword_backend() -> LightRAGBackend:
    """Build the lexical arm over the suite's serving session factory.

    ``keyword_recall`` touches neither the completer nor the embedder — it is a
    PostgreSQL full-text query — so those two are placeholders here and nothing else is.
    The session factory is the real serving one, which is what makes the read subject to
    the ``tenant_isolation`` policy.
    """

    async def _unused_complete(*args: object, **kwargs: object) -> object:  # pragma: no cover
        raise AssertionError("keyword_recall must not call the model gateway")

    async def _unused_embed(texts: list[str]) -> list[list[float]]:  # pragma: no cover
        raise AssertionError("keyword_recall must not embed anything")

    return LightRAGBackend(
        _unused_complete,  # type: ignore[arg-type] - never called; see the docstring
        _unused_embed,
        session_factory=get_sessionmaker(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# The whole point: an uploaded document is findable by keyword
# ─────────────────────────────────────────────────────────────────────────────


async def test_an_uploaded_pdf_becomes_chunks_that_keyword_search_finds(
    client, db, wired, store, temporal
) -> None:
    """Upload a real PDF, run the stages, and find its own words in the corpus.

    Every assertion is on a real row: the page count Docling reported, the title read off
    the document's first heading, the chunk rows carrying their tenant and their document,
    and finally a hit from the corpus-wide lexical arm — which before task 4.7 could only
    re-rank what dense retrieval had already found, and before this task had no corpus to
    search at all.
    """
    await _seed_tenants()
    data = fixture_pdf().read_bytes()
    body = await _upload(
        client, data, tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A
    )
    document_id = body["document_id"]
    assert temporal.started == [f"ingest:{_TENANT_A}:{document_id}"]

    for stage in ("parse", "chunk", "enrich"):
        await _run(stage, tenant_id=_TENANT_A, document_id=document_id)

    document = await _document(document_id, tenant_id=_TENANT_A)
    assert document.completed_stage == "enrich"
    assert document.page_count == 15, "the fixture is 15 pages; the parse said otherwise"
    assert document.chunk_count and document.chunk_count > 0
    assert document.title, "the parse recorded no title at all"
    # Supplied by the uploader, and carried unchanged — never inferred from created_at.
    assert document.doc_type == "research paper"
    assert document.doc_date.isoformat() == "2017-06-12"

    chunks = await _chunks(document_id, tenant_id=_TENANT_A)
    assert len(chunks) == document.chunk_count
    assert all(chunk.tenant_id == _TENANT_A for chunk in chunks)
    assert all(chunk.document_id == document_id for chunk in chunks)
    # D7: the prefix is *in* the embedded text, which is also the text PostgreSQL
    # generates ``search_vector`` from.
    first = chunks[0]
    assert first.content.startswith(first.meta["prefix"] + "\n")
    assert document.doc_date.isoformat() in first.meta["prefix"]
    assert "untyped" not in first.meta["prefix"]
    # Provenance survives to the row: a citation can name a page.
    assert first.meta["spans"], "a chunk with no page span cannot be cited"

    hits = await _keyword_backend().keyword_recall(
        _QUERY, top_k=5, scope=RetrievalScope(tenant_id=_TENANT_A)
    )

    assert hits, f"nothing in the corpus matched {_QUERY!r} after a successful ingest"
    assert all(hit.metadata["document_id"] == document_id for hit in hits)
    assert any("attention" in hit.text.lower() for hit in hits)


# ─────────────────────────────────────────────────────────────────────────────
# Idempotency: the same stage, run twice
# ─────────────────────────────────────────────────────────────────────────────


async def _ingest_from_artifact(
    client, artifact: tuple[bytes, str], *, tenant_id: int, username: str, user_id: int
) -> int:
    """Upload the fixture and seed its parse artifact, without re-parsing it.

    Args:
        client: The ASGI client.
        artifact: ``(bytes, artifact_json)`` from the session-scoped parse.
        tenant_id: The uploading tenant.
        username: The uploading principal's name.
        user_id: The uploading principal's id.

    Returns:
        The document id, with ``chunk``'s input already in place.
    """
    data, payload = artifact
    body = await _upload(
        client, data, tenant_id=tenant_id, username=username, user_id=user_id
    )
    from app.ingestion.stages import _deps  # noqa: PLC0415 - the installed test store

    _deps().store.put_artifact(
        tenant_id=tenant_id, sha256=body["content_sha256"], payload=payload
    )
    return body["document_id"]


async def test_running_the_chunk_stage_twice_leaves_exactly_one_set_of_chunks(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """The contract's own requirement, asserted the only way it can be: by doing it twice.

    The second call goes through :func:`app.jobs.activities.run_stage` with the stage
    already committed *and* through the handler directly, because those are two different
    guarantees. The first is the substrate's replay short-circuit; the second is the
    handler's own write being delete-then-insert rather than a bare insert — and it is the
    second that covers the case the substrate cannot: an attempt that wrote its rows and
    was retried for an unrelated reason.
    """
    await _seed_tenants()
    document_id = await _ingest_from_artifact(
        client, parsed_artifact, tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A
    )

    await _run("chunk", tenant_id=_TENANT_A, document_id=document_id)
    after_first = await _chunks(document_id, tenant_id=_TENANT_A)
    assert after_first, "the chunk stage wrote nothing at all"

    # (a) through the substrate: a replay finds the stage committed and does nothing.
    await _run("chunk", tenant_id=_TENANT_A, document_id=document_id)
    # (b) through the handler itself, which is the write the SQL clause protects.
    from app.ingestion.stages import chunk_stage  # noqa: PLC0415 - the unit under test

    async with db() as session:
        await set_tenant_scope(session, _TENANT_A)
        await chunk_stage(
            session, tenant_id=_TENANT_A, document_id=document_id, stage="chunk"
        )
        await session.commit()

    after_second = await _chunks(document_id, tenant_id=_TENANT_A)
    assert len(after_second) == len(after_first), (
        "running the chunk stage twice left two sets of chunks: every one of this "
        "document's passages would now be returned twice, and half of them are stale"
    )
    assert [chunk.content for chunk in after_second] == [
        chunk.content for chunk in after_first
    ]
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, _TENANT_A)
        total = (
            await session.execute(select(func.count(Chunk.id)))
        ).scalar_one()
    assert total == len(after_first)


async def test_running_the_enrich_stage_twice_does_not_prefix_twice(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """The enrich UPDATE is guarded, so a second run is a no-op rather than a second fold.

    A doubled prefix would not raise anywhere: it would quietly spend tokens twice, shift
    every chunk's embedding, and perturb the lexical statistics of the whole corpus.
    """
    await _seed_tenants()
    document_id = await _ingest_from_artifact(
        client, parsed_artifact, tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A
    )
    await _run("chunk", tenant_id=_TENANT_A, document_id=document_id)

    from app.ingestion.stages import enrich_stage  # noqa: PLC0415 - the unit under test

    for _ in range(2):
        async with db() as session:
            await set_tenant_scope(session, _TENANT_A)
            await enrich_stage(
                session, tenant_id=_TENANT_A, document_id=document_id, stage="enrich"
            )
            await session.commit()

    for chunk in await _chunks(document_id, tenant_id=_TENANT_A):
        prefix = chunk.meta["prefix"]
        assert chunk.content.startswith(prefix + "\n")
        assert not chunk.content[len(prefix) + 1 :].startswith(prefix), (
            "the prefix was folded in twice"
        )
        assert chunk.meta["enriched"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Isolation: the corpus one tenant uploaded is not the other's
# ─────────────────────────────────────────────────────────────────────────────


async def test_one_tenants_chunks_are_invisible_to_another_tenant(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """Tenant B searches for a phrase that is unmistakably in tenant A's document.

    Read over the ``NOSUPERUSER NOBYPASSRLS`` role, so the ``tenant_isolation`` policy on
    ``chunks`` is genuinely applied to both reads — the arm's ``WHERE tenant_id`` predicate
    is the second line of defence here, not the only one. Tenant A's own hit is the
    positive control: without it, "B found nothing" is equally true of an ingest that
    wrote nothing.
    """
    await _seed_tenants()
    document_id = await _ingest_from_artifact(
        client, parsed_artifact, tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A
    )
    for stage in ("chunk", "enrich"):
        await _run(stage, tenant_id=_TENANT_A, document_id=document_id)

    backend = _keyword_backend()
    mine, theirs = await asyncio.gather(
        backend.keyword_recall(_QUERY, top_k=5, scope=RetrievalScope(tenant_id=_TENANT_A)),
        backend.keyword_recall(_QUERY, top_k=5, scope=RetrievalScope(tenant_id=_TENANT_B)),
    )

    assert mine, "the owning tenant could not find its own document"
    assert theirs == [], "another tenant's keyword search reached this document's chunks"

    # And the rows themselves are not visible either, count included.
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, _TENANT_B)
        visible = (await session.execute(select(func.count(Chunk.id)))).scalar_one()
    assert visible == 0, "tenant B can count another tenant's chunks"


# ─────────────────────────────────────────────────────────────────────────────
# The stage that has nothing to work with says so, rather than committing progress
# ─────────────────────────────────────────────────────────────────────────────


async def test_the_chunk_stage_refuses_a_document_whose_parse_artifact_is_gone(
    client, db, wired, store, temporal
) -> None:
    """No artifact means no chunks — and no ``completed_stage`` bump either.

    Silently re-parsing here would hide the fact that the artifact is missing, and
    committing the stage without writing chunks would advance a document past work that
    never happened.
    """
    await _seed_tenants()
    body = await _upload(
        client,
        b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n",
        tenant_id=_TENANT_A,
        username="a-admin",
        user_id=_USER_A,
    )
    document_id = body["document_id"]

    with pytest.raises(Exception, match="parse artifact"):
        await _run("chunk", tenant_id=_TENANT_A, document_id=document_id)

    document = await _document(document_id, tenant_id=_TENANT_A)
    assert document.completed_stage is None
    assert document.chunk_count is None
    assert await _chunks(document_id, tenant_id=_TENANT_A) == []
