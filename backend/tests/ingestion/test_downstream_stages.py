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

import hashlib

import pgsupport
import pytest
from aegis.governance.models import Budget, BudgetScope, BudgetWindow
from aegis.jobs import Chunk
from aegis.jobs.facts import collect_stage_facts
from aegis.retrieval.graph_extract import Entity, Relation
from aegis.retrieval.types import tenant_metadata_value
from sqlalchemy import select
from temporalio.exceptions import ApplicationError

from app.api.schemas import Role
from app.core.security import TENANT_ADMIN, create_access_token
from app.data import Tenant, User, get_sessionmaker, set_tenant_scope
from app.ingestion.graph_projection import ProjectionResult
from app.ingestion.graph_vectors import GraphVectorResult
from app.ingestion.stages import (
    IngestDependencies,
    chunk_stage,
    embed_stage,
    enrich_stage,
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


class _ContentAddressedEmbed:
    """An embedder whose vector is a pure function of the text it was given.

    :class:`_CountingEmbed` proves *how many* vectors were written; this proves **which**.
    The vector is derived from a digest of the text alone — no counter, no position, no
    call order — so ``vector_of(chunk.content)`` is a claim about that chunk and nothing
    else. Reversing the pairing inside a batch, or handing every chunk the batch's last
    vector, changes what lands on the row and cannot change what this function says the
    row should hold.

    Two distinct digests can only collide with probability 2**-64 apiece, so a passing
    assertion is not an accident of a 1000-bucket hash.
    """

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    @staticmethod
    def vector_of(text: str) -> list[float]:
        """Return the one vector this embedder will ever return for ``text``."""
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        # Scaled into [0, 1) so the value survives the ``jsonb`` column as a float rather
        # than coming back as an integer — the column is the embedding of record, not a
        # place to smuggle a 64-bit integer through.
        return [
            int.from_bytes(digest[0:8], "big") / 2**64,
            int.from_bytes(digest[8:16], "big") / 2**64,
        ]

    async def __call__(self, texts: list[str]) -> list[list[float]]:
        """Return one content-derived vector per text, recording the batch."""
        self.batches.append(list(texts))
        return [self.vector_of(text) for text in texts]


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


async def test_embed_stores_each_chunks_own_vector_and_not_its_neighbours(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """The pairing, asserted — the failure ``embed_stage``'s own docstring names.

    ``EmbeddingCountMismatch`` catches a provider that returns the *wrong number* of
    vectors. It cannot catch a provider (or a ``zip``) that returns the right number in
    the wrong order, and that is the worse bug: every chunk ends up holding its
    neighbour's meaning, every dense hit is off by one passage, and nothing downstream —
    not the row count, not the vector width, not the re-run test — can see it. Reversing
    the pairing inside each batch passed the whole suite before this test existed; so did
    giving every chunk the last vector of its batch.

    The only assertion that closes it is the one the docstring implies: what is on the row
    equals what the embedder returns for **that row's own text**.
    """
    embed = _ContentAddressedEmbed()
    set_ingest_dependencies(IngestDependencies(store=store, embed=embed))
    await _seed_tenant()
    document_id = await _chunked_document(client, parsed_artifact, store)

    await _run("embed", document_id)

    chunks = await _chunks(document_id)
    assert len(chunks) > 1, "one chunk cannot mis-pair, so this fixture proves nothing"
    # Distinct texts, or a reversed pairing would be indistinguishable from a correct one.
    assert len({chunk.content for chunk in chunks}) == len(chunks)
    for chunk in chunks:
        assert chunk.embedding == pytest.approx(
            _ContentAddressedEmbed.vector_of(chunk.content)
        ), (
            f"chunk {chunk.id} (ordinal {chunk.meta['ordinal']}) holds a vector that is "
            "not the embedding of its own text"
        )
    # And the mis-pairing that is easiest to introduce is ruled out by name: no chunk
    # holds the vector of the chunk before or after it in reading order.
    for earlier, later in zip(chunks, chunks[1:], strict=False):
        assert earlier.embedding != later.embedding


async def test_embed_pairs_correctly_across_a_batch_boundary(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """The same property with the batch size forced down, so several batches really run.

    ``_EMBED_BATCH`` is 64 and the fixture may not exceed it, in which case the test above
    exercises exactly one batch and says nothing about the ``start`` arithmetic that slices
    the next one. Here the batch is 3, so the loop runs many times and an off-by-one in the
    slice — a batch re-embedded, a batch skipped, a batch's vectors written at the previous
    batch's offset — lands on a row and is read back.
    """
    embed = _ContentAddressedEmbed()
    set_ingest_dependencies(IngestDependencies(store=store, embed=embed))
    monkey = pytest.MonkeyPatch()
    monkey.setattr("app.ingestion.stages._EMBED_BATCH", 3, raising=True)
    try:
        await _seed_tenant()
        document_id = await _chunked_document(client, parsed_artifact, store)

        await _run("embed", document_id)
    finally:
        monkey.undo()

    chunks = await _chunks(document_id)
    assert len(embed.batches) == -(-len(chunks) // 3), "the batching did not partition"
    # Every text embedded exactly once, in reading order, across the batch boundaries.
    assert [text for batch in embed.batches for text in batch] == [
        chunk.content for chunk in chunks
    ]
    for chunk in chunks:
        assert chunk.embedding == pytest.approx(
            _ContentAddressedEmbed.vector_of(chunk.content)
        ), (
            f"chunk {chunk.id} (ordinal {chunk.meta['ordinal']}) holds a vector that is "
            "not the embedding of its own text"
        )


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


async def test_re_chunking_then_re_indexing_publishes_the_same_ids_under_new_row_ids(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """The content-addressing claim, on the only path where it can be observed.

    Publishing twice without re-chunking proves nothing: the ``chunks`` rows never moved,
    so ``meta['content_id']`` and the database primary key are equally constant and the
    two are indistinguishable. The property the ``index`` docstring actually claims — "a
    re-publish of unchanged text is an overwrite of the same key rather than a duplicate"
    — only has teeth after a **re-chunk**, because ``chunk_stage`` is delete-then-insert
    and mints a fresh primary key for every chunk. That is task 4.13's re-index path, and
    it is what this runs.

    So: chunk, index, re-chunk, index again. The primary keys must all have changed (or
    the test is the blind one again) and every published id must be identical.
    """
    publisher = _RecordingPublisher()
    set_ingest_dependencies(IngestDependencies(store=store, publish=publisher))
    await _seed_tenant()
    document_id = await _chunked_document(client, parsed_artifact, store)
    before = await _chunks(document_id)

    await _run("index", document_id)

    # Re-chunk through the handler: ``run_stage`` would short-circuit a committed stage,
    # and the delete-then-insert underneath is exactly what this test needs to happen.
    await _handler(db, chunk_stage, document_id, "chunk")
    # ``enrich`` too, because the re-index path runs it and because ``chunk`` writes the
    # bare body: without it the comparison below would be enriched text against raw text.
    await _handler(db, enrich_stage, document_id, "enrich")
    after = await _chunks(document_id)
    assert [chunk.id for chunk in after] != [chunk.id for chunk in before], (
        "the re-chunk reused its primary keys, so this test cannot tell a content-addressed "
        "id from a row id — which is the exact blindness it exists to remove"
    )
    assert not ({chunk.id for chunk in after} & {chunk.id for chunk in before})
    assert [chunk.content for chunk in after] == [
        chunk.content for chunk in before
    ], "the fixture re-chunked to different text; the ids below would differ for that reason"

    await _handler(db, index_stage, document_id, "index")

    first, second = publisher.calls[0], publisher.calls[1]
    assert len(second) == len(first) == len(before)
    assert [item.id for item in second] == [item.id for item in first], (
        "a re-index after a re-chunk published new ids, so the store now holds two copies "
        "of every chunk of this document"
    )
    # Not a row id in disguise: no published id ends in a primary key from either run.
    row_ids = {str(chunk.id) for chunk in [*before, *after]}
    assert not ({item.id.split(":", 1)[1] for item in second} & row_ids)
    assert len({item.id for item in second}) == len(second), "two chunks share an id"


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


class _RecordingProjector:
    """Stands in for the durable graph, recording what the stage handed it.

    The real projector refuses to run in a test process — there is no scratch Neo4j, so a
    test that wrote into it would be writing into the developer's own graph. What can be
    asserted here is the half that decides who sees the result: the tenant and the source
    the stage attributes its extraction to.
    """

    def __init__(self, result: ProjectionResult | None = None) -> None:
        """Hold the result to return, and start with an empty call log."""
        self.calls: list[dict[str, object]] = []
        self._result = result

    async def __call__(
        self,
        entities,  # noqa: ANN001 - Sequence[Entity]
        relations,  # noqa: ANN001 - Sequence[Relation]
        *,
        tenant_value: str | None,
        source: str,
        extractor: str,
    ) -> ProjectionResult:
        """Record one projection and return the configured outcome."""
        self.calls.append(
            {
                "entities": list(entities),
                "relations": list(relations),
                "tenant_value": tenant_value,
                "source": source,
                "extractor": extractor,
            }
        )
        if self._result is not None:
            return self._result
        return ProjectionResult(
            nodes=len({entity.id for entity in entities}),
            edges=len(relations),
            attempted_nodes=len({entity.id for entity in entities}),
            attempted_edges=len(relations),
        )


async def test_graph_projects_the_extraction_under_the_tenant_that_owns_it(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """The extraction reaches the durable graph, attributed to its owning tenant.

    The stage used to write ``chunks.meta`` and stop there, and ``GET /v1/graph`` reads
    neither that table nor anything derived from it — so an upload finished with the
    stage ``completed`` and the graph exactly as large as it had been. The provenance is
    asserted beside the projection because it is the same fact: an element the graph
    cannot attribute is shown to no tenant at all.
    """
    projector = _RecordingProjector()
    set_ingest_dependencies(
        IngestDependencies(
            store=store, extractor=_FixedExtractor(), project=projector
        )
    )
    await _seed_tenant()
    document_id = await _chunked_document(client, parsed_artifact, store)

    with collect_stage_facts() as facts:
        await _handler(db, graph_stage, document_id, "graph")

    assert len(projector.calls) == 1
    call = projector.calls[0]
    assert call["tenant_value"] == tenant_metadata_value(_TENANT)
    assert call["source"], "the projection was handed no source to attribute it to"
    assert call["extractor"] == "fixed-test-extractor"
    assert call["entities"], "nothing was handed to the graph to project"
    # What the graph confirmed holding, which is the only number worth reporting.
    assert facts["projected_entities"] == len(
        {entity.id for entity in call["entities"]}
    )
    assert facts["projected_relations"] == len(call["relations"])


async def test_graph_fails_rather_than_report_an_extraction_the_graph_did_not_get(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """A ``completed`` stage that grew no graph is the defect, not the extraction.

    The measured failure was a stage recording ``{"entities": 1}`` while Neo4j held 78
    nodes before the upload and 78 after. The extraction is on the chunk rows either way;
    what a person can see is what did not happen, and the run must say so.
    """
    set_ingest_dependencies(
        IngestDependencies(
            store=store,
            extractor=_FixedExtractor(),
            project=_RecordingProjector(
                ProjectionResult(nodes=0, edges=0, attempted_nodes=4, attempted_edges=1)
            ),
        )
    )
    await _seed_tenant()
    document_id = await _chunked_document(client, parsed_artifact, store)

    with pytest.raises(ApplicationError) as raised:
        await _handler(db, graph_stage, document_id, "graph")
    assert raised.value.type == "GraphNotProjected"


async def test_a_deployment_with_no_durable_graph_says_so_instead_of_reporting_zero(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """"There is no graph to project into" and "the graph is empty" are different facts.

    Collapsing them into ``projected_entities: 0`` would make a databaseless run look
    exactly like a broken one, which is the confusion the ``index`` stage's ``verified:
    null`` already exists to avoid.
    """
    set_ingest_dependencies(
        IngestDependencies(
            store=store,
            extractor=_FixedExtractor(),
            project=_RecordingProjector(ProjectionResult(skipped="STORES=off")),
        )
    )
    await _seed_tenant()
    document_id = await _chunked_document(client, parsed_artifact, store)

    with collect_stage_facts() as facts:
        await _handler(db, graph_stage, document_id, "graph")

    assert facts["projected_entities"] == 0
    assert facts["projection"] == "skipped: STORES=off"
    assert facts["entities"] > 0, "the extraction itself is unaffected and still recorded"


class _RecordingGraphIndexer:
    """Stands in for the graph vector index, recording what the stage handed it.

    The real publisher refuses to run in a test process for the reason the projector does
    — there is no scratch Qdrant, so a test that published would write into whatever node
    the developer was looking at. What is assertable here is the half the stage owns: that
    it reports the count the *store* confirmed, and what it does when the store cannot.
    """

    def __init__(self, result: GraphVectorResult | None = None) -> None:
        """Hold the outcome to return, and start with an empty call log."""
        self.calls: list[dict[str, object]] = []
        self._result = result

    async def __call__(
        self,
        entities,  # noqa: ANN001 - Sequence[Entity]
        relations,  # noqa: ANN001 - Sequence[Relation]
        *,
        tenant_value: str | None,
        source: str,
        extractor: str,
        entity_sources=None,  # noqa: ANN001 - Mapping[str, Sequence[str]]
        relation_sources=None,  # noqa: ANN001
    ) -> GraphVectorResult:
        """Record one publish and return the configured outcome."""
        self.calls.append(
            {
                "entities": list(entities),
                "relations": list(relations),
                "tenant_value": tenant_value,
                "source": source,
                "entity_sources": dict(entity_sources or {}),
                "relation_sources": dict(relation_sources or {}),
            }
        )
        return self._result or GraphVectorResult(entities=1, relations=0)


async def test_graph_indexes_the_extraction_for_the_arm_that_has_to_find_it(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """Neo4j is what the Graph screen draws; ``entities_vdb`` is what a query can reach.

    The measured gap was 156 nodes in the graph and 0 points in the entity collection, so
    hybrid recall's ``local`` arm — which matches a vector *first* and looks the name up
    in the graph second — returned nothing for every query ever asked. The stage now makes
    the third write, attributed to the same tenant and source as the other two, and
    reports the count the vector store confirmed rather than the count it sent.
    """
    indexer = _RecordingGraphIndexer(GraphVectorResult(entities=2, relations=1))
    set_ingest_dependencies(
        IngestDependencies(
            store=store,
            extractor=_FixedExtractor(),
            project=_RecordingProjector(),
            index_graph=indexer,
        )
    )
    await _seed_tenant()
    document_id = await _chunked_document(client, parsed_artifact, store)

    with collect_stage_facts() as facts:
        await _handler(db, graph_stage, document_id, "graph")

    assert len(indexer.calls) == 1
    call = indexer.calls[0]
    assert call["tenant_value"] == tenant_metadata_value(_TENANT)
    assert call["entities"], "nothing was handed to the graph index"
    # ``source_id`` is LightRAG's chunk-level provenance, and it is keyed by the same
    # normalised label the graph node is stored under — the string the ``local`` arm
    # carries from the vector back to the node.
    assert "Transformer" in call["entity_sources"]
    assert all(call["entity_sources"].values()), "an entity was attributed to no chunk"
    assert facts["entity_vectors"] == 2
    assert facts["relation_vectors"] == 1
    assert "graph_vectors" not in facts


async def test_an_unwritable_graph_index_degrades_the_arm_and_never_the_ingest(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """A failed vector publish is reported, not raised — and never reported as zero.

    The opposite call from the projection, and deliberately so. A projection that did not
    land leaves the entities in no place a person can see, so the stage fails. These
    vectors are an *index over* a graph that has already been verified present: the chunk
    rows, the embeddings, Neo4j and the dense index are all correct and the corpus is
    still searchable, so failing here would discard a wholly correct document because a
    second index blipped. ``None`` rather than ``0`` because "the index could not be
    asked" and "the arm has nothing to find" call for opposite responses.
    """
    set_ingest_dependencies(
        IngestDependencies(
            store=store,
            extractor=_FixedExtractor(),
            project=_RecordingProjector(),
            index_graph=_RecordingGraphIndexer(
                GraphVectorResult(
                    entities=None,
                    relations=None,
                    attempted_entities=2,
                    attempted_relations=1,
                    failed="connection refused",
                )
            ),
        )
    )
    await _seed_tenant()
    document_id = await _chunked_document(client, parsed_artifact, store)

    with collect_stage_facts() as facts:
        await _handler(db, graph_stage, document_id, "graph")

    assert facts["entity_vectors"] is None
    assert facts["relation_vectors"] is None
    assert facts["graph_vectors"] == "failed: connection refused"
    assert facts["projected_entities"] > 0, "the durable graph is unaffected"
