"""The chunks in PostgreSQL and the vectors in Qdrant must not silently disagree.

This is one claim with one failure mode, and it is the claim that was never checked.

The corpus lives in two stores. ``chunks`` holds the text and the embedding of record;
the dense index holds the vectors retrieval actually searches. Each store can be
internally perfect while disagreeing with the other, and nothing in the platform ever
asked them the same question. So when the vector engine was swapped — Chroma deleted for
Qdrant in ``3dafbdb``, five hours after the only real document was ingested — the rows
survived, the index did not, and **every part of the system continued to report success**.
The ``index`` stage recorded ``{"indexed": 37}`` onto the ingest log against a collection
holding zero points; the Jobs funnel drew that 37; retrieval answered from nothing.

The two tests below are that claim and that failure mode:

* :func:`test_indexed_chunks_are_present_in_the_dense_index` reads **both stores** and
  compares them. It never asks the stage what it did — the whole defect was a stage whose
  self-report was believed.
* :func:`test_index_stage_refuses_to_report_success_when_nothing_was_written` reproduces
  the exact silent-publish shape (``LightRAG.ainsert`` returns normally having written
  nothing) and requires the stage to fail loudly instead of recording a success.

Both run against a **real** Qdrant — ``qdrant_client``'s in-process mode, the same
implementation the server exposes — and a real PostgreSQL, so the agreement being asserted
is between two real engines rather than two fakes that were written to match.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator

import pgsupport
import pytest
from aegis.governance.models import Budget, BudgetScope, BudgetWindow
from aegis.jobs import Chunk
from aegis.retrieval.chunk_index import (
    ChunkPoint,
    audit_chunk_index,
    effective_workspace,
    lightrag_point_id,
    publish_chunk_points,
)
from qdrant_client import QdrantClient, models
from sqlalchemy import select

from app.api.schemas import Role
from app.core.security import TENANT_ADMIN, create_access_token
from app.data import Tenant, User, get_sessionmaker, set_tenant_scope
from app.ingestion.stages import (
    IngestDependencies,
    index_stage,
    set_ingest_dependencies,
)
from app.jobs.activities import run_stage
from app.jobs.flows.contracts import StageInput

from .conftest import FIXTURE

pytestmark = pytest.mark.asyncio

_TENANT = 1
_USER = 11

#: Width of the test embedder's vectors. Small on purpose — this suite is about whether
#: the two stores agree on *which* vectors exist, not about embedding quality, and a
#: 3072-float vector per chunk would make the fixture slow for no added proof.
_DIM = 2

_COLLECTION = "test_vdb_chunks"


def _headers() -> dict[str, str]:
    """A tenant-admin bearer for the one tenant these tests use."""
    token = create_access_token(
        user_id=_USER, username="a-admin", role=TENANT_ADMIN, tenant_id=_TENANT
    )
    return {"Authorization": f"Bearer {token}"}


class _ContentAddressedEmbed:
    """An embedder whose vector is a pure function of the text it was given."""

    @staticmethod
    def vector_of(text: str) -> list[float]:
        """Return the one vector this embedder will ever return for ``text``."""
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [
            int.from_bytes(digest[0:8], "big") / 2**64,
            int.from_bytes(digest[8:16], "big") / 2**64,
        ]

    async def __call__(self, texts: list[str]) -> list[list[float]]:
        """Return one content-derived vector per text."""
        return [self.vector_of(text) for text in texts]


class _QdrantPublisher:
    """The real publish path, pointed at an in-process Qdrant.

    Stands in for ``LightRAGBackend.publish_vectors`` at exactly the seam the stage uses,
    running the shipped :func:`publish_chunk_points` against a real engine — so the point
    ids, payload shape and workspace tag under test are the ones production writes.
    """

    def __init__(self, client: QdrantClient) -> None:
        """Hold the client to publish into."""
        self.client = client
        self.published: list[str] = []

    @staticmethod
    def _points(chunks) -> list[ChunkPoint]:  # noqa: ANN001 - Sequence[RetrievalChunk]
        """Shape the stage's chunks into dense-index points."""
        return [
            ChunkPoint(
                key=chunk.id,
                content=chunk.text,
                file_path=f"t{_TENANT}::{chunk.metadata.get('source')}",
                full_doc_id=str(chunk.doc_id),
                vector=chunk.vector or [],
            )
            for chunk in chunks
            if chunk.vector
        ]

    async def publish(self, chunks) -> None:  # noqa: ANN001 - Sequence[RetrievalChunk]
        """Write the chunks' stored vectors into the index."""
        points = self._points(chunks)
        self.published = [point.key for point in points]
        publish_chunk_points(self.client, points, collection=_COLLECTION)

    async def verify(self, chunks) -> int | None:  # noqa: ANN001
        """Return how many of ``chunks`` the index actually holds."""
        drift = audit_chunk_index(
            self.client, self._points(chunks), collection=_COLLECTION
        )
        return len(drift.present)


