"""``embed``, ``index`` and ``graph``: what each writes, and what running it twice does.

These three stages spend money and reach outside the database, so their collaborators are
injected here — a deterministic embedder, a recording publisher, a fixed extractor — while
everything they *write* is real: real chunk rows, in a real PostgreSQL, through the real
stage-runner activity under a bound tenant scope.

Injecting the embedder is not a way of avoiding the real one. It is the only way to assert
the property that matters about this stage, which is not "the vectors are good" (that is a
claim about a provider) but **"every chunk got its own vector, and running the stage again
does not leave two"**. A recorded call log makes an off-by-one in the batching visible; a
live provider would hide it behind plausible numbers.

The chunks under test come from the fixture PDF's real parse artifact, so the texts being
embedded, published and extracted from are the texts a real ingest produces.
"""

from __future__ import annotations

import pgsupport
import pytest
from aegis.governance.models import Budget, BudgetScope, BudgetWindow
from aegis.jobs import Chunk
from aegis.retrieval.graph_extract import Entity, Relation
from sqlalchemy import select

from app.api.schemas import Role
from app.core.security import TENANT_ADMIN, create_access_token
from app.data import Tenant, User, get_sessionmaker, set_tenant_scope
from app.ingestion.stages import (
    IngestDependencies,
    embed_stage,
    graph_stage,
    index_stage,
    set_ingest_dependencies,
)
from app.jobs.activities import run_stage
from app.jobs.flows.contracts import StageInput

from .conftest import FIXTURE

pytestmark = pytest.mark.asyncio

_TENANT = 1
_USER = 11


class _CountingEmbed:
    """A deterministic embedder that records the batches it was given.

    The vector is derived from the text's own length and hash, so two different chunks get
    two different vectors and the *same* chunk gets the same one twice — which is what
    makes "the stage re-ran and wrote the same thing" a checkable statement.
    """

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    async def __call__(self, texts: list[str]) -> list[list[float]]:
        """Return one small vector per text, recording the batch."""
        self.batches.append(list(texts))
        return [[float(len(text)), float(hash(text) % 1000)] for text in texts]

    @property
    def embedded(self) -> list[str]:
        """Every text this embedder was asked for, in order."""
        return [text for batch in self.batches for text in batch]


class _RecordingPublisher:
    """Stands in for the knowledge backend, recording what the index stage published."""

    def __init__(self) -> None:
        self.calls: list[list[object]] = []

    async def __call__(self, chunks) -> None:  # noqa: ANN001 - Sequence[RetrievalChunk]
        """Record one publish."""
        self.calls.append(list(chunks))


class _FixedExtractor:
    """An extractor whose output depends only on the text, so re-runs are comparable."""

    name = "fixed-test-extractor"

    async def extract(self, chunk_text: str) -> tuple[list[Entity], list[Relation]]:
        """Return one entity per chunk, and a relation when the chunk mentions attention."""
        entity = Entity.make("Transformer", "product")
        if "attention" in chunk_text.lower():
            other = Entity.make("Attention", "procedure")
            return ([entity, other], [Relation(entity.id, other.id, "is built from")])
        return ([entity], [])


def _headers() -> dict[str, str]:
    """A tenant-admin bearer for the one tenant these tests use."""
    token = create_access_token(
        user_id=_USER, username="a-admin", role=TENANT_ADMIN, tenant_id=_TENANT
    )
    return {"Authorization": f"Bearer {token}"}


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


async def _chunked_document(client, artifact, store) -> int:
    """Upload the fixture, seed its parse artifact, run ``chunk`` and ``enrich``.

    Args:
        client: The ASGI client.
        artifact: ``(bytes, artifact_json)`` from the session-scoped parse.
        store: The temporary document store the handlers were given.

    Returns:
        The document id, with real chunk rows in place.
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
    for stage in ("chunk", "enrich"):
        await run_stage(
            StageInput(
                tenant_id=_TENANT,
                workflow_id=f"ingest:{_TENANT}:{body['document_id']}",
                document_id=body["document_id"],
                stage=stage,
            )
        )
    return body["document_id"]


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


async def _handler(db, handler, document_id: int, stage: str) -> None:
    """Call one stage handler directly, on its own committed transaction.

    The second run of a stage has to go through the *handler* and not through
    :func:`app.jobs.activities.run_stage`: the substrate short-circuits a stage that has
    already committed, so running it again that way proves the replay guard works and says
    nothing about the write underneath it. The write is what these tests are about.

    Args:
        db: The serving session factory.
        handler: The stage handler to call.
        document_id: The document to run it for.
        stage: The stage name, passed to the handler as the substrate would.
    """
    async with db() as session:
        await set_tenant_scope(session, _TENANT)
        await handler(session, tenant_id=_TENANT, document_id=document_id, stage=stage)
        await session.commit()


async def _chunks(document_id: int) -> list[Chunk]:
    """Read a document's chunks back over the serving role, in insertion order."""
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, _TENANT)
        return list(
            (
                await session.execute(
                    select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.id)
                )
            )
            .scalars()
            .all()
        )


