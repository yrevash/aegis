"""Qdrant-backed memory recall — the ANN engine, isolation, and SQL source-of-truth.

These tests pin the slice-2 behaviour: memory's semantic recall runs through an embedded
:class:`~aegis.retrieval.vector_store.QdrantVectorStore` (a real offline index, never a
RAM dict), scoped by tenant + subject payload filters, and every hit is joined back to the
authoritative SQL row before it is returned. The cross-tenant leak test is load-bearing —
a recall must never surface another tenant's row even when the subject id collides.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from aegis.memory import (
    MemoryVectorIndex,
    get_default_index,
    set_default_index,
)
from aegis.memory.config import MemoryConfig
from aegis.memory.recall import recall
from aegis.memory.stores import MemoryFact, MemoryMessage, MemorySession
from aegis.memory.vector_ops import topk_by_cosine

pytestmark = pytest.mark.asyncio


def _fact(subject_id, predicate, obj, emb, *, tenant_id=None, **kw) -> MemoryFact:
    return MemoryFact(
        subject_id=subject_id,
        tenant_id=tenant_id,
        fact_type="preference",
        subject="customer",
        predicate=predicate,
        object=obj,
        text=f"Customer {predicate} {obj}.",
        embedding=emb,
        **kw,
    )


# --------------------------------------------------------------------------- engine


async def test_default_index_is_embedded_qdrant_not_a_dict():
    """The process-wide default is the real embedded qdrant engine, offline."""
    index = get_default_index()
    assert isinstance(index, MemoryVectorIndex)
    assert index.store.mode == "local"  # embedded — no server, no dict fallback
    assert index.store.location == ":memory:"


async def test_recall_search_goes_through_qdrant(db):
    """Recall mirrors the subject's fact into a Qdrant collection and searches it there."""
    index = MemoryVectorIndex.local()
    set_default_index(index)
    cfg = MemoryConfig()
    async with db() as s:
        s.add(MemorySession(id="sess-1", subject_id="user:1"))
        s.add(_fact("user:1", "prefers_channel", "email", [1.0, 0.0, 0.0, 0.0]))
        await s.commit()

    async with db() as s:
        bundle = await recall(
            s,
            subject_id="user:1",
            session_id="sess-1",
            persona="ops",
            query="channel?",
            query_vec=[1.0, 0.0, 0.0, 0.0],
            config=cfg,
        )
    assert [c.key for c in bundle.facts] == ["customer|prefers_channel"]
    # Independently prove the vector actually landed in — and is searchable from — Qdrant.
    collection = index._collection("memory_fact", 4)
    hits = index.store.search(collection, [1.0, 0.0, 0.0, 0.0], 5, filter={"subject_id": "user:1"})
    assert hits and hits[0].score == pytest.approx(1.0)  # cosine identity via Qdrant


async def test_score_is_qdrant_cosine_and_ranks(db):
    """The composite's relevance is the Qdrant cosine score; nearest ranks first."""
    async with db() as s:
        s.add(MemorySession(id="sess-1", subject_id="user:1"))
        s.add(_fact("user:1", "prefers_channel", "email", [1.0, 0.0, 0.0, 0.0], importance=5))
        s.add(_fact("user:1", "region", "emea", [0.0, 1.0, 0.0, 0.0], importance=5))
        await s.commit()

    async with db() as s:
        hits = await topk_by_cosine(
            s,
            MemoryFact,
            subject_id="user:1",
            query_vec=[1.0, 0.0, 0.0, 0.0],
            k=5,
        )
    assert [f.predicate for f, _ in hits] == ["prefers_channel", "region"]
    assert hits[0][1] == pytest.approx(1.0)  # exact match → cosine 1.0
    assert hits[1][1] == pytest.approx(0.0)  # orthogonal → cosine 0.0


# --------------------------------------------------------------------------- isolation


async def test_cross_tenant_isolation_same_subject_id(db):
    """CRITICAL: colliding subject ids across tenants never leak — payload + SQL both gate."""
    cfg = MemoryConfig()
    async with db() as s:
        s.add(MemorySession(id="sess-t2", subject_id="user:1", tenant_id=2))
        # Same subject_id "user:1" in two tenants, identical (perfect-match) vectors.
        s.add(_fact("user:1", "secret", "tenant-one-only", [1.0, 0.0, 0.0, 0.0], tenant_id=1))
        s.add(_fact("user:1", "topic", "tenant-two-note", [1.0, 0.0, 0.0, 0.0], tenant_id=2))
        await s.commit()

    async with db() as s:
        bundle = await recall(
            s,
            subject_id="user:1",
            session_id="sess-t2",
            persona="ops",
            query="anything",
            query_vec=[1.0, 0.0, 0.0, 0.0],
            config=cfg,
            tenant_id=2,
        )
    keys = [c.key for c in bundle.facts]
    assert "customer|topic" in keys
    assert "customer|secret" not in keys  # tenant-1 fact NEVER crosses into tenant 2
    assert all(c.payload.tenant_id == 2 for c in bundle.facts)