@pytest.fixture
def dense() -> Iterator[QdrantClient]:
    """An in-process Qdrant holding the chunk collection at the test's width."""
    client = QdrantClient(":memory:")
    client.create_collection(
        collection_name=_COLLECTION,
        vectors_config=models.VectorParams(
            size=_DIM, distance=models.Distance.COSINE
        ),
    )
    try:
        yield client
    finally:
        client.close()


async def _seed_tenant() -> None:
    """One tenant, one admin, one budget row generous enough to admit the ingest."""
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Tenant(id=_TENANT, name="Tenant A"),
            User(id=_USER, username="a-admin", role=Role.ADMIN, tenant_id=_TENANT),
            Budget(
                tenant_id=_TENANT,
                scope_type=BudgetScope.TENANT,
                scope_id=_TENANT,
                window=BudgetWindow.DAY,
                usd_cap=100.0,
            ),
        )
        await session.commit()


async def _run(stage: str, document_id: int) -> None:
    """Run one stage through the real activity."""
    await run_stage(
        StageInput(
            tenant_id=_TENANT,
            workflow_id=f"ingest:{_TENANT}:{document_id}:{stage}",
            document_id=document_id,
            stage=stage,
        )
    )


async def _embedded_document(client, artifact, store) -> int:
    """Upload the fixture and run it through ``chunk``, ``enrich`` and ``embed``.

    Args:
        client: The ASGI client.
        artifact: ``(bytes, artifact_json)`` from the session-scoped parse.
        store: The temporary document store the handlers were given.

    Returns:
        The document id, with real chunk rows carrying real embeddings of record.
    """
    data, payload = artifact
    res = await client.post(
        "/documents",
        files={"file": (FIXTURE, data, "application/pdf")},
        headers=_headers(),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    store.put_artifact(
        tenant_id=_TENANT, sha256=body["content_sha256"], payload=payload
    )
    for stage in ("chunk", "enrich", "embed"):
        await _run(stage, body["id"])
    return body["id"]


async def _chunk_rows(document_id: int) -> list[Chunk]:
    """Read a document's chunks back over the serving role, in insertion order."""
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, _TENANT)
        return list(
            (
                await session.execute(
                    select(Chunk)
                    .where(Chunk.document_id == document_id)
                    .order_by(Chunk.id)
                )
            )
            .scalars()
            .all()
        )