# ── embed ────────────────────────────────────────────────────────────────────


async def test_embed_writes_one_vector_per_chunk_and_re_running_leaves_one(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """Every chunk gets its own embedding of record, and a re-run overwrites in place.

    The embedded text is the *enriched* content — prefix included — because that is what
    the retrieval side compares against, and embedding the bare body would silently make
    D7's measured gain unavailable to the dense arm.
    """
    embed = _CountingEmbed()
    set_ingest_dependencies(IngestDependencies(store=store, embed=embed))
    await _seed_tenant()
    document_id = await _chunked_document(client, parsed_artifact, store)
    chunks = await _chunks(document_id)

    await _run("embed", document_id)

    assert embed.embedded == [chunk.content for chunk in chunks]
    assert all(chunk.content.startswith(chunk.meta["prefix"]) for chunk in chunks)
    embedded = await _chunks(document_id)
    assert len(embedded) == len(chunks)
    assert all(len(chunk.embedding) == 2 for chunk in embedded), (
        "a chunk was left with the empty 'not embedded yet' vector"
    )

    # Twice: same rows, same vectors, no duplicates.
    await _handler(db, embed_stage, document_id, "embed")
    again = await _chunks(document_id)
    assert [chunk.id for chunk in again] == [chunk.id for chunk in embedded]
    assert [chunk.embedding for chunk in again] == [chunk.embedding for chunk in embedded]


async def test_embed_refuses_a_provider_that_returns_the_wrong_number_of_vectors(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """An off-by-one would attach each chunk's vector to a different chunk, undetectably."""

    async def short(texts: list[str]) -> list[list[float]]:
        """Return one fewer vector than it was given texts."""
        return [[0.1, 0.2] for _ in texts[:-1]]

    set_ingest_dependencies(IngestDependencies(store=store, embed=short))
    await _seed_tenant()
    document_id = await _chunked_document(client, parsed_artifact, store)

    with pytest.raises(Exception, match="vector"):
        await _run("embed", document_id)

    assert all(chunk.embedding == [] for chunk in await _chunks(document_id)), (
        "a refused embed stage still wrote vectors, so its output and its failure are "
        "not in one transaction"
    )


# ── index ────────────────────────────────────────────────────────────────────


async def test_index_publishes_every_chunk_under_a_tenant_scoped_stable_id(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """Content-addressed and tenant-prefixed: a re-publish overwrites, and cannot collide.

    Two tenants who uploaded the same public filing produce identical chunk *text*, so an
    id built from the content alone would have one tenant's row overwrite the other's in a
    store whose ids are global.
    """
    publisher = _RecordingPublisher()
    set_ingest_dependencies(IngestDependencies(store=store, publish=publisher))
    await _seed_tenant()
    document_id = await _chunked_document(client, parsed_artifact, store)
    chunks = await _chunks(document_id)

    await _run("index", document_id)

    assert len(publisher.calls) == 1
    published = publisher.calls[0]
    assert len(published) == len(chunks)
    assert [item.text for item in published] == [chunk.content for chunk in chunks]
    # ``t1``, the canonical owner token ``aegis.retrieval.types`` mints — the same string
    # the read side filters on, so the write and the read cannot drift apart.
    assert all(item.id.startswith(f"t{_TENANT}:") for item in published)
    assert all(item.metadata["tenant_id"] == f"t{_TENANT}" for item in published)
    assert all(item.metadata["document_id"] == document_id for item in published)
    assert len({item.id for item in published}) == len(published), "two chunks share an id"

    # A second run publishes the same ids, which is what makes it an overwrite.
    await _handler(db, index_stage, document_id, "index")
    assert [item.id for item in publisher.calls[1]] == [item.id for item in published]


# ── graph ────────────────────────────────────────────────────────────────────


async def test_graph_records_the_entities_and_relations_on_the_chunk_row(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """The extraction lands on rows we own, named with the extractor that produced it.

    Which extractor ran is recorded beside its output because a corpus extracted by the
    deterministic extractor and one extracted by an LLM are not the same corpus, and
    nothing downstream could tell them apart otherwise.
    """
    set_ingest_dependencies(
        IngestDependencies(store=store, extractor=_FixedExtractor())
    )
    await _seed_tenant()
    document_id = await _chunked_document(client, parsed_artifact, store)

    await _run("graph", document_id)

    chunks = await _chunks(document_id)
    assert all(chunk.meta["extractor"] == "fixed-test-extractor" for chunk in chunks)
    assert all(chunk.meta["entities"] for chunk in chunks)
    assert any(chunk.meta["relations"] for chunk in chunks)
    # The provenance the chunk stage wrote is still there: this stage adds, never replaces.
    assert all(chunk.meta["prefix"] for chunk in chunks)

    before = [chunk.meta for chunk in chunks]
    await _handler(db, graph_stage, document_id, "graph")
    assert [chunk.meta for chunk in await _chunks(document_id)] == before, (
        "a second extraction changed the recorded graph for text that did not change"
    )
