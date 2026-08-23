"""Cross-tenant isolation of the retrieval path.

These tests exist because the leak they cover was real: ``retrieve`` carried no tenant,
so two tenants asking the same question shared a cache entry — including the retrieved
passages. Each test below pins one link of the chain that now prevents that:

* the contract cannot be called unscoped at all;
* the exact and semantic cache tiers are partitioned per tenant, not filtered;
* a corpus-version bump invalidates one tenant's entries and nobody else's;
* the backend's vector, keyword and graph arms all apply the same tenant predicate;
* a null tenant reads the shared corpus and is never a wildcard;
* each tenant's vectors live in a collection of their own, so a forgotten filter
  returns nothing rather than everything, and an unresolvable tenant raises;
* and — the last section — every row that *did* come back is re-checked against the
  caller's scope afterwards, so the two mechanisms above both failing is still not a
  leak.
"""

from __future__ import annotations

import inspect

import pytest

from aegis.retrieval import lightrag_backend as lightrag_module
from aegis.retrieval.cache import SemanticCache
from aegis.retrieval.corpus import bump_corpus_version, corpus_version, reset_corpus_versions
from aegis.retrieval.lightrag_backend import LightRAGBackend
from aegis.retrieval.memory import InMemoryKnowledgeBackend
from aegis.retrieval.models import RetrievalResult, Source
from aegis.retrieval.pipeline import RetrievalConfig, Retriever
from aegis.retrieval.types import (
    TENANT_METADATA_KEY,
    CrossTenantLeakError,
    RetrievalOrigin,
    RetrievalScope,
    UnresolvedTenantScopeError,
    tenant_collection_name,
    tenant_metadata_value,
    verify_rows_in_scope,
)
from aegis.retrieval.vector_store import QdrantVectorStore

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
    await cache.set("what is our escalation policy?", _ACME, [1.0, 0.0], _result("acme answer"))

    assert await cache.get_exact("what is our escalation policy?", _GLOBEX) is None
    hit = await cache.get_exact("what is our escalation policy?", _ACME)
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
    await retriever.ingest([{"id": "a", "text": "Acme closures take five days."}], scope=_ACME)
    assert _tenant_chunks(retriever) == [tenant_metadata_value(1)]


async def test_ingest_ledger_is_per_tenant_so_two_tenants_can_hold_one_document():
    """The same document ingested by two tenants is two rows, not a deduped one."""
    retriever = _retriever()
    doc = [{"id": "a", "text": "Closures are approved and returned to the original approver."}]

    first = await retriever.ingest(doc, scope=_ACME)
    second = await retriever.ingest(doc, scope=_GLOBEX)
    third = await retriever.ingest(doc, scope=_ACME)  # genuine re-ingest

    assert first.chunks_written == second.chunks_written == 1
    assert third.chunks_written == 0 and third.chunks_duplicate == 1
    ids = [c.id for c in retriever.backend.ingested]
    assert len(set(ids)) == 2, "per-tenant rows must not share a vector-store primary key"


async def _lite_backend() -> InMemoryKnowledgeBackend:
    """Build one shared in-process Qdrant backend holding two tenants' + shared chunks."""
    backend = InMemoryKnowledgeBackend([], vector_store=QdrantVectorStore.local())
    retriever = Retriever(
        backend=backend,
        cache=SemanticCache(FakeRedis(), ttl_seconds=60, similarity_threshold=0.985),
        complete=RecordingComplete('{"scores": []}'),
        embed=SequenceEmbed([1.0, 0.0]),
        config=RetrievalConfig(),
    )
    await retriever.ingest(
        [{"id": "acme", "text": "Acme approves closures within five business days."}],
        scope=_ACME,
    )
    await retriever.ingest(
        [{"id": "globex", "text": "Globex approves closures within five business days."}],
        scope=_GLOBEX,
    )
    await retriever.ingest(
        [
            {
                "id": "handbook",
                "text": "Closures are approved and returned to the original approver.",
            }
        ],
        scope=_SHARED,
    )
    return backend


