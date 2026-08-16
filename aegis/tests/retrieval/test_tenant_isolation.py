"""Cross-tenant isolation of the retrieval path.

These tests exist because the leak they cover was real: ``retrieve`` carried no tenant,
so two tenants asking the same question shared a cache entry — including the retrieved
passages. Each test below pins one link of the chain that now prevents that:

* the contract cannot be called unscoped at all;
* the exact and semantic cache tiers are partitioned per tenant, not filtered;
* a corpus-version bump invalidates one tenant's entries and nobody else's;
* the backend's vector, keyword and graph arms all apply the same tenant predicate;
* a null tenant reads the shared corpus and is never a wildcard.
"""

from __future__ import annotations

import inspect

import pytest

from aegis.retrieval.cache import SemanticCache
from aegis.retrieval.corpus import bump_corpus_version, corpus_version, reset_corpus_versions
from aegis.retrieval.memory import InMemoryKnowledgeBackend
from aegis.retrieval.models import RetrievalResult, Source
from aegis.retrieval.pipeline import RetrievalConfig, Retriever
from aegis.retrieval.types import (
    TENANT_METADATA_KEY,
    RetrievalOrigin,
    RetrievalScope,
    tenant_metadata_value,
)
from aegis.retrieval.vector_store import ChromaVectorStore

from .conftest import FakeBackend, FakeRedis, RecordingComplete, SequenceEmbed, make_recall

_ACME = RetrievalScope(tenant_id=1)
_GLOBEX = RetrievalScope(tenant_id=2)
_SHARED = RetrievalScope(tenant_id=None)


@pytest.fixture(autouse=True)
def _clean_corpus_versions():
    """Keep the process-wide corpus-version counters out of neighbouring tests."""
    reset_corpus_versions()
    yield
    reset_corpus_versions()


def _result(text: str) -> RetrievalResult:
    return RetrievalResult(answer_context=text, sources=[Source(id="s", text=text)])


def _cache(threshold: float = 0.95) -> SemanticCache:
    return SemanticCache(FakeRedis(), ttl_seconds=60, similarity_threshold=threshold)


def _retriever(backend=None) -> Retriever:
    """Build a retriever over canned fakes (no gateway, no stores)."""
    return Retriever(
        backend=backend or FakeBackend(make_recall()),
        cache=SemanticCache(FakeRedis(), ttl_seconds=60, similarity_threshold=0.985),
        complete=RecordingComplete('{"scores": [{"id": 0, "score": 9}]}'),
        embed=SequenceEmbed([1.0, 0.0]),
        config=RetrievalConfig(recall_top_k=8, final_top_k=3),
    )


# ─────────────────────────────────────────────────────────────── the contract


def test_retrieve_requires_a_scope_with_no_default():
    """``scope`` is required: a defaulted one is how the original leak happened."""
    param = inspect.signature(Retriever.retrieve).parameters["scope"]
    assert param.default is inspect.Parameter.empty
    assert param.kind is inspect.Parameter.KEYWORD_ONLY


def test_ingest_requires_a_scope_with_no_default():
    """The write side is scoped too — otherwise the read-side predicate matches nothing."""
    param = inspect.signature(Retriever.ingest).parameters["scope"]
    assert param.default is inspect.Parameter.empty
    assert param.kind is inspect.Parameter.KEYWORD_ONLY


async def test_calling_retrieve_without_a_scope_raises():
    with pytest.raises(TypeError):
        await _retriever().retrieve("why is the sky blue?")


# ───────────────────────────────────────────────────────── cache partitioning


async def test_exact_cache_keys_differ_across_tenants():
    """The same query under two tenants is two entries, and neither can reach the other."""
    cache = _cache()
    await cache.set("what is our refund policy?", _ACME, [1.0, 0.0], _result("acme answer"))

    assert await cache.get_exact("what is our refund policy?", _GLOBEX) is None
    hit = await cache.get_exact("what is our refund policy?", _ACME)
    assert hit is not None
    assert hit.answer_context == "acme answer"


async def test_semantic_tier_is_partitioned_not_filtered():
    """Tenant B's index set does not even contain tenant A's entry.

    The assertion is on the *keyspace*, not just the returned result: a filtered
    implementation would still have loaded and compared A's stored embedding, and one
    missing guard would then serve it.
    """
    redis = FakeRedis()
    cache = SemanticCache(redis, ttl_seconds=60, similarity_threshold=0.5)
    await cache.set("q", _ACME, [1.0, 0.0], _result("acme answer"))

    assert await cache.get_semantic([1.0, 0.0], _GLOBEX) is None
    acme_index = cache._index_key(_ACME)
    globex_index = cache._index_key(_GLOBEX)
    assert acme_index != globex_index
    assert redis.sets.get(acme_index)  # A's entry is indexed...
    assert not redis.sets.get(globex_index)  # ...and B's search space is empty


