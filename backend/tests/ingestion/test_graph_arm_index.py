"""The graph arm's vector index must speak LightRAG's dialect, or it is not an index.

One claim, one failure mode, and the failure mode is the reason this file exists.

The claim: an entity this platform extracted can be *found* by LightRAG's own graph-aware
query. Hybrid recall's ``local`` arm matches a query against ``entities_vdb`` and only
then looks the matched names up in Neo4j, so a graph with no entity vectors is a graph no
query can reach. That was the live state — ``lightrag_vdb_chunks`` 164 points,
``lightrag_vdb_entities`` 0, against a Neo4j holding 156 nodes for the same workspace —
and every arm reported success throughout.

The failure mode is not "nothing was written". It is **points that exist and never
match**: a key seeded slightly differently, a content string assembled in another order, a
workspace the reader does not filter on. Every one of those writes cleanly, raises
``points_count``, and leaves retrieval exactly as empty as before — which is the shape of
silence the whole module was written to end, wearing a green tick.

So the tests below are the contract and the round trip, not the branches:

* :func:`test_the_keys_and_contents_are_lightrags_own` compares this module's seeds and
  embedded text against ``lightrag``'s own functions. Both sides of the round-trip test
  would shift together if this drifted, so only a comparison against the real thing can
  catch it — the same reason ``test_point_id_matches_lightrag_exactly`` exists for chunks.
* :func:`test_a_published_entity_is_found_by_a_lightrag_shaped_query` publishes into a
  **real** Qdrant (``qdrant_client``'s in-process mode, the same implementation the server
  exposes) and then reads it back the exact way ``QdrantVectorDBStorage.query`` does —
  same filter, same payload key — because "the point is in the collection" was already
  true of a collection nothing could search.
* :func:`test_republishing_the_same_extraction_leaves_one_point_each` pins idempotency,
  which is what makes a re-ingest safe to run on a hunch.
* :func:`test_a_missing_collection_is_refused_rather_than_created` pins the one refusal:
  a collection created here at a guessed width is an index no query can use.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator

import pytest
from aegis.retrieval.chunk_index import lightrag_point_id
from aegis.retrieval.graph_index import (
    DEFAULT_ENTITY_COLLECTION,
    DEFAULT_RELATION_COLLECTION,
    EntityPoint,
    RelationPoint,
    confirmed_point_keys,
    publish_entity_points,
    publish_relation_points,
)
from lightrag.kg.qdrant_impl import (
    compute_mdhash_id_for_qdrant,
    workspace_filter_condition,
)
from lightrag.utils import compute_mdhash_id
from qdrant_client import QdrantClient, models

#: Small on purpose: this file is about *which* points exist and whether a
#: LightRAG-shaped query finds them, not about embedding quality.
_DIM = 2

_ENTITIES = "test_vdb_entities"
_RELATIONS = "test_vdb_relationships"

_WORKSPACE = "run-under-test"


def _vector(text: str) -> list[float]:
    """Return the one vector this file's embedder will ever return for ``text``."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [
        int.from_bytes(digest[0:8], "big") / 2**64,
        int.from_bytes(digest[8:16], "big") / 2**64,
    ]


def _entity(name: str, description: str) -> EntityPoint:
    """One entity point, embedded the way the publisher embeds it."""
    point = EntityPoint(
        name=name, description=description, file_path="t1::refund-policy.pdf"
    )
    return EntityPoint(
        name=name,
        description=description,
        file_path=point.file_path,
        source_id="t1:abcd1234",
        vector=_vector(point.content),
    )


def _relation(src: str, tgt: str) -> RelationPoint:
    """One relation point, embedded the way the publisher embeds it."""
    point = RelationPoint(
        src=src,
        tgt=tgt,
        keywords="escalates to",
        description="escalates to",
        file_path="t1::refund-policy.pdf",
    )
    return RelationPoint(
        src=src,
        tgt=tgt,
        keywords=point.keywords,
        description=point.description,
        file_path=point.file_path,
        source_id="t1:abcd1234",
        vector=_vector(point.content),
    )


@pytest.fixture
def graph_index() -> Iterator[QdrantClient]:
    """An in-process Qdrant holding both graph collections at the test's width."""
    client = QdrantClient(":memory:")
    for collection in (_ENTITIES, _RELATIONS):
        client.create_collection(
            collection_name=collection,
            vectors_config=models.VectorParams(
                size=_DIM, distance=models.Distance.COSINE
            ),
        )
    yield client
    client.close()