async def test_vector_arm_filters_by_tenant_in_the_qdrant_query():
    """One embedded store, three owners: a tenant sees its own rows plus the shared ones."""
    backend = await _lite_backend()
    ranked = await backend.recall_ranked(
        "closures approved within business days", top_k=10, scope=_ACME
    )
    vector = next(rl for rl in ranked.lists if RetrievalOrigin.VECTOR in rl.origins)
    docs = {c.metadata.get("doc") for c in vector.candidates}
    assert docs == {"acme", "handbook"}, "own rows + the shared corpus, and nothing else"


async def test_keyword_arm_applies_the_same_tenant_predicate():
    """A BM25 arm that skipped the predicate would re-open the leak the vector arm closed."""
    backend = await _lite_backend()
    hits = await backend.keyword_recall(
        "closures approved within business days", top_k=10, scope=_ACME
    )
    assert {c.metadata.get("doc") for c in hits} == {"acme", "handbook"}


async def test_unscoped_recall_sees_only_the_shared_corpus():
    """A null tenant is not a wildcard: it reads shared rows, never a tenant's."""
    backend = await _lite_backend()
    hits = await backend.keyword_recall(
        "closures approved within business days", top_k=10, scope=_SHARED
    )
    assert {c.metadata.get("doc") for c in hits} == {"handbook"}


