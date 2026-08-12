"""The Qdrant-backed vector store, exercised offline with the REAL client.

These use qdrant-client's embedded local mode (``:memory:``) — the official engine, no
server, no network — so they prove upsert + nearest-neighbour search and payload
filtering against the real library, not a stand-in.
"""

from __future__ import annotations

import pytest

from aegis.retrieval.vector_store import QdrantVectorStore, VectorHit, _point_uuid


def _store() -> QdrantVectorStore:
    return QdrantVectorStore.local()  # embedded :memory: — real engine, offline


def test_local_store_reports_honest_mode_and_location():
    store = _store()
    assert store.mode == "local"
    assert store.location == ":memory:"
    assert "QdrantVectorStore" in repr(store)


def test_upsert_then_search_returns_nearest_first():
    store = _store()
    store.ensure_collection("c", dim=3)
    store.upsert(
        "c",
        ids=["a", "b", "c"],
        vectors=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.9, 0.1, 0.0]],
        payloads=[{"doc": "da"}, {"doc": "db"}, {"doc": "dc"}],
    )
    hits = store.search("c", [1.0, 0.0, 0.0], k=3)
    assert [h.id for h in hits] == ["a", "c", "b"]  # cosine nearest first
    assert isinstance(hits[0], VectorHit)
    assert hits[0].score >= hits[1].score >= hits[2].score
    assert hits[0].payload["doc"] == "da"  # caller ids + payload round-trip


def test_search_on_missing_collection_is_honestly_empty():
    store = _store()
    assert store.search("never_created", [1.0, 0.0], k=5) == []


def test_upsert_is_idempotent_by_caller_id():
    store = _store()
    store.ensure_collection("c", dim=2)
    store.upsert("c", ids=["x"], vectors=[[1.0, 0.0]], payloads=[{"v": 1}])
    store.upsert("c", ids=["x"], vectors=[[0.0, 1.0]], payloads=[{"v": 2}])  # overwrite
    hits = store.search("c", [0.0, 1.0], k=5)
    assert len(hits) == 1  # same id → one point, not two
    assert hits[0].payload["v"] == 2  # latest write wins


def test_payload_filter_scopes_results_by_tenant():
    store = _store()
    store.ensure_collection("c", dim=2)
    store.upsert(
        "c",
        ids=["a", "b"],
        vectors=[[1.0, 0.0], [1.0, 0.0]],  # identical vectors — only the filter separates
        payloads=[{"tenant": "acme"}, {"tenant": "globex"}],
    )
    hits = store.search("c", [1.0, 0.0], k=5, filter={"tenant": "globex"})
    assert [h.payload["tenant"] for h in hits] == ["globex"]


def test_match_any_filter_accepts_a_list_of_values():
    store = _store()
    store.ensure_collection("c", dim=2)
    store.upsert(
        "c",
        ids=["a", "b", "c"],
        vectors=[[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]],
        payloads=[{"subject": "s1"}, {"subject": "s2"}, {"subject": "s3"}],
    )
    hits = store.search("c", [1.0, 0.0], k=5, filter={"subject": ["s1", "s3"]})
    assert {h.payload["subject"] for h in hits} == {"s1", "s3"}


def test_ensure_collection_is_idempotent():
    store = _store()
    store.ensure_collection("c", dim=4)
    store.ensure_collection("c", dim=4)  # second call must not raise or wipe data
    store.upsert("c", ids=["a"], vectors=[[1.0, 0.0, 0.0, 0.0]], payloads=[{}])
    store.ensure_collection("c", dim=4)
    assert store.search("c", [1.0, 0.0, 0.0, 0.0], k=1)[0].id == "a"


def test_point_uuid_is_deterministic_and_collection_scoped():
    assert _point_uuid("c", "a") == _point_uuid("c", "a")
    assert _point_uuid("c", "a") != _point_uuid("other", "a")


def test_two_local_stores_are_isolated():
    a, b = _store(), _store()
    a.ensure_collection("c", dim=2)
    a.upsert("c", ids=["x"], vectors=[[1.0, 0.0]], payloads=[{}])
    assert b.search("c", [1.0, 0.0], k=1) == []  # b never saw a's data


def test_server_mode_fails_loud_when_unreachable():
    """A store built for a live node must raise if the node is down — never degrade."""
    with pytest.raises(Exception):  # noqa: B017 - any connection error is acceptable
        QdrantVectorStore.server(url="http://127.0.0.1:59999", timeout=0.5)
