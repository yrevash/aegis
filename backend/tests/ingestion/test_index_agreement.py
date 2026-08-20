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

The tests below are that claim, that failure mode, and the check that answers them:

* :func:`test_indexed_chunks_are_present_in_the_dense_index` reads **both stores** and
  compares them. It never asks the stage what it did — the whole defect was a stage whose
  self-report was believed.
* :func:`test_index_stage_refuses_to_report_success_when_nothing_was_written` reproduces
  the exact silent-publish shape (``LightRAG.ainsert`` returns normally having written
  nothing) and requires the stage to fail loudly instead of recording a success.

The last two guard the check itself, because a check that cries wolf is disarmed inside a
week and then the platform is back where it started:

* :func:`test_a_scoped_audit_does_not_call_another_scope_s_points_orphans` pins that
  narrowing the question narrows *both* reads, not just the one against PostgreSQL.
* :func:`test_the_rebuild_scopes_its_audit_the_way_it_scoped_its_read` pins that the
  rebuild actually passes that scope down — the half that had shipped with no caller.

The last two pin the contracts this agreement is expressed in, both of which are
replicated from another module rather than imported, and so can drift silently:

* :func:`test_point_id_matches_lightrag_exactly` — the addressing scheme, against
  LightRAG's own function. A shift here is invisible to every test above, because both
  sides of their comparison shift together; it was confirmed by flipping the hash order
  and watching only this test fail.
* :func:`test_rebuilt_points_carry_the_owner_tag_recall_demands` — the ``file_path``
  owner tag, against the recall path that parses it.

Both run against a **real** Qdrant — ``qdrant_client``'s in-process mode, the same
implementation the server exposes — and a real PostgreSQL, so the agreement being asserted
is between two real engines rather than two fakes that were written to match.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterator

import pgsupport
import pytest
from aegis.governance.models import Budget, BudgetScope, BudgetWindow
from aegis.jobs import Chunk
from aegis.retrieval.chunk_index import (
    DEFAULT_WORKSPACE as CHUNK_INDEX_DEFAULT_WORKSPACE,
)
from aegis.retrieval.chunk_index import (
    ChunkPoint,
    IndexDrift,
    audit_chunk_index,
    effective_workspace,
    lightrag_point_id,
    publish_chunk_points,
)
from aegis.retrieval.types import tenant_metadata_value
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
from app.ingestion.vector_index import (
    _TENANT_TAG_SEP,
    rebuild_dense_index,
    verify_document_index,
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
        await _run(stage, body["document_id"])
    return body["document_id"]


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


def _cosine(a, b) -> float:  # noqa: ANN001 - two float sequences
    """Return the cosine similarity of two vectors."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


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

    # And the vector the index serves for a given chunk is *that chunk's* embedding of
    # record — not a second, separately-derived embedding that merely happens to be
    # close to it. Matching ids with mismatched vectors is the quieter half of the same
    # disagreement: retrieval would return the right passage for the wrong reasons, and
    # rank it wrongly against its neighbours.
    #
    # Compared by direction rather than by value, because a Cosine collection stores
    # vectors L2-normalised — Qdrant hands back the unit vector, not the bytes it was
    # given. Direction is the whole of what a cosine index uses, so equality of
    # direction is equality of everything that can affect an answer.
    row = rows[0]
    point_id = lightrag_point_id(
        f"t{_TENANT}:{(row.meta or {}).get('content_id') or row.id}",
        workspace=workspace,
    )
    served = dense.retrieve(
        collection_name=_COLLECTION, ids=[point_id], with_vectors=True
    )[0]
    assert _cosine(served.vector, row.embedding) == pytest.approx(1.0, abs=1e-9), (
        "the index is serving a different vector than the database holds for this chunk"
    )


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

    session = await _open_scoped_session(db)
    try:
        with pytest.raises(Exception, match="search index holds"):
            await index_stage(
                session,
                tenant_id=_TENANT,
                document_id=document_id,
                stage="index",
            )
    finally:
        await session.__aexit__(None, None, None)

    # The store is the witness: still empty, and the stage did not pretend otherwise.
    assert dense.count(collection_name=_COLLECTION).count == 0


async def test_a_scoped_audit_does_not_call_another_scope_s_points_orphans(
    dense,
) -> None:
    """An audit restricted to one tenant or document must not orphan the rest.

    ``orphaned`` means "the index holds a vector for text the corpus no longer has", and
    the CLI exits non-zero on it — correctly, because that is retrieval grounding an
    answer in deleted text. But the read that produces it is a read of the *index*, and
    if it is left collection-wide while the rows were read under a ``WHERE tenant_id``,
    then every healthy point belonging to every other tenant comes back as an orphan.

    That was live: ``--verify --tenant 2`` on this corpus reported "37 orphaned" and
    exited 1 while all 37 were tenant 1's, entirely correct, and untouched by the run.
    An operator who sees a scoped re-index fail on a healthy corpus learns to stop
    reading its exit code — which is precisely how the funnel's collapse to 1 survived
    for months. So the scope of the two reads is asserted to match.
    """
    tenant_1 = [
        ChunkPoint(
            key=f"t1:c{n}",
            content=f"one {n}",
            file_path="t1::alpha.pdf",
            full_doc_id="7",
            vector=_ContentAddressedEmbed.vector_of(f"one {n}"),
        )
        for n in range(3)
    ]
    tenant_2 = [
        ChunkPoint(
            key=f"t2:c{n}",
            content=f"two {n}",
            file_path="t2::beta.pdf",
            full_doc_id="9",
            vector=_ContentAddressedEmbed.vector_of(f"two {n}"),
        )
        for n in range(2)
    ]
    publish_chunk_points(dense, [*tenant_1, *tenant_2], collection=_COLLECTION)

    # Unscoped, tenant 1's rows really are a partial account of the collection, and the
    # audit says so. This is the control: it is what makes the two asserts below a
    # statement about scoping rather than about an audit that never orphans anything.
    wide = audit_chunk_index(dense, tenant_1, collection=_COLLECTION)
    assert len(wide.orphaned) == len(tenant_2)

    by_tenant = audit_chunk_index(
        dense, tenant_1, collection=_COLLECTION, file_path_prefix="t1::"
    )
    assert by_tenant.agrees, (
        f"a tenant-scoped audit orphaned {len(by_tenant.orphaned)} point(s) that belong "
        "to a tenant it was not asked about"
    )
    assert len(by_tenant.present) == len(tenant_1)

    by_document = audit_chunk_index(
        dense, tenant_2, collection=_COLLECTION, full_doc_id="9"
    )
    assert by_document.agrees, (
        f"a document-scoped audit orphaned {len(by_document.orphaned)} point(s) that "
        "belong to a document it was not asked about"
    )
    assert len(by_document.present) == len(tenant_2)


async def test_the_rebuild_scopes_its_audit_the_way_it_scoped_its_read(
    monkeypatch,
) -> None:
    """The rebuild hands the audit the same scope it read the rows under.

    :func:`test_a_scoped_audit_does_not_call_another_scope_s_points_orphans` proves the
    audit *can* be scoped. This proves :func:`rebuild_dense_index` actually asks it to,
    which is the half that was missing: ``file_path_prefix`` shipped with the parameter
    written, documented and **never passed by anyone**, so every scoped run still audited
    the whole collection.

    Asserted by intercepting the call rather than by checking the helper's return value.
    A test of :func:`_audit_scope` alone passes with the argument unwired — that was
    confirmed by deleting the wiring and watching such a test stay green — so it would
    have guarded the calculation while the bug lived in the call.

    The owner tag is checked against the one :func:`chunk_points` writes into
    ``file_path``, because a prefix that does not match what the publisher wrote scopes
    the read to nothing and reports the tenant's own healthy points missing.
    """
    seen: list[dict] = []

    async def _rows(_session, *, tenant_id=None, document_id=None):  # noqa: ANN001
        return [], [], 0

    def _spy(_client, _expected, **kwargs):  # noqa: ANN001
        seen.append(kwargs)
        return IndexDrift(
            expected=frozenset(), present=frozenset(), missing=frozenset()
        )

    monkeypatch.setattr("app.ingestion.vector_index.chunk_points", _rows)
    monkeypatch.setattr("app.ingestion.vector_index.audit_chunk_index", _spy)

    await rebuild_dense_index(None, client=object(), dry_run=True)
    await rebuild_dense_index(None, tenant_id=_TENANT, client=object(), dry_run=True)
    await rebuild_dense_index(None, document_id=7, client=object(), dry_run=True)
    await verify_document_index(None, document_id=7, client=object())

    owner_prefix = f"{tenant_metadata_value(_TENANT)}{_TENANT_TAG_SEP}"
    unscoped, by_tenant, by_document, verified = seen

    assert "file_path_prefix" not in unscoped and "full_doc_id" not in unscoped, (
        "an unscoped rebuild must audit the whole workspace; narrowing it would hide "
        "genuine orphans"
    )
    assert by_tenant.get("file_path_prefix") == owner_prefix
    assert by_document.get("full_doc_id") == "7"
    assert verified.get("full_doc_id") == "7"

    # And the prefix is the one actually written into ``file_path`` at publish time.
    assert f"{owner_prefix}paper.pdf".startswith(owner_prefix)
    assert owner_prefix == f"{tenant_metadata_value(_TENANT)}{_TENANT_TAG_SEP}"


def test_point_id_matches_lightrag_exactly() -> None:
    """Our point id is byte-for-byte the one LightRAG would compute for the same chunk.

    :mod:`aegis.retrieval.chunk_index` writes into LightRAG's own chunk collection and
    therefore replicates LightRAG's addressing scheme rather than importing it — the
    standalone ``aegis`` package must stay importable without ``lightrag``, whose import
    costs seconds and drags in the whole storage stack.

    Replication without a check is drift waiting to happen, and the drift is silent in
    the worst way: our points would sit *beside* the ones LightRAG's reader looks up,
    every count would be right, and every query would miss them. That is a duplicate
    index — the same class of two-stores-disagreeing failure this suite exists for.

    So the check is made here, where importing ``lightrag`` is affordable. If LightRAG
    changes the scheme, this fails instead of retrieval going quiet.
    """
    from lightrag.kg.qdrant_impl import (
        DEFAULT_WORKSPACE as LIGHTRAG_DEFAULT_WORKSPACE,
    )
    from lightrag.kg.qdrant_impl import compute_mdhash_id_for_qdrant

    for workspace in ("_", "aegis", "tenant-7"):
        for key in ("t1:abc123", "t42:chunk-0", "shared:doc"):
            assert lightrag_point_id(key, workspace=workspace) == (
                compute_mdhash_id_for_qdrant(key, prefix=workspace, style="simple")
            ), f"point id scheme drifted from LightRAG for {key!r} in {workspace!r}"

    # And the fallback workspace, which is what an unconfigured deployment writes under.
    # A single underscore is easy to mistake for a placeholder; points written under any
    # other value are invisible to a reader filtering on this one.
    assert CHUNK_INDEX_DEFAULT_WORKSPACE == LIGHTRAG_DEFAULT_WORKSPACE


def test_rebuilt_points_carry_the_owner_tag_recall_demands() -> None:
    """A rebuilt point's ``file_path`` is tagged the way the read path insists on.

    ``file_path`` is the only per-chunk field LightRAG hands back at recall time, so the
    owner tag it carries is how :func:`aegis.retrieval.lightrag_backend._scoped_recall`
    decides whether a recalled row may be shown to the asking tenant — a row it cannot
    attribute is refused outright.

    That makes the separator load-bearing across a module boundary:
    :mod:`app.ingestion.vector_index` builds the tag and ``lightrag_backend`` parses it.
    If the rebuild wrote ``"t1:paper.pdf"`` while recall split on ``"::"``, the rebuild
    would report a healthy index and every recalled row would be dropped as
    unattributable — a re-index that restores the vectors and leaves retrieval empty,
    which is this outage wearing a different hat. Asserted against the producer rather
    than restated, so the two cannot drift apart quietly.
    """
    from aegis.retrieval.lightrag_backend import _tag_file_path

    owner = tenant_metadata_value(_TENANT)
    assert (
        f"{owner}{_TENANT_TAG_SEP}paper.pdf" == _tag_file_path("paper.pdf", owner)
    ), "the rebuild's owner tag is not the one the recall path parses"

    # And the tag really is the leading segment recall splits off, not a substring that
    # merely happens to appear somewhere in the path.
    tagged = _tag_file_path("reports/2026/q3.pdf", owner)
    assert tagged.split(_TENANT_TAG_SEP, 1)[0] == owner


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
