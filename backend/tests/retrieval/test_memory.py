"""Lite / no-database retrieval: in-memory backend + cache run fully offline."""

from __future__ import annotations

import pytest

from app.api.schemas import FusionMethod, RetrievalOrigin
from app.config import Settings
from app.retrieval.cache import SemanticCache
from app.retrieval.fusion import ORIGIN_METADATA_KEY
from app.retrieval.memory import (
    InMemoryKnowledgeBackend,
    InMemoryRedis,
    _local_embed,
    build_lite_retriever,
)
from app.retrieval.pipeline import RetrievalConfig, Retriever
from app.retrieval.protocols import MultiListBackend
from app.retrieval.spotlight import DATAMARK_TOKEN

from .conftest import RecordingComplete, SequenceEmbed


def test_stores_enabled_property():
    assert Settings(stores="on").stores_enabled is True
    assert Settings(stores="off").stores_enabled is False
    assert Settings(stores="OFF").stores_enabled is False
    assert Settings(stores="false").stores_enabled is False


async def test_in_memory_redis_parity():
    r = InMemoryRedis()
    assert await r.get("missing") is None
    await r.set("k", "v", ex=60)
    assert await r.get("k") == "v"
    await r.sadd("s", "a", "b")
    assert await r.smembers("s") == {"a", "b"}


async def test_backend_loads_corpus_and_recalls():
    backend = InMemoryKnowledgeBackend.from_corpus()
    assert backend._chunks, "adapter corpus should chunk into at least one chunk"

    # Query from a real chunk's own words so token overlap is guaranteed.
    sample = backend._chunks[0]
    query = " ".join(sample.text.split()[:8])
    recall = await backend.recall(query, top_k=5)

    assert recall.candidates, "a corpus-overlapping query should recall candidates"
    assert any(c.metadata.get("doc") == sample.doc_id for c in recall.candidates)
    # The graph slice is the real entity subgraph now: any nodes carry entity
    # kinds (never the retired "source" doc-chain) and edges never use "related".
    assert all(n.kind != "source" for n in recall.nodes)
    assert all(e.relation != "related" for e in recall.edges)


async def test_lite_retriever_runs_end_to_end_and_caches():
    """The full retrieve() path works with zero infrastructure (in-memory + fakes)."""
    backend = InMemoryKnowledgeBackend.from_corpus()
    cache = SemanticCache(InMemoryRedis(), ttl_seconds=60, similarity_threshold=0.95)
    complete = RecordingComplete('{"scores": [{"id": 0, "score": 9}]}')
    embed = SequenceEmbed([1.0, 0.0])
    retriever = Retriever(
        backend=backend,
        cache=cache,
        complete=complete,
        embed=embed,
        config=RetrievalConfig(recall_top_k=8, final_top_k=3),
    )

    query = " ".join(backend._chunks[0].text.split()[:8])
    first = await retriever.retrieve(query, persona="ops")
    assert first.cache_hit is False
    assert first.sources
    assert DATAMARK_TOKEN in first.answer_context  # spotlighted, no infra needed

    second = await retriever.retrieve(query, persona="ops")
    assert second.cache_hit is True  # served from the in-memory cache


def test_build_lite_retriever_is_databaseless():
    retriever = build_lite_retriever(Settings(stores="off"))
    assert isinstance(retriever.backend, InMemoryKnowledgeBackend)
    assert isinstance(retriever.cache._client, InMemoryRedis)


def test_local_embed_is_offline_deterministic_and_discriminative():
    # Same text → identical unit vector (deterministic, no gateway/network).
    a = _local_embed("the amazon river discharges more water than any other")
    assert a == _local_embed("the amazon river discharges more water than any other")
    assert sum(v * v for v in a) == pytest.approx(1.0)
    # Overlapping text is nearer than unrelated text (real semantic-ish signal).
    near = _local_embed("the amazon river discharges the most water")
    far = _local_embed("stock prices rose on quarterly earnings news")
    dot = lambda x, y: sum(p * q for p, q in zip(x, y, strict=True))  # noqa: E731
    assert dot(a, near) > dot(a, far)


def test_backend_is_multilist_and_splits_vector_and_graph():
    backend = InMemoryKnowledgeBackend.from_corpus()
    assert isinstance(backend, MultiListBackend)  # advertises recall_ranked


async def test_recall_ranked_returns_vector_and_graph_lists_offline():
    backend = InMemoryKnowledgeBackend.from_corpus()
    query = " ".join(backend._chunks[0].text.split()[:8])
    ranked = await backend.recall_ranked(query, top_k=6)

    origins = {o for rl in ranked.lists for o in rl.origins}
    assert origins == {RetrievalOrigin.VECTOR, RetrievalOrigin.GRAPH}
    assert any(rl.candidates for rl in ranked.lists)  # real candidates, no infra
    assert all(n.kind != "source" for n in ranked.nodes)


async def test_lite_result_carries_hybrid_provenance_offline():
    """Lite mode demonstrates genuine hybrid retrieval + honest provenance, offline."""
    backend = InMemoryKnowledgeBackend.from_corpus()
    cache = SemanticCache(InMemoryRedis(), ttl_seconds=60, similarity_threshold=0.985)
    complete = RecordingComplete('{"scores": [{"id": 0, "score": 9}]}')
    embed = SequenceEmbed([1.0, 0.0])
    retriever = Retriever(
        backend=backend,
        cache=cache,
        complete=complete,
        embed=embed,
        config=RetrievalConfig(recall_top_k=8, final_top_k=3),
    )

    query = " ".join(backend._chunks[0].text.split()[:8])
    result = await retriever.retrieve(query, persona="ops")

    assert result.cache_hit is False
    assert result.provenance.fusion is FusionMethod.RRF
    # At least the in-memory vector signal contributed; keyword/graph add to it.
    assert RetrievalOrigin.VECTOR in result.provenance.origins
    # Sources carry per-candidate origin tags from fusion.
    assert result.sources[0].metadata.get(ORIGIN_METADATA_KEY)