async def test_indexed_chunks_are_present_in_the_dense_index(
    client, db, wired, store, temporal, parsed_artifact, dense
) -> None:
    """Every embedded chunk row has a matching point in the dense index, and vice versa.

    The assertion reads **both stores independently** and compares the two sets. It does
    not consult the stage's own report, its log line or its recorded facts, because every
    one of those said ``37`` on the day the index held nothing — the stage was not lying
    about what it intended, it was reporting an intention as an outcome.

    The comparison is by *point id*, derived from each row's content-addressed key, so
    this also pins the addressing: a stage that wrote the right number of points under
    the wrong ids would satisfy a count and fail here. That matters because "the right
    number of unfindable points" is precisely what a workspace or id-scheme mismatch
    produces, and it is indistinguishable from success from every angle except the answer.
    """
    publisher = _QdrantPublisher(dense)
    set_ingest_dependencies(
        IngestDependencies(
            store=store,
            embed=_ContentAddressedEmbed(),
            publish=publisher.publish,
            verify=publisher.verify,
        )
    )
    await _seed_tenant()
    document_id = await _embedded_document(client, parsed_artifact, store)

    await _run("index", document_id)

    rows = await _chunk_rows(document_id)
    assert rows, "the fixture must produce chunks for this test to mean anything"

    workspace = effective_workspace()
    expected = {
        lightrag_point_id(
            f"t{_TENANT}:{(row.meta or {}).get('content_id') or row.id}",
            workspace=workspace,
        )
        for row in rows
    }
    actual = {
        str(point.id).replace("-", "")
        for point in dense.scroll(
            collection_name=_COLLECTION, limit=len(rows) * 4, with_payload=False
        )[0]
    }

    assert actual == expected, (
        f"the two stores disagree: {len(expected - actual)} chunk row(s) have no vector "
        f"and {len(actual - expected)} vector(s) belong to no chunk row"
    )

    # And the vector the index serves is the one the database holds — not a second,
    # separately-derived embedding that merely happens to be close to it.
    served = dense.retrieve(
        collection_name=_COLLECTION,
        ids=[str(point_id) for point_id in sorted(expected)[:1]],
        with_vectors=True,
    )[0]
    assert [round(value, 9) for value in served.vector] in [
        [round(value, 9) for value in row.embedding] for row in rows
    ]


async def test_index_stage_refuses_to_report_success_when_nothing_was_written(
    client, db, wired, store, temporal, parsed_artifact, dense
) -> None:
    """A publish that silently writes nothing must fail the stage, not pass it.

    This is the exact shape of the original defect, reproduced rather than described.
    ``LightRAG.ainsert`` records per-document failures into its own doc-status store and
    **returns normally** — so the publish call succeeded, the stage had nothing to catch,
    and it went on to log ``"indexed 37 chunk(s)"`` and write ``{"indexed": 37}`` onto the
    ingest log while the collection stayed empty. 73 such failure rows are still in
    ``lightrag_doc_status`` for this corpus.

    The publisher here does the same thing: accepts the chunks, writes nothing, raises
    nothing. The stage must now notice, because it asks the store instead of itself.

    Without the check this is a *passing* ingest — which is why the assertion is that the
    run raises, and not merely that some counter came back low.
    """

    async def _silently_writes_nothing(chunks) -> None:  # noqa: ANN001
        """Accept the chunks and write nothing, exactly as the real defect did."""

    publisher = _QdrantPublisher(dense)
    set_ingest_dependencies(
        IngestDependencies(
            store=store,
            embed=_ContentAddressedEmbed(),
            publish=_silently_writes_nothing,
            verify=publisher.verify,
        )
    )
    await _seed_tenant()
    document_id = await _embedded_document(client, parsed_artifact, store)

    with pytest.raises(Exception, match="search index holds"):
        await index_stage(
            (await _open_scoped_session(db)),
            tenant_id=_TENANT,
            document_id=document_id,
            stage="index",
        )

    # The store is the witness: still empty, and the stage did not pretend otherwise.
    assert dense.count(collection_name=_COLLECTION).count == 0


async def _open_scoped_session(db):  # noqa: ANN001, ANN202 - test helper
    """Return a tenant-scoped session for a direct handler call.

    The handler is called directly rather than through :func:`run_stage` because the
    substrate wraps a failing stage in its own retry/record machinery, and what is under
    test is the handler's own refusal.
    """
    session = db()
    await session.__aenter__()
    await set_tenant_scope(session, _TENANT)
    return session