def test_the_keys_and_contents_are_lightrags_own() -> None:
    """The seeds, the embedded text and the point id are LightRAG's, not ours.

    Confirmed by construction rather than trusted: every one of these is replicated in
    :mod:`aegis.retrieval.graph_index` because importing ``lightrag`` at runtime costs
    seconds and drags in the whole storage stack. A drift in any of them writes points
    that exist and never match — invisible to every other test here, because both sides of
    their comparison would shift together.
    """
    name = "Northwind Support"
    description = "org extracted by the llm extractor"
    entity = EntityPoint(name=name, description=description, file_path="t1::p.pdf")
    assert entity.key == compute_mdhash_id(name, prefix="ent-")
    assert entity.content == f"{name}\n{description}"

    # LightRAG sorts the endpoints for the vector record's identity while the graph edge
    # keeps the stated direction (operate.py:3157-3175), so one relation is one record
    # whichever way round the extractor phrased it.
    src, tgt = "team lead", "Northwind Support"
    relation = RelationPoint(
        src=src, tgt=tgt, keywords="escalates to", description="d", file_path="t1::p.pdf"
    )
    assert relation.key == compute_mdhash_id(tgt + src, prefix="rel-")
    assert relation.key == RelationPoint(
        src=tgt, tgt=src, keywords="escalates to", description="d", file_path="t1::p.pdf"
    ).key
    assert relation.content == f"escalates to\t{tgt}\n{src}\nd"

    assert lightrag_point_id(
        entity.key, workspace=_WORKSPACE
    ) == compute_mdhash_id_for_qdrant(entity.key, prefix=_WORKSPACE)


def test_a_published_entity_is_found_by_a_lightrag_shaped_query(graph_index) -> None:
    """The point is not merely present: LightRAG's own read finds it and can use it.

    ``_get_node_data`` takes ``entity_name`` off each hit and looks *that string* up in
    the graph, so the assertion is on the payload field the arm actually consumes rather
    than on ``points_count`` — a count was already true of the collection that returned
    nothing for five months.
    """
    entity = _entity("Northwind Support", "org extracted by the llm extractor")
    relation = _relation("team lead", "Northwind Support")
    published = publish_entity_points(
        graph_index, [entity], collection=_ENTITIES, workspace=_WORKSPACE
    )
    publish_relation_points(
        graph_index, [relation], collection=_RELATIONS, workspace=_WORKSPACE
    )
    assert published == 1

    hits = graph_index.query_points(
        collection_name=_ENTITIES,
        query=_vector(entity.content),
        limit=5,
        with_payload=True,
        query_filter=models.Filter(must=[workspace_filter_condition(_WORKSPACE)]),
    ).points
    assert [hit.payload["entity_name"] for hit in hits] == ["Northwind Support"]

    payload = hits[0].payload
    assert set(payload) == {
        "id",
        "workspace_id",
        "created_at",
        "entity_name",
        "source_id",
        "content",
        "file_path",
    }
    # The owner tag travels on the vector too, the way it does for chunks: the graph
    # arm's read-time attribution comes off the graph node, and a payload that could not
    # name an owner would leave a future reader nothing to refuse on.
    assert payload["file_path"] == "t1::refund-policy.pdf"
    assert payload["content"] == entity.content

    relation_hits = graph_index.query_points(
        collection_name=_RELATIONS,
        query=_vector(relation.content),
        limit=5,
        with_payload=True,
        query_filter=models.Filter(must=[workspace_filter_condition(_WORKSPACE)]),
    ).points
    assert (relation_hits[0].payload["src_id"], relation_hits[0].payload["tgt_id"]) == (
        "Northwind Support",
        "team lead",
    )


def test_republishing_the_same_extraction_leaves_one_point_each(graph_index) -> None:
    """A re-ingest of unchanged text overwrites the same rows rather than adding to them.

    The point id is derived from the entity's name under the workspace, so this holds by
    construction — and it is asserted because the alternative failure is quiet: a
    collection that grows on every re-ingest still answers queries, just with duplicated
    neighbourhoods and a slowly rotting cost profile.
    """
    entities = [_entity("Northwind Support", "org extracted by the llm extractor")]
    for _ in range(2):
        publish_entity_points(
            graph_index, entities, collection=_ENTITIES, workspace=_WORKSPACE
        )
    assert graph_index.count(_ENTITIES, exact=True).count == 1

    confirmed = confirmed_point_keys(
        graph_index,
        [point.key for point in entities],
        collection=_ENTITIES,
        workspace=_WORKSPACE,
    )
    assert confirmed == {entities[0].key}
    # Asking under a workspace nothing was written under is not an error and is not a
    # hit: the workspace is inside the hashed point id, so a mismatch is simply a
    # different row — which is exactly what makes it worth pinning.
    assert not confirmed_point_keys(
        graph_index,
        [point.key for point in entities],
        collection=_ENTITIES,
        workspace="another-run",
    )


def test_a_missing_collection_is_refused_rather_than_created(graph_index) -> None:
    """Publishing into a collection that does not exist raises instead of guessing.

    The collection's dimensionality belongs to whoever created it. Creating it here at a
    guessed width is how a 3072-dim corpus ends up in a 1536-dim collection: every write
    succeeds and every query is rejected or, worse, silently useless.
    """
    with pytest.raises(LookupError):
        publish_entity_points(
            graph_index,
            [_entity("Northwind Support", "org")],
            collection="lightrag_vdb_entities_that_do_not_exist",
            workspace=_WORKSPACE,
        )
    # And the defaults name the collections the running deployment actually serves from.
    assert DEFAULT_ENTITY_COLLECTION == "lightrag_vdb_entities"
    assert DEFAULT_RELATION_COLLECTION == "lightrag_vdb_relationships"