async def test_cross_subject_isolation(db):
    """A recall for one subject never surfaces another subject's fact via the index."""
    async with db() as s:
        s.add(_fact("user:A", "secret", "alpha", [1.0, 0.0, 0.0, 0.0]))
        s.add(_fact("user:B", "topic", "beta", [0.0, 1.0, 0.0, 0.0]))
        await s.commit()

    async with db() as s:
        hits = await topk_by_cosine(
            s,
            MemoryFact,
            subject_id="user:B",
            query_vec=[1.0, 0.0, 0.0, 0.0],  # matches A's secret exactly
            k=5,
        )
    assert all(f.subject_id == "user:B" for f, _ in hits)
    assert "secret" not in {f.predicate for f, _ in hits}


# --------------------------------------------------------------------------- source of truth


async def test_valid_only_join_excludes_invalidated_nearest(db):
    """Even the nearest vector is dropped by the authoritative valid-only SQL join."""
    async with db() as s:
        s.add(_fact("user:1", "prefers_channel", "email", [1.0, 0.0, 0.0, 0.0]))
        s.add(
            _fact(
                "user:1",
                "old_tier",
                "free",
                [1.0, 0.0, 0.0, 0.0],  # identical vector, but invalidated
                invalid_at=datetime.now(UTC),
            )
        )
        await s.commit()

    async with db() as s:
        hits = await topk_by_cosine(
            s,
            MemoryFact,
            subject_id="user:1",
            query_vec=[1.0, 0.0, 0.0, 0.0],
            k=5,
            valid_only=True,
        )
    preds = {f.predicate for f, _ in hits}
    assert "prefers_channel" in preds
    assert "old_tier" not in preds  # invalid row filtered at the SQL source of truth


async def test_stale_qdrant_point_cannot_resurrect_a_deleted_row(db):
    """A point left in Qdrant for a row no longer in SQL is dropped by the join."""
    index = MemoryVectorIndex.local()
    set_default_index(index)
    async with db() as s:
        f = _fact("user:1", "prefers_channel", "email", [1.0, 0.0, 0.0, 0.0])
        s.add(f)
        await s.commit()
        # Prime Qdrant with the fact, then hard-delete the SQL row it points to.
        hits = await topk_by_cosine(
            s, MemoryFact, subject_id="user:1", query_vec=[1.0, 0.0, 0.0, 0.0], k=5
        )
        assert hits  # indexed + found
        fid = hits[0][0].id
        row = (await s.execute(select(MemoryFact).where(MemoryFact.id == fid))).scalar_one()
        await s.delete(row)
        await s.commit()

    async with db() as s:
        hits = await topk_by_cosine(
            s, MemoryFact, subject_id="user:1", query_vec=[1.0, 0.0, 0.0, 0.0], k=5
        )
    assert hits == []  # Qdrant may still hold the point, but SQL is the source of truth


async def test_none_query_vec_and_nonpositive_k_short_circuit(db):
    """No comparable query vector (or k<=0) yields no vector hits (recency handles it)."""
    async with db() as s:
        s.add(_fact("user:1", "prefers_channel", "email", [1.0, 0.0, 0.0, 0.0]))
        await s.commit()
        assert await topk_by_cosine(
            s, MemoryFact, subject_id="user:1", query_vec=None, k=5
        ) == []
        assert await topk_by_cosine(
            s, MemoryFact, subject_id="user:1", query_vec=[1.0, 0.0, 0.0, 0.0], k=0
        ) == []


async def test_episodic_messages_recalled_via_qdrant(db):
    """Message (episodic) vectors are indexed + searched through the same Qdrant path."""
    async with db() as s:
        s.add(MemorySession(id="sess-1", subject_id="user:1"))
        s.add(
            MemoryMessage(
                subject_id="user:1",
                session_id="sess-1",
                turn_index=0,
                role="user",
                content="old refund note",
                embedding=[1.0, 0.0, 0.0, 0.0],
                embedding_dim=4,
            )
        )
        await s.commit()

    async with db() as s:
        hits = await topk_by_cosine(
            s,
            MemoryMessage,
            subject_id="user:1",
            query_vec=[1.0, 0.0, 0.0, 0.0],
            k=5,
        )
    assert len(hits) == 1
    assert hits[0][0].content == "old refund note"
    assert hits[0][1] == pytest.approx(1.0)