async def test_pipeline_does_not_serve_one_tenants_result_to_another():
    """End-to-end through `Retriever`: tenant B's identical question is a miss."""
    backend = FakeBackend(make_recall())
    retriever = _retriever(backend)

    first = await retriever.retrieve("why is the sky blue?", scope=_ACME)
    assert first.cache_hit is False
    recalls_after_first = backend.recall_calls

    second = await retriever.retrieve("why is the sky blue?", scope=_GLOBEX)
    assert second.cache_hit is False, "tenant B must not be served tenant A's cached result"
    assert backend.recall_calls == recalls_after_first + 1  # B ran its own retrieval

    # ...while a repeat for tenant A still hits, so partitioning did not break caching.
    again = await retriever.retrieve("why is the sky blue?", scope=_ACME)
    assert again.cache_hit is True


async def test_null_tenant_is_not_a_wildcard_in_the_cache():
    """An unscoped lookup cannot read a tenant's entry, in either direction."""
    cache = SemanticCache(FakeRedis(), ttl_seconds=60, similarity_threshold=0.5)
    await cache.set("q", _ACME, [1.0, 0.0], _result("acme answer"))

    assert await cache.get_exact("q", _SHARED) is None
    assert await cache.get_semantic([1.0, 0.0], _SHARED) is None

    await cache.set("q", _SHARED, [1.0, 0.0], _result("shared answer"))
    assert await cache.get_exact("q", _SHARED) is not None
    acme_hit = await cache.get_exact("q", _ACME)
    assert acme_hit is not None
    assert acme_hit.answer_context == "acme answer"  # unchanged by the shared write


async def test_a_foreign_entry_in_a_partition_raises_rather_than_being_skipped():
    """The stored-scope tripwire fails loud; a silent skip would hide a broken keyspace."""
    redis = FakeRedis()
    cache = SemanticCache(redis, ttl_seconds=60, similarity_threshold=0.5)
    await cache.set("q", _ACME, [1.0, 0.0], _result("acme answer"))
    # Forge the corruption a digest collision would produce: A's entry, B's partition.
    acme_key = next(iter(redis.sets[cache._index_key(_ACME)]))
    redis.kv[cache._entry_key("q", _GLOBEX)] = redis.kv[acme_key]

    with pytest.raises(RuntimeError, match="different scope"):
        await cache.get_exact("q", _GLOBEX)


# ─────────────────────────────────────────────────────────────── corpus version


async def test_corpus_version_bump_invalidates_only_that_tenant():
    """Ingesting for one tenant must not serve them a pre-ingest answer, nor cost others theirs."""
    cache = _cache()
    acme_v0 = RetrievalScope(tenant_id=1, corpus_version=corpus_version(1))
    globex_v0 = RetrievalScope(tenant_id=2, corpus_version=corpus_version(2))
    await cache.set("q", acme_v0, [1.0, 0.0], _result("pre-upload answer"))
    await cache.set("q", globex_v0, [1.0, 0.0], _result("globex answer"))

    bump_corpus_version(1)
    acme_v1 = RetrievalScope(tenant_id=1, corpus_version=corpus_version(1))

    assert await cache.get_exact("q", acme_v1) is None  # stale entry unreachable
    assert await cache.get_exact("q", globex_v0) is not None  # neighbour untouched


def test_corpus_version_starts_at_zero_and_is_per_tenant():
    assert corpus_version(1) == 0
    assert bump_corpus_version(1) == 1
    assert corpus_version(1) == 1
    assert corpus_version(2) == 0
    assert corpus_version(None) == 0


# ──────────────────────────────────────────────────────── scope value object


def test_partition_key_is_injective_for_colliding_looking_scopes():
    """Distinct scopes must never share a key — a collision *is* a cross-tenant hit."""
    keys = {
        RetrievalScope(tenant_id=None).partition_key(),
        RetrievalScope(tenant_id=1).partition_key(),
        RetrievalScope(tenant_id=12, persona=None).partition_key(),
        RetrievalScope(tenant_id=1, persona="2").partition_key(),
        RetrievalScope(tenant_id=None, persona="null").partition_key(),
        RetrievalScope(tenant_id=1, corpus_version=1).partition_key(),
    }
    assert len(keys) == 6


def test_visible_tenant_values_never_widen_a_null_scope():
    assert _SHARED.visible_tenant_values() == [None]
    assert set(_ACME.visible_tenant_values()) == {tenant_metadata_value(1), None}
    assert tenant_metadata_value(2) not in _ACME.visible_tenant_values()


# ──────────────────────────────────────────────────────────── backend filters


def _tenant_chunks(retriever: Retriever) -> list[str]:
    return [c.metadata[TENANT_METADATA_KEY] for c in retriever.backend.ingested]


