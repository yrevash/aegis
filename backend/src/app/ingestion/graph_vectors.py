"""Index one document's extraction so LightRAG's graph-aware arm can find it.

The half of the graph stage that was still missing
--------------------------------------------------

:mod:`app.ingestion.graph_projection` put the extraction into Neo4j, which is what the
Graph screen draws and what ``GET /v1/graph`` reads. It is not what *retrieval* searches.
Hybrid recall's second arm (``local``) starts by querying LightRAG's ``entities_vdb`` and
only then looks the matched names up in the graph — so with that collection empty, a
perfectly complete Neo4j is unreachable from a query. Measured on the live deployment:
``lightrag_vdb_chunks`` 164 points, ``lightrag_vdb_entities`` **0**,
``lightrag_vdb_relationships`` **0**, against a Neo4j holding 156 nodes and 141 edges for
the same workspace. Half of hybrid retrieval was inert and every arm reported success.

Nothing wrote those vectors because nothing was ever going to:
:meth:`aegis.retrieval.lightrag_backend.LightRAGBackend.publish_vectors` deliberately
bypasses ``ainsert`` (see its docstring — 36 of 37 chunks rejected as duplicate documents,
73 ``FAILED`` rows in ``lightrag_doc_status``), and ``ainsert`` is the only thing in
LightRAG that writes entity and relation vectors. This module does for them what
:mod:`aegis.retrieval.chunk_index` does for chunks: builds the points under LightRAG's own
addressing and payload contract and publishes them directly.

Derived from the same rows the graph was built from, on purpose
---------------------------------------------------------------

The points are shaped from :func:`app.ingestion.graph_projection.projection_rows` — the
*same pure function*, called with the same arguments, that produced the Neo4j rows. That
is the load-bearing detail. ``local`` matches a vector, reads ``entity_name`` off it and
looks that name up in the graph; a name that differs from the node's ``entity_id`` by so
much as a normalised space is dropped by ``_get_node_data`` without comment. Deriving both
from one function makes that class of drift unrepresentable rather than merely tested for.

It is also why this runs only when the projection actually landed: an entity vector whose
node is not in the graph is a point that matches and then contributes nothing, which is
the same silence with a higher ``points_count``.

Why a failure here does not fail the ingest — and is not silent either
----------------------------------------------------------------------

The ``graph`` stage already has a considered stance on this, and it points the other way
for the projection: a projection that was *owed* and did not land raises
``GraphNotProjected``, because the entities would then exist in no place a person can see
and recording ``completed`` is how that stayed invisible. The vectors are not that case.
When this fails, every durable store is still correct — the extraction is on
``chunks.meta``, the graph is in Neo4j and was verified there, the chunk vectors are in
the dense index and were verified there — the corpus is searchable, the Graph screen
draws, and what is lost is one arm's *reach* into a graph that is fully present. That is a
degradation of an index which can be rebuilt from durable state at any time, not a success
that cannot be shown; failing the ingest would discard a wholly correct document because a
second index blipped.

So the failure is *reported* instead: the stage's facts carry ``entity_vectors`` /
``relation_vectors`` as the count **the store confirmed holding**, ``None`` when it could
not be established, and a ``graph_vectors: skipped: …`` / ``failed: …`` note beside them.
``None`` and ``0`` are never collapsed, for the reason ``index`` already keeps ``verified:
null`` distinct from ``verified: 0``: "the audit could not run" and "the audit found
nothing" call for opposite responses.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from aegis.retrieval.graph_index import (
    DEFAULT_ENTITY_COLLECTION,
    DEFAULT_RELATION_COLLECTION,
    EntityPoint,
    RelationPoint,
    confirmed_point_keys,
    publish_entity_points,
    publish_relation_points,
)

from app.ingestion.graph_projection import projection_rows
from app.ingestion.vector_index import open_qdrant_client
from app.retrieval.protocols import EmbedFn

logger = logging.getLogger(__name__)

__all__ = ["GraphVectorResult", "publish_document_graph_vectors"]

#: Texts per embedding request. The same bound the ``embed`` stage uses, for the same
#: reason: it is about round-trips and about not building one request the provider will
#: reject on size. An entity's text is a name plus a one-line description, so this is a
#: very small request by comparison with a batch of chunks.
_EMBED_BATCH = 64


@dataclass(frozen=True, slots=True)
class GraphVectorResult:
    """What reached the graph vector index, counted by reading it back.

    Attributes:
        entities: Entity points the store confirmed holding, or ``None`` when that could
            not be established. Never a fabricated zero — see the module docstring.
        relations: Relation points confirmed, likewise.
        attempted_entities: Distinct entity points built and sent.
        attempted_relations: Distinct relation points built and sent.
        skipped: Why nothing was attempted, or ``None``. A deployment with no Qdrant and
            a deployment whose write failed are different facts.
        failed: Why an attempt did not complete, or ``None``.
    """

    entities: int | None = 0
    relations: int | None = 0
    attempted_entities: int = 0
    attempted_relations: int = 0
    skipped: str | None = None
    failed: str | None = None

    @property
    def complete(self) -> bool:
        """True when everything attempted was confirmed present afterwards."""
        return (
            self.entities is not None
            and self.relations is not None
            and self.entities >= self.attempted_entities
            and self.relations >= self.attempted_relations
        )


def _unavailable() -> str | None:
    """Return why the graph vector index must not be written, or ``None`` when it may be.

    The same three refusals :func:`app.ingestion.graph_projection._unavailable` makes, for
    the same reasons and in the same order, so a deployment that skips one skips both
    rather than half-indexing itself:

    * **A test process.** There is no scratch Qdrant, so a test that published here would
      write into whatever node the developer was looking at. The suite drives this surface
      through in-process Qdrant instances it owns instead.
    * **Databaseless mode.** ``STORES=off`` has no vector store to publish into.
    * **No configured Qdrant.** ``QDRANT_URL`` empty.
    """
    if "PYTEST_CURRENT_TEST" in os.environ:
        return "test process (no scratch Qdrant to isolate the write)"
    from app.config import get_settings  # noqa: PLC0415 - runtime-only dependency

    settings = get_settings()
    if not settings.stores_enabled:
        return "STORES=off (this deployment has no vector store)"
    if not (settings.qdrant_url or "").strip():
        return "QDRANT_URL is not configured"
    return None


async def _embedded(
    contents: Sequence[str], embed: EmbedFn
) -> list[Sequence[float]]:
    """Embed ``contents`` in batches and return one vector per text, in order.

    Args:
        contents: The texts LightRAG would have embedded.
        embed: The platform's embedding function — the same one the ``embed`` stage
            resolves, so the graph vectors and the chunk vectors are produced by one
            provider at one width. Two widths in one collection is a write that succeeds
            and a query that never matches.

    Returns:
        The vectors, positionally aligned with ``contents``.

    Raises:
        RuntimeError: If the embedder returns a different number of vectors than it was
            given texts. Pairing by position after that would attach each entity's vector
            to a different entity, which no later check could detect.
    """
    vectors: list[Sequence[float]] = []
    for start in range(0, len(contents), _EMBED_BATCH):
        batch = contents[start : start + _EMBED_BATCH]
        got = await embed(list(batch))
        if len(got) != len(batch):
            raise RuntimeError(
                f"the embedder returned {len(got)} vector(s) for {len(batch)} graph "
                "element(s); pairing them by position would give each element another "
                "element's vector"
            )
        vectors.extend(got)
    return vectors


async def publish_document_graph_vectors(
    entities: Sequence[Any],
    relations: Sequence[Any],
    *,
    tenant_value: str | None,
    source: str,
    extractor: str,
    embed: EmbedFn,
    entity_sources: Mapping[str, Sequence[str]] | None = None,
    relation_sources: Mapping[tuple[str, str], Sequence[str]] | None = None,
) -> GraphVectorResult:
    """Publish one document's entities and relations as vectors, and verify they landed.

    Idempotent: every point id is ``sha256(workspace + md5-seeded LightRAG key)``, so a
    re-ingest of unchanged text overwrites the same rows rather than adding to them. A
    document that yielded no entities publishes nothing and reports zero — that is an
    empty extraction, not a failure, and it is the same outcome the projection reports for
    it.

    Args:
        entities: Every entity the document's chunks yielded (duplicates fine).
        relations: Every relation, referring to entities by their extractor id.
        tenant_value: The owning tenant's metadata value, or ``None`` for the shared
            corpus. Tags every ``file_path``, exactly as it does for the graph and for the
            chunk vectors.
        source: The document's source name, as the ``index`` stage tags it.
        extractor: The extractor's honest ``name`` — it is part of the description that
            gets embedded, so a graph extracted two different ways is two different
            vectors rather than one silently overwritten.
        embed: The embedding function to use.
        entity_sources: Chunk ids per normalised entity label, for ``source_id``.
        relation_sources: Chunk ids per ``(src label, tgt label)`` pair, likewise.

    Returns:
        What was attempted and what the store confirmed holding. Never raises for an
        unwritable store: see the module docstring for why this degrades and reports
        rather than failing the ingest.
    """
    reason = _unavailable()
    if reason is not None:
        return GraphVectorResult(entities=None, relations=None, skipped=reason)

    # The same rows, from the same function, that built the graph. See the module
    # docstring: this is what makes the vdb's entity_name and the node's entity_id one
    # string rather than two that agree today.
    node_rows, edge_rows, _dropped = projection_rows(
        entities,
        relations,
        tenant_value=tenant_value,
        source=source,
        extractor=extractor,
        entity_sources=entity_sources,
        relation_sources=relation_sources,
    )
    if not node_rows:
        return GraphVectorResult()

    # ``source_id`` is read off the row rather than joined a second time here. The graph
    # node and this payload have to carry the same string — the node's copy is what the
    # reader walks — and two joins of the same mapping is one edit away from being two
    # different strings.
    entity_points = [
        EntityPoint(
            name=row["entity_id"],
            description=row["description"],
            file_path=row["file_path"],
            source_id=row["source_id"],
        )
        for row in node_rows
    ]
    relation_points = [
        RelationPoint(
            src=row["src"],
            tgt=row["tgt"],
            keywords=row["keywords"],
            description=row["description"],
            file_path=row["file_path"],
            source_id=row["source_id"],
        )
        for row in edge_rows
    ]

    try:
        split = len(entity_points)
        contents = [point.content for point in (*entity_points, *relation_points)]
        vectors = await _embedded(contents, embed)
        entity_points = [
            replace(point, vector=vector)
            for point, vector in zip(entity_points, vectors[:split], strict=True)
        ]
        relation_points = [
            replace(point, vector=vector)
            for point, vector in zip(relation_points, vectors[split:], strict=True)
        ]
        client = open_qdrant_client()
        written_entities = publish_entity_points(client, entity_points)
        written_relations = publish_relation_points(client, relation_points)
        present_entities = confirmed_point_keys(
            client,
            [point.key for point in entity_points],
            collection=DEFAULT_ENTITY_COLLECTION,
        )
        present_relations = confirmed_point_keys(
            client,
            [point.key for point in relation_points],
            collection=DEFAULT_RELATION_COLLECTION,
        )
    except Exception as exc:  # noqa: BLE001 - every failure here is the same outcome
        logger.exception(
            "could not index the %d entities and %d relations of '%s' for the graph "
            "arm; the graph itself is unaffected and the arm will find nothing for them",
            len(entity_points),
            len(relation_points),
            source,
        )
        return GraphVectorResult(
            entities=None,
            relations=None,
            attempted_entities=len(entity_points),
            attempted_relations=len(relation_points),
            # ``str(TimeoutError())`` is the empty string, and an empty reason reads
            # as no reason at all. The class name is the least this can honestly say.
            failed=str(exc) or type(exc).__name__,
        )

    logger.info(
        "published %d entity and %d relation vector(s) for '%s'; the index reports "
        "holding %d and %d",
        written_entities,
        written_relations,
        source,
        len(present_entities),
        len(present_relations),
    )
    return GraphVectorResult(
        entities=len(present_entities),
        relations=len(present_relations),
        attempted_entities=written_entities,
        attempted_relations=written_relations,
    )