# --------------------------------------------------------------------------- sync cost


class _CountingStore:
    """Wraps a real store, recording every (synchronous) qdrant-client call it makes."""

    def __init__(self, store) -> None:
        self._store = store
        self.upserted: list[list[str]] = []
        self.selected_rows = 0
        self.call_threads: set[int] = set()

    def __getattr__(self, name):
        return getattr(self._store, name)

    def ensure_collection(self, *args, **kwargs) -> None:
        self.call_threads.add(threading.get_ident())
        self._store.ensure_collection(*args, **kwargs)

    def upsert(self, name, ids, vectors, payloads) -> None:
        self.call_threads.add(threading.get_ident())
        self.upserted.append(list(ids))
        self._store.upsert(name, ids, vectors, payloads)

    def search(self, *args, **kwargs):
        self.call_threads.add(threading.get_ident())
        return self._store.search(*args, **kwargs)


def _counting_index() -> tuple[MemoryVectorIndex, _CountingStore]:
    index = MemoryVectorIndex.local()
    counting = _CountingStore(index.store)
    index._store = counting
    set_default_index(index)
    return index, counting


async def test_search_does_not_reindex_the_whole_subject_every_query(db):
    """REGRESSION: the ANN mirror is incremental, not a full rescan per query.

    ``search_rows`` used to re-SELECT every embedded row for the subject and re-upsert
    ALL of them on EVERY query, so a consolidation reconciling 8 candidates did 8 full
    scans plus 8 full re-index passes — strictly more expensive than the in-Python cosine
    loop the index replaced.
    """
    _, store = _counting_index()
    async with db() as s:
        for i in range(3):
            s.add(_fact("user:1", f"p{i}", "v", [1.0, 0.0, 0.0, float(i)]))
        await s.commit()

    async with db() as s:
        for _ in range(4):  # four queries, as a multi-candidate reconcile would make
            hits = await topk_by_cosine(
                s, MemoryFact, subject_id="user:1", query_vec=[1.0, 0.0, 0.0, 0.0], k=5
            )
            assert hits  # the index still answers every one of them

    # Exactly one upsert pass — the first — covering the three rows; nothing re-uploaded.
    assert len(store.upserted) == 1
    assert len(store.upserted[0]) == 3


async def test_rows_added_after_the_first_sync_are_still_indexed(db):
    """The high-water mark must not blind the index to rows written later."""
    _, store = _counting_index()
    async with db() as s:
        s.add(_fact("user:1", "first", "v", [1.0, 0.0, 0.0, 0.0]))
        await s.commit()

    async with db() as s:
        assert await topk_by_cosine(
            s, MemoryFact, subject_id="user:1", query_vec=[1.0, 0.0, 0.0, 0.0], k=5
        )
        s.add(_fact("user:1", "second", "v", [0.0, 1.0, 0.0, 0.0]))
        await s.commit()
        hits = await topk_by_cosine(
            s, MemoryFact, subject_id="user:1", query_vec=[0.0, 1.0, 0.0, 0.0], k=5
        )
    assert [f.predicate for f, _ in hits][0] == "second"
    # Two incremental passes, each carrying only the row that was new at the time.
    assert store.upserted == [["1"], ["2"]]


async def test_qdrant_calls_run_off_the_event_loop(db):
    """Every qdrant-client call is synchronous, so none may run on the loop's thread."""
    _, store = _counting_index()
    async with db() as s:
        s.add(_fact("user:1", "prefers_channel", "email", [1.0, 0.0, 0.0, 0.0]))
        await s.commit()

    async with db() as s:
        assert await topk_by_cosine(
            s, MemoryFact, subject_id="user:1", query_vec=[1.0, 0.0, 0.0, 0.0], k=5
        )
    assert store.call_threads  # ensure_collection + upsert + search all fired
    assert threading.get_ident() not in store.call_threads


async def test_null_tenant_search_never_joins_a_tenants_row(db):
    """SECURITY: ``tenant_id=None`` is the null-tenant scope, never a wildcard."""
    async with db() as s:
        s.add(_fact("user:1", "secret", "tenant-one-only", [1.0, 0.0, 0.0, 0.0], tenant_id=1))
        await s.commit()

    async with db() as s:
        hits = await topk_by_cosine(
            s,
            MemoryFact,
            subject_id="user:1",
            query_vec=[1.0, 0.0, 0.0, 0.0],
            k=5,
            tenant_id=None,
        )
    assert hits == []