async def test_ingest_stamps_the_owning_tenant_on_every_chunk():
    retriever = _retriever()
    await retriever.ingest([{"id": "a", "text": "Acme refunds take five days."}], scope=_ACME)
    assert _tenant_chunks(retriever) == [tenant_metadata_value(1)]


async def test_ingest_ledger_is_per_tenant_so_two_tenants_can_hold_one_document():
    """The same document ingested by two tenants is two rows, not a deduped one."""
    retriever = _retriever()
    doc = [{"id": "a", "text": "Refunds are issued to the original payment method."}]

    first = await retriever.ingest(doc, scope=_ACME)
    second = await retriever.ingest(doc, scope=_GLOBEX)
    third = await retriever.ingest(doc, scope=_ACME)  # genuine re-ingest

    assert first.chunks_written == second.chunks_written == 1
    assert third.chunks_written == 0 and third.chunks_duplicate == 1
    ids = [c.id for c in retriever.backend.ingested]
    assert len(set(ids)) == 2, "per-tenant rows must not share a vector-store primary key"


async def _lite_backend() -> InMemoryKnowledgeBackend:
    """Build one shared embedded-Chroma backend holding two tenants' + shared chunks."""
    backend = InMemoryKnowledgeBackend([], vector_store=ChromaVectorStore.local())
    retriever = Retriever(
        backend=backend,
        cache=SemanticCache(FakeRedis(), ttl_seconds=60, similarity_threshold=0.985),
        complete=RecordingComplete('{"scores": []}'),
        embed=SequenceEmbed([1.0, 0.0]),
        config=RetrievalConfig(),
    )
    await retriever.ingest(
        [{"id": "acme", "text": "Acme issues refunds within five business days."}],
        scope=_ACME,
    )
    await retriever.ingest(
        [{"id": "globex", "text": "Globex issues refunds within five business days."}],
        scope=_GLOBEX,
    )
    await retriever.ingest(
        [{"id": "handbook", "text": "Refunds are issued to the original payment method."}],
        scope=_SHARED,
    )
    return backend


async def test_vector_arm_filters_by_tenant_in_the_chroma_query():
    """One embedded store, three owners: a tenant sees its own rows plus the shared ones."""
    backend = await _lite_backend()
    ranked = await backend.recall_ranked(
        "refunds issued within business days", top_k=10, scope=_ACME
    )
    vector = next(rl for rl in ranked.lists if RetrievalOrigin.VECTOR in rl.origins)
    docs = {c.metadata.get("doc") for c in vector.candidates}
    assert docs == {"acme", "handbook"}, "own rows + the shared corpus, and nothing else"


async def test_keyword_arm_applies_the_same_tenant_predicate():
    """A BM25 arm that skipped the predicate would re-open the leak the vector arm closed."""
    backend = await _lite_backend()
    hits = await backend.keyword_recall(
        "refunds issued within business days", top_k=10, scope=_ACME
    )
    assert {c.metadata.get("doc") for c in hits} == {"acme", "handbook"}


async def test_unscoped_recall_sees_only_the_shared_corpus():
    """A null tenant is not a wildcard: it reads shared rows, never a tenant's."""
    backend = await _lite_backend()
    hits = await backend.keyword_recall(
        "refunds issued within business days", top_k=10, scope=_SHARED
    )
    assert {c.metadata.get("doc") for c in hits} == {"handbook"}


async def test_graph_expansion_cannot_hop_through_another_tenants_chunk():
    """Foreign rows are excluded as seeds *and* as neighbours, not just from the output."""
    backend = await _lite_backend()
    ranked = await backend.recall_ranked(
        "refunds issued within business days", top_k=10, scope=_ACME
    )
    graph = next(rl for rl in ranked.lists if RetrievalOrigin.GRAPH in rl.origins)
    assert {c.metadata.get("doc") for c in graph.candidates} == {"acme", "handbook"}


async def test_end_to_end_one_tenants_passage_never_reaches_another():
    """The whole pipeline, twice: the second run is the one that would catch a cache leak."""
    backend = await _lite_backend()
    retriever = Retriever(
        backend=backend,
        cache=SemanticCache(FakeRedis(), ttl_seconds=60, similarity_threshold=0.985),
        complete=RecordingComplete('{"scores": [{"id": 0, "score": 9}]}'),
        embed=SequenceEmbed([1.0, 0.0]),
        config=RetrievalConfig(recall_top_k=10, final_top_k=5),
    )
    query = "how long do refunds take?"

    for _ in range(2):
        acme = await retriever.retrieve(query, scope=_ACME)
        globex = await retriever.retrieve(query, scope=_GLOBEX)
        assert "Globex" not in acme.answer_context
        assert not any("Globex" in s.text for s in acme.sources)
        assert "Acme" not in globex.answer_context
        assert not any("Acme" in s.text for s in globex.sources)
