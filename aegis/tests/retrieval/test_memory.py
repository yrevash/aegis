"""Lite / no-database retrieval: in-memory backend + cache run fully offline."""

from __future__ import annotations

import pytest

from aegis.retrieval.cache import SemanticCache
from aegis.retrieval.fusion import ORIGIN_METADATA_KEY
from aegis.retrieval.memory import (
    InMemoryKnowledgeBackend,
    InMemoryRedis,
    _local_embed,
    build_lite_retriever,
)
from aegis.retrieval.pipeline import RetrievalConfig, Retriever
from aegis.retrieval.protocols import MultiListBackend
from aegis.retrieval.spotlight import DATAMARK_TOKEN
from aegis.retrieval.types import FusionMethod, RetrievalOrigin

from .conftest import RecordingComplete, SequenceEmbed

# A small, overlapping, caller-supplied corpus standing in for a host app's real
# knowledge base — this package has no bundled corpus of its own (see
# `InMemoryKnowledgeBackend.from_corpus`'s docstring).
_SAMPLE_DOCS = [
    (
        "kb_refund_process",
        "Refunds are issued to the original payment method within five to seven "
        "business days. Verify the customer's identity and confirm the charge on "
        "the invoice before issuing a refund. Enterprise-tier customers may "
        "request an expedited refund approved by a senior agent.",
    ),
    (
        "policy_escalation",
        "Every request carries a resolution deadline based on its priority. If a "
        "request is at risk of breaching its deadline, escalate it to a senior "
        "agent. Enterprise customers are escalated one tier earlier than standard, "
        "and their refund requests are prioritised accordingly.",
    ),
    (
        "runbook_login_failures",
        "When customers report login failures returning HTTP 500, first check the "
        "auth service status page for an active incident. If the error persists, "
        "collect the request id and escalate to a senior technical agent.",
    ),
]


def _backend() -> InMemoryKnowledgeBackend:
    return InMemoryKnowledgeBackend.from_corpus(docs=_SAMPLE_DOCS)


def test_stores_enabled_defaults_true_and_is_overridable():
    assert RetrievalConfig().stores_enabled is True
    assert RetrievalConfig(stores_enabled=False).stores_enabled is False


async def test_in_memory_redis_parity():
    r = InMemoryRedis()
    assert await r.get("missing") is None
    await r.set("k", "v", ex=60)
    assert await r.get("k") == "v"
    await r.sadd("s", "a", "b")
    assert await r.smembers("s") == {"a", "b"}


async def test_from_corpus_with_no_input_is_honestly_empty():
    backend = InMemoryKnowledgeBackend.from_corpus()
    assert backend._chunks == []


async def test_backend_loads_supplied_corpus_and_recalls():
    backend = _backend()
    assert backend._chunks, "the supplied corpus should chunk into at least one chunk"

    # Query from a real chunk's own words so token overlap is guaranteed.
    sample = backend._chunks[0]
    query = " ".join(sample.text.split()[:8])
    recall = await backend.recall(query, top_k=5)

    assert recall.candidates, "a corpus-overlapping query should recall candidates"
    assert any(c.metadata.get("doc") == sample.doc_id for c in recall.candidates)
    assert recall.nodes  # at least one source node for the viz
    assert recall.nodes[0].kind == "source"


async def test_lite_retriever_runs_end_to_end_and_caches():
    """The full retrieve() path works with zero infrastructure (in-memory + fakes)."""
    backend = _backend()
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
    retriever = build_lite_retriever(
        complete=RecordingComplete("{}"), embed=SequenceEmbed([1.0, 0.0])
    )
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
    backend = _backend()
    assert isinstance(backend, MultiListBackend)  # advertises recall_ranked


async def test_recall_ranked_returns_vector_and_graph_lists_offline():
    backend = _backend()
    query = " ".join(backend._chunks[0].text.split()[:8])
    ranked = await backend.recall_ranked(query, top_k=6)

    origins = {o for rl in ranked.lists for o in rl.origins}
    assert origins == {RetrievalOrigin.VECTOR, RetrievalOrigin.GRAPH}
    assert any(rl.candidates for rl in ranked.lists)  # real candidates, no infra
    assert ranked.nodes and ranked.nodes[0].kind == "source"


async def test_lite_result_carries_hybrid_provenance_offline():
    """Lite mode demonstrates genuine hybrid retrieval + honest provenance, offline."""
    backend = _backend()
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