async def test_graph_expansion_cannot_hop_through_another_tenants_chunk():
    """Foreign rows are excluded as seeds *and* as neighbours, not just from the output."""
    backend = await _lite_backend()
    ranked = await backend.recall_ranked(
        "closures approved within business days", top_k=10, scope=_ACME
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
    query = "how long do closures take?"

    for _ in range(2):
        acme = await retriever.retrieve(query, scope=_ACME)
        globex = await retriever.retrieve(query, scope=_GLOBEX)
        assert "Globex" not in acme.answer_context
        assert not any("Globex" in s.text for s in acme.sources)
        assert "Acme" not in globex.answer_context
        assert not any("Acme" in s.text for s in globex.sources)


# ──────────────────────────────────────────────── collection per tenant (D4c)


#: The three passages the collection-per-tenant tests assert on. They are near-identical
#: on purpose: every arm below is asked the *same* question, so what separates the
#: results has to be the partition rather than the wording.
_ACME_TEXT = "Acme approves closures within five business days."
_GLOBEX_TEXT = "Globex approves closures within five business days."
_HANDBOOK_TEXT = "Closures are approved and returned to the original approver."
_QUERY = "closures approved within business days"


class _RecordingStore:
    """A vector store that records which collections were searched.

    The assertion "nothing leaked" is weaker than the assertion "nothing was even
    looked at", and only the second one can distinguish *refusing* to search from
    searching everything and filtering the results afterwards. This wrapper is what makes
    the second one available: it delegates every call to the real in-process Qdrant store
    and keeps a list of the collection names ``search`` was asked for.
    """

    def __init__(self, inner: QdrantVectorStore) -> None:
        self._inner = inner
        self.searched: list[str] = []

    def ensure_collection(self, name: str, dim: int, **kwargs: object) -> None:
        self._inner.ensure_collection(name, dim, **kwargs)  # type: ignore[arg-type]

    def upsert(self, name: str, ids, vectors, payloads) -> None:  # noqa: ANN001
        self._inner.upsert(name, ids, vectors, payloads)

    def search(self, name: str, vector, k: int, *, filter=None):  # noqa: A002, ANN001, ANN202
        self.searched.append(name)
        return self._inner.search(name, vector, k, filter=filter)


async def _recording_backend() -> InMemoryKnowledgeBackend:
    """Build a lite backend over a recording store holding all three passages."""
    backend = InMemoryKnowledgeBackend([], vector_store=_RecordingStore(QdrantVectorStore.local()))
    retriever = Retriever(
        backend=backend,
        cache=SemanticCache(FakeRedis(), ttl_seconds=60, similarity_threshold=0.985),
        complete=RecordingComplete('{"scores": []}'),
        embed=SequenceEmbed([1.0, 0.0]),
        config=RetrievalConfig(),
    )
    for doc_id, text, scope in (
        ("acme", _ACME_TEXT, _ACME),
        ("globex", _GLOBEX_TEXT, _GLOBEX),
        ("handbook", _HANDBOOK_TEXT, _SHARED),
    ):
        await retriever.ingest([{"id": doc_id, "text": text}], scope=scope)
    return backend


def _vector_texts(ranked) -> set[str]:  # noqa: ANN001 - RankedRecall
    """Return the candidate *texts* of the vector arm — never the ids or the count."""
    vector = next(rl for rl in ranked.lists if RetrievalOrigin.VECTOR in rl.origins)
    return {c.text for c in vector.candidates}


async def test_two_tenants_asking_the_same_question_get_disjoint_passages():
    """The same query, two tenants, and the only text they share is the shared corpus.

    Asserted on **content**. A count assertion ("each sees two") is satisfied by a policy
    that returns everything and truncates, and by one that returns the wrong tenant's
    row; only the text says which passage actually came back. The intersection is
    asserted too, and asserted to be exactly the shared handbook rather than merely
    small — the shared corpus is deliberately readable by both, and every *other* overlap
    would be a leak.
    """
    backend = await _recording_backend()

    acme = _vector_texts(await backend.recall_ranked(_QUERY, top_k=10, scope=_ACME))
    globex = _vector_texts(await backend.recall_ranked(_QUERY, top_k=10, scope=_GLOBEX))

    assert acme == {_ACME_TEXT, _HANDBOOK_TEXT}
    assert globex == {_GLOBEX_TEXT, _HANDBOOK_TEXT}
    assert acme & globex == {_HANDBOOK_TEXT}, (
        "the only passage two tenants may both read is the tenant-less shared corpus"
    )
    assert _GLOBEX_TEXT not in acme and _ACME_TEXT not in globex


async def test_a_tenants_vectors_live_in_a_collection_of_their_own():
    """Each tenant's rows are a separate Qdrant collection, and a read touches only its own.

    The names are asserted because they are the boundary: ``aegis_chunks_t1`` cannot be
    reached by a query against ``aegis_chunks_t2`` no matter what filter that query
    carries or forgets.
    """
    backend = await _recording_backend()
    store = backend._vector_store

    assert backend._collection_for(_ACME.tenant_value()) == "aegis_chunks_t1"
    assert backend._collection_for(_GLOBEX.tenant_value()) == "aegis_chunks_t2"
    assert backend._collection_for(None) == "aegis_chunks_shared"

    store.searched.clear()
    await backend.recall_ranked(_QUERY, top_k=10, scope=_ACME)
    assert store.searched == ["aegis_chunks_t1", "aegis_chunks_shared"], (
        "tenant 1's recall read collections it does not own"
    )


async def test_the_collection_returns_one_tenant_even_with_no_filter_at_all():
    """Forget the ``where`` filter entirely and the partition still answers for one tenant.

    This is the property the whole change exists for, and it is asserted at its weakest
    point: the store is queried directly, with ``filter=None``, which under the previous
    single-collection layout returned **every** tenant's chunks and no error. A boundary
    that only holds while callers remember something is not a boundary.
    """
    backend = await _recording_backend()
    store = backend._vector_store
    q_vec = (await backend._embed([_QUERY]))[0]

    unfiltered = store.search("aegis_chunks_t1", q_vec, 10, filter=None)
    texts = {str(hit.payload.get("text")) for hit in unfiltered}
    assert texts == {_ACME_TEXT}, (
        f"an unfiltered search of tenant 1's collection returned {texts} — the "
        "collection is not the boundary"
    )


async def test_the_scope_filter_is_a_second_predicate_and_not_decoration():
    """Poison one collection with a foreign row and the ``where`` filter must still hold.

    ``_scope_filter``'s docstring calls itself "the second, independent predicate on the
    same fact" and claims "a regression there fails a test here instead of in
    production". That claim was false: the collection *is* the boundary for every row
    ``_owner_of`` routes, so deleting the ``where`` clause from ``_vector_list``
    entirely changed no test's outcome — measured, 284 passed either way. A defence
    nothing exercises is a defence nobody will notice the removal of.

    What can put a foreign row inside a tenant's collection is not hypothetical: a
    repair or backfill script writing straight to the store, a restore that replays an
    older payload shape, or a future writer that derives the collection from the request
    in flight rather than from the row's own owner — the exact mistake ``_owner_of``
    exists to prevent, which means it is the mistake worth having a second net under.
    This writes that row directly and asserts the search does not return it.
    """
    backend = await _lite_backend()
    store = backend._vector_store
    q_vec = (await backend._embed([_QUERY]))[0]
    foreign = "Globex's merger terms, mis-filed into tenant 1's partition."

    store.upsert(
        tenant_collection_name("aegis_chunks", "t1"),
        ids=["mis-routed"],
        vectors=[q_vec],
        payloads=[{"doc": "globex", "text": foreign, TENANT_METADATA_KEY: "t2"}],
    )

    # Non-vacuity: the poisoned row really is in tenant 1's collection and really is a
    # hit for this query, so the assertion below is about the filter and nothing else.
    unfiltered = store.search(
        tenant_collection_name("aegis_chunks", "t1"), q_vec, 10, filter=None
    )
    assert foreign in {str(hit.payload.get("text")) for hit in unfiltered}

    ranked = await backend.recall_ranked(_QUERY, top_k=10, scope=_ACME)
    texts = {c.text for lst in ranked.lists for c in lst.candidates}
    assert foreign not in texts, (
        "the vector arm returned a row whose recorded owner is another tenant — the "
        f"where filter is not being applied: {texts}"
    )


async def test_the_scope_filter_keeps_a_null_owner_from_matching_a_real_tenant():
    """The shared corpus's null sentinel is not a wildcard, at the filter level.

    This is the specific closure ``_scope_filter``'s docstring points at: the store
    encodes a ``None`` owner as an explicit null sentinel rather than letting Qdrant drop
    the key, because a dropped key matches every ``where`` clause. Both directions are
    asserted, so a regression that turns either "no tenant" or "some tenant" into "any
    tenant" fails here.
    """
    backend = await _lite_backend()
    store = backend._vector_store
    q_vec = (await backend._embed([_QUERY]))[0]
    shared_name = tenant_collection_name("aegis_chunks", None)
    tenant_name = tenant_collection_name("aegis_chunks", "t1")

    # A tenant-owned row mis-filed into the shared partition, and a shared-owned row
    # mis-filed into a tenant's. Neither may be returned by the other's filter.
    store.upsert(
        shared_name,
        ids=["tenant-row-in-shared"],
        vectors=[q_vec],
        payloads=[{"doc": "globex", "text": "owned by t2", TENANT_METADATA_KEY: "t2"}],
    )
    store.upsert(
        tenant_name,
        ids=["shared-row-in-tenant"],
        vectors=[q_vec],
        payloads=[{"doc": "hb", "text": "owned by nobody", TENANT_METADATA_KEY: None}],
    )

    shared_hits = store.search(shared_name, q_vec, 10, filter=backend._scope_filter(None))
    assert "owned by t2" not in {str(h.payload.get("text")) for h in shared_hits}, (
        "a filter for the shared corpus matched a row owned by a real tenant"
    )

    own_hits = store.search(tenant_name, q_vec, 10, filter=backend._scope_filter("t1"))
    assert "owned by nobody" not in {str(h.payload.get("text")) for h in own_hits}, (
        "a filter for tenant 1 matched a row whose owner is the null sentinel"
    )


async def test_a_tenant_with_nothing_ingested_reads_nothing_of_anyone_elses():
    """An unwritten partition is empty, not absent-therefore-unfiltered.

    A collection that was never created is the state a *new* tenant is in, and it is the
    state a bug that mis-derives the name produces. Both must return the tenant's own
    (empty) corpus plus the shared one — never a widened search.
    """
    backend = await _recording_backend()
    stranger = RetrievalScope(tenant_id=9999)

    texts = _vector_texts(await backend.recall_ranked(_QUERY, top_k=10, scope=stranger))
    assert texts == {_HANDBOOK_TEXT}, (
        f"a tenant with no corpus of its own read {texts}"
    )


@pytest.mark.parametrize(
    "scope",
    [
        None,
        "7",
        RetrievalScope(tenant_id="7"),
        RetrievalScope(tenant_id=True),
        RetrievalScope(tenant_id=object()),
    ],
    ids=["missing", "bare-string", "string-tenant", "bool-tenant", "object-tenant"],
)
async def test_an_unresolvable_scope_raises_instead_of_searching_unscoped(scope):
    """Every recall arm refuses, and refuses **before** touching the store.

    Each parameter is a real shape a tenant id arrives in when it has lost its type on
    the way — a header value, a JSON payload, a half-built request object, or a
    ``bool`` that would otherwise resolve to the genuinely-existing tenant 1. None of
    them names a partition, so none of them may be answered.

    The store assertion is the load-bearing half. "It returned nothing" would also be
    true of a search that ran against a collection that happened to be empty; "it never
    searched" is the only evidence that the refusal is the mechanism rather than luck.
    """
    backend = await _recording_backend()
    store = backend._vector_store
    store.searched.clear()

    with pytest.raises(UnresolvedTenantScopeError):
        await backend.recall_ranked(_QUERY, top_k=10, scope=scope)
    with pytest.raises(UnresolvedTenantScopeError):
        await backend.keyword_recall(_QUERY, top_k=10, scope=scope)
    with pytest.raises(UnresolvedTenantScopeError):
        await backend.recall(_QUERY, top_k=10, scope=scope)

    assert store.searched == [], (
        f"an unresolvable scope reached the vector store: {store.searched}. It must "
        "never fall back to an unscoped search"
    )


def test_a_collection_name_cannot_be_built_from_an_arbitrary_string():
    """The partition name is derived, never accepted — including on the write side.

    A chunk whose recorded owner is not a token this package minted would otherwise pick
    its own collection, which is the same class of bug as a caller choosing its own
    tenant filter.
    """
    assert tenant_collection_name("p", None) == "p_shared"
    assert tenant_collection_name("p", tenant_metadata_value(3)) == "p_t3"
    for forged in ("shared", "t3'; drop", "", "T3", "t-3"):
        with pytest.raises(UnresolvedTenantScopeError):
            tenant_collection_name("p", forged)


async def test_an_unresolvable_scope_cannot_reach_a_real_tenants_cache_partition():
    """The cache is the *first* door, so resolution has to happen there too.

    ``Retriever.retrieve`` consults both cache tiers before it ever calls the backend.
    A scope carrying ``tenant_id="7"`` would therefore have been served tenant 7's
    cached answer — its partition key was built with ``int(self.tenant_id)``, which
    happily turns the string ``"7"`` into the integer 7 — with no arm of the backend
    running and nothing to raise. Closing the vector arm alone would have left that
    path open, which is why it is asserted here rather than assumed.
    """
    cache = _cache()
    await cache.set("what is our escalation policy?", RetrievalScope(tenant_id=7), [1.0, 0.0],
                    _result("tenant seven's answer"))
    forged = RetrievalScope(tenant_id="7")

    with pytest.raises(UnresolvedTenantScopeError):
        forged.partition_key()
    with pytest.raises(UnresolvedTenantScopeError):
        await cache.get_exact("what is our escalation policy?", forged)
    with pytest.raises(UnresolvedTenantScopeError):
        await cache.get_semantic([1.0, 0.0], forged)

    # The positive control: the real tenant 7 still reads its own entry, so the refusal
    # above is about resolution and not about a cache that stopped working.
    hit = await cache.get_exact("what is our escalation policy?", RetrievalScope(tenant_id=7))
    assert hit is not None and hit.answer_context == "tenant seven's answer"


async def test_the_pipeline_refuses_an_unresolvable_scope_before_the_cache():
    """End to end: ``retrieve`` raises rather than answering an unresolvable request."""
    retriever = _retriever()
    with pytest.raises(UnresolvedTenantScopeError):
        await retriever.retrieve("why is the sky blue?", scope=RetrievalScope(tenant_id="7"))


# ────────────────────────────── the fail-closed post-check (defence in depth)


#: The foreign passage every test in this section tries to reach. It exists nowhere else
#: in this repository, so an assertion that it did not come back is an assertion about
#: this path rather than about a coincidence of wording.
_FOREIGN = "Globex board pack: the AUDITA-SERVAL-3307 acquisition closes in March."


def _neuter_the_predicate(backend: InMemoryKnowledgeBackend) -> None:
    """Simulate the payload predicate being forgotten, without deleting the code.

    ``{}`` is what a filter builder that lost its conditions returns, and
    :meth:`~aegis.retrieval.vector_store.QdrantVectorStore._build_filter` turns it into
    ``query_filter=None`` — an unfiltered search, which is the exact shape of the bug
    this section exists for. Patching the builder rather than editing it means the test
    describes the *failure*, not a second implementation of the code under test.
    """
    backend._scope_filter = lambda tenant_value: {}  # type: ignore[assignment]


async def _poison(
    backend: InMemoryKnowledgeBackend, collection: str, owner: str | None, **payload: object
) -> list[float]:
    """Write a row directly into ``collection``, bypassing the owner-based routing.

    This is the first of the two boundaries failing: a repair script, a restore of an
    older payload shape, or a writer that picks the partition from the request in flight
    instead of from the row's own owner. It returns the query vector it indexed the row
    at, so the caller can prove the row really is a hit.
    """
    q_vec = (await backend._embed([_QUERY]))[0]
    body: dict[str, object] = {"doc": "globex", "text": _FOREIGN, **payload}
    if owner is not _UNSET:
        body[TENANT_METADATA_KEY] = owner
    backend._vector_store.upsert(collection, ids=["mis-routed"], vectors=[q_vec], payloads=[body])
    return q_vec


#: Distinguishes "write a ``None`` owner" from "write no owner key at all".
_UNSET = object()


async def test_a_forgotten_vector_predicate_raises_rather_than_returning_a_foreign_chunk():
    """Both pre-search boundaries fail, and the caller still cannot see the other tenant.

    This is the whole claim of the post-check, so both boundaries are broken deliberately
    and at the same time:

    1. the **collection** stops being the boundary — the foreign row is written straight
       into tenant 1's partition, the way a backfill script or a restore can;
    2. the **predicate** stops being applied — ``_scope_filter`` is patched to return
       nothing, which is what a filter builder that lost its conditions produces and what
       :meth:`QdrantVectorStore._build_filter` renders as ``query_filter=None``.

    With both gone the search genuinely returns tenant 2's passage — asserted below
    before the call, so this test cannot pass vacuously. What must not happen is that it
    reaches the caller, and it must not be silently dropped either: a third quiet filter
    fails in the same direction as the first two.
    """
    backend = await _lite_backend()
    q_vec = await _poison(backend, tenant_collection_name("aegis_chunks", "t1"), "t2")
    _neuter_the_predicate(backend)

    # Non-vacuity: with no predicate, tenant 1's own collection really does hand back
    # tenant 2's row. Everything after this is about the post-check and nothing else.
    unfiltered = backend._vector_store.search(
        tenant_collection_name("aegis_chunks", "t1"), q_vec, 10, filter=None
    )
    assert _FOREIGN in {str(h.payload.get("text")) for h in unfiltered}

    with pytest.raises(CrossTenantLeakError) as raised:
        await backend.recall_ranked(_QUERY, top_k=10, scope=_ACME)

    message = str(raised.value)
    assert "mis-routed" in message, "the refusal must name the row that broke the scope"
    assert "t2" in message
    assert _FOREIGN not in message, "the refusal must not itself carry the foreign text"


async def test_the_post_check_lets_a_scope_read_its_own_rows_and_the_shared_corpus():
    """The positive control: with the predicate gone but nothing mis-filed, nothing raises.

    Without this the test above is satisfied by a check that raises on everything, which
    would be a broken retriever rather than a defence. It also pins the null-tenant
    semantics from the other side — the shared corpus is a *positive match*, not a row
    the post-check has to tolerate.
    """
    backend = await _lite_backend()
    _neuter_the_predicate(backend)

    ranked = await backend.recall_ranked(_QUERY, top_k=10, scope=_ACME)
    docs = {c.metadata.get("doc") for lst in ranked.lists for c in lst.candidates}

    assert "acme" in docs and "handbook" in docs
    assert "globex" not in docs


async def test_the_post_check_is_not_a_wildcard_for_a_null_tenant():
    """An unscoped caller reads the shared corpus, and a tenant's row inside it still raises.

    "No tenant" must not become "any tenant" at this layer either. The row is mis-filed
    into the *shared* partition — the one an unscoped request is entitled to read — and
    the predicate is gone, so the only thing left saying no is the owner recorded on the
    row itself.
    """
    backend = await _lite_backend()
    await _poison(backend, tenant_collection_name("aegis_chunks", None), "t2")
    _neuter_the_predicate(backend)

    with pytest.raises(CrossTenantLeakError):
        await backend.recall_ranked(_QUERY, top_k=10, scope=_SHARED)


async def test_a_row_that_records_no_owner_is_refused_rather_than_read_as_shared():
    """A missing owner is unknown, not unowned — and unknown fails closed.

    The distinction is the one that let an untagged row be readable by every tenant at
    once: ``payload.get(TENANT_METADATA_KEY)`` returns ``None`` for a key that is absent
    *and* for a row genuinely owned by nobody, and only one of those two is safe to serve.
    """
    backend = await _lite_backend()
    await _poison(backend, tenant_collection_name("aegis_chunks", "t1"), _UNSET)
    _neuter_the_predicate(backend)

    with pytest.raises(CrossTenantLeakError, match="no owner at all"):
        await backend.recall_ranked(_QUERY, top_k=10, scope=_ACME)


def test_the_post_check_refuses_an_unresolvable_scope_instead_of_waving_rows_through():
    """A checker that cannot resolve the caller's scope must not become a no-op."""
    rows = [("r1", {TENANT_METADATA_KEY: None})]
    with pytest.raises(UnresolvedTenantScopeError):
        verify_rows_in_scope(rows, RetrievalScope(tenant_id="7"), arm="unit")


# ───────────────────────────────── the same net under the LightRAG production path


class _RagHoldingTwoTenants:
    """A LightRAG whose one shared index holds both tenants' chunks.

    That is the real shape of the production path: one instance means one vector index
    and one Neo4j graph for every tenant in the process, LightRAG exposes no per-query
    metadata predicate, and so the tenant boundary there is a filter applied on the way
    out rather than a partition. A filter is the only thing standing between these two
    rows and the caller, which is precisely why it needs something behind it.
    """

    async def aquery_data(self, query: str, param: object) -> dict:
        return {
            "status": "success",
            "data": {
                "chunks": [
                    {"id": "a1", "content": "Acme's own closure policy.",
                     "file_path": "t1::terms.pdf"},
                    {"id": "b1", "content": _FOREIGN, "file_path": "t2::board-pack.pdf"},
                ],
                "entities": [],
                "relationships": [],
            },
        }


def _lightrag_backend() -> LightRAGBackend:
    backend = LightRAGBackend(
        complete=RecordingComplete("{}"), embed=SequenceEmbed([1.0, 0.0])
    )
    backend._rag = _RagHoldingTwoTenants()  # the real seam _ensure() fills
    return backend


async def test_the_lightrag_filter_keeps_the_foreign_chunk_out():
    """The control: with the filter in place, tenant 1 sees its own chunk and no other."""
    recall = await _lightrag_backend().recall("closures", top_k=10, scope=_ACME)
    assert [c.id for c in recall.candidates] == ["a1"]


async def test_a_lightrag_recall_that_lost_its_filter_raises_rather_than_leaking(monkeypatch):
    """Delete the only boundary on the production path and the row-check still refuses.

    ``_scoped_recall`` is replaced with the identity — the shape of "why are we filtering
    twice?", of a refactor that moves the call, and of a ``visible`` list that comes back
    wrong. Before the post-check existed this returned tenant 2's board pack to tenant 1
    with no error anywhere; it now raises.
    """
    monkeypatch.setattr(lightrag_module, "_scoped_recall", lambda recall, scope: recall)
    backend = _lightrag_backend()

    with pytest.raises(CrossTenantLeakError) as raised:
        await backend.recall("closures", top_k=10, scope=_ACME)
    assert "b1" in str(raised.value)

    # ...and an unscoped run is unchanged: this backend has no boundary to cross there,
    # and inventing one would break the offline eval path rather than close a leak.
    monkeypatch.setattr(lightrag_module, "_scoped_recall", lambda recall, scope: recall)
    unscoped = await backend.recall("closures", top_k=10, scope=_SHARED)
    assert {c.id for c in unscoped.candidates} == {"a1", "b1"}
