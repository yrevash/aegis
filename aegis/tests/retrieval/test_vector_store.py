"""The Qdrant-backed vector store, exercised offline with the REAL client.

These use ``qdrant_client``'s in-process mode (reported as ``:memory:``) — the official
client's own implementation of the same API the server speaks, no server binary and no
network — so they prove upsert + nearest-neighbour search and payload filtering against
the real library, not a stand-in. ``test_live_qdrant_server_round_trip`` runs the same
path against a node on :data:`_LIVE_URL` when one is up, so the two modes are not taken
on trust.

The null-payload and collection-name tests are load-bearing: Qdrant cannot *match* a
JSON null with ``MatchValue`` (so a naive encoding would have to drop the condition, and
"null tenant" would become "any tenant"), point ids must be UUIDs rather than the
caller's own strings, and collection names become directory names on disk. Each of those
would otherwise surface as a scoping leak, a duplicated row, or a hard crash on names the
rest of Aegis already uses.
"""

from __future__ import annotations

import os
import sys
import uuid

import pytest

from aegis.retrieval.vector_store import (
    _NULL,
    EmbeddedVectorStoreMultiprocessError,
    QdrantVectorStore,
    VectorHit,
    _safe_name,
    configured_worker_count,
)


def _store() -> QdrantVectorStore:
    return QdrantVectorStore.local()  # embedded, in-process — real engine, offline


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
    assert hits[0].score == pytest.approx(1.0)  # similarity, not a raw distance
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


def test_caller_ids_are_scoped_to_their_collection():
    """The same logical id in two collections is two independent rows, never a clash."""
    store = _store()
    store.ensure_collection("one", dim=2)
    store.ensure_collection("two", dim=2)
    store.upsert("one", ids=["x"], vectors=[[1.0, 0.0]], payloads=[{"where": "one"}])
    store.upsert("two", ids=["x"], vectors=[[1.0, 0.0]], payloads=[{"where": "two"}])
    assert store.search("one", [1.0, 0.0], k=5)[0].payload["where"] == "one"
    assert store.search("two", [1.0, 0.0], k=5)[0].payload["where"] == "two"


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


def test_multiple_filter_fields_are_all_required():
    store = _store()
    store.ensure_collection("c", dim=2)
    store.upsert(
        "c",
        ids=["a", "b"],
        vectors=[[1.0, 0.0], [1.0, 0.0]],
        payloads=[
            {"tenant": "acme", "subject": "s1"},
            {"tenant": "acme", "subject": "s2"},
        ],
    )
    hits = store.search("c", [1.0, 0.0], k=5, filter={"tenant": "acme", "subject": "s2"})
    assert [h.id for h in hits] == ["b"]  # AND, not OR


def test_ensure_collection_is_idempotent():
    store = _store()
    store.ensure_collection("c", dim=4)
    store.ensure_collection("c", dim=4)  # second call must not raise or wipe data
    store.upsert("c", ids=["a"], vectors=[[1.0, 0.0, 0.0, 0.0]], payloads=[{}])
    store.ensure_collection("c", dim=4)
    assert store.search("c", [1.0, 0.0, 0.0, 0.0], k=1)[0].id == "a"


def test_ensure_collection_rejects_a_conflicting_dimension():
    """Reusing a collection at a different width fails loud, not silently mixed."""
    store = _store()
    store.ensure_collection("c", dim=4)
    with pytest.raises(ValueError, match="4-dim"):
        store.ensure_collection("c", dim=8)


def test_upsert_before_ensure_collection_fails_loud():
    store = _store()
    with pytest.raises(LookupError):
        store.upsert("nope", ids=["a"], vectors=[[1.0, 0.0]], payloads=[{}])


def test_two_local_stores_are_isolated():
    a, b = _store(), _store()
    a.ensure_collection("c", dim=2)
    a.upsert("c", ids=["x"], vectors=[[1.0, 0.0]], payloads=[{}])
    assert b.search("c", [1.0, 0.0], k=1) == []  # b never saw a's data


def test_server_mode_fails_loud_when_unreachable():
    """A store built for a live node must raise if the node is down — never degrade."""
    with pytest.raises(Exception):  # noqa: B017 - any connection error is acceptable
        QdrantVectorStore.server(url="http://127.0.0.1:59999", timeout=0.5)


# --------------------------------------------------------------------------- null scope


def test_null_payload_value_round_trips_as_none():
    """``None`` is stored as a sentinel; the store must hand it back as ``None``."""
    store = _store()
    store.ensure_collection("c", dim=2)
    store.upsert("c", ids=["a"], vectors=[[1.0, 0.0]], payloads=[{"tenant_id": None}])
    hit = store.search("c", [1.0, 0.0], k=1)[0]
    assert hit.payload["tenant_id"] is None


def test_null_filter_matches_only_null_scoped_points():
    """SECURITY: filtering on ``None`` is the *null scope*, never a wildcard.

    Qdrant's ``MatchValue`` cannot express a JSON null, so the naive encoding would have
    to drop the condition entirely and return every tenant's points. This is the
    cross-tenant leak the store's sentinel exists to prevent.
    """
    store = _store()
    store.ensure_collection("c", dim=2)
    store.upsert(
        "c",
        ids=["null-tenant", "tenant-1", "tenant-2"],
        vectors=[[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]],  # identical — only scope separates
        payloads=[{"tenant_id": None}, {"tenant_id": 1}, {"tenant_id": 2}],
    )
    hits = store.search("c", [1.0, 0.0], k=10, filter={"tenant_id": None})
    assert [h.id for h in hits] == ["null-tenant"]
    assert all(h.payload["tenant_id"] is None for h in hits)


def test_tenant_filter_never_matches_the_null_tenant_row():
    """The symmetry also holds the other way: a real tenant never sees null-scoped rows."""
    store = _store()
    store.ensure_collection("c", dim=2)
    store.upsert(
        "c",
        ids=["null-tenant", "tenant-1"],
        vectors=[[1.0, 0.0], [1.0, 0.0]],
        payloads=[{"tenant_id": None}, {"tenant_id": 1}],
    )
    assert [h.id for h in store.search("c", [1.0, 0.0], k=10, filter={"tenant_id": 1})] == [
        "tenant-1"
    ]


def test_storing_the_null_sentinel_literally_is_refused():
    """Aliasing a real value onto ``None`` would corrupt scoping, so it raises."""
    store = _store()
    store.ensure_collection("c", dim=2)
    with pytest.raises(ValueError, match="null sentinel"):
        store.upsert("c", ids=["a"], vectors=[[1.0, 0.0]], payloads=[{"tenant_id": _NULL}])


def test_non_scalar_payload_value_is_refused():
    store = _store()
    store.ensure_collection("c", dim=2)
    with pytest.raises(TypeError):
        store.upsert("c", ids=["a"], vectors=[[1.0, 0.0]], payloads=[{"tags": ["a", "b"]}])


# --------------------------------------------------------------------------- names


def test_safe_name_passes_legal_names_through_unchanged():
    for name in ("aegis_lite_chunks", "aegis_mem_memory_fact_d4", "abc"):
        assert _safe_name(name) == name


def test_safe_name_normalises_illegal_names_injectively():
    """Short/odd names must become legal, and stay distinct from one another."""
    illegal = ["c", "", "x", "a/b", "тест", "_lead", "trail_"]
    mapped = [_safe_name(n) for n in illegal]
    assert len(set(mapped)) == len(mapped)  # injective — no two names collide
    for name in mapped:
        assert 3 <= len(name) <= 512
        assert name[0].isalnum() and name[-1].isalnum()
        assert all(ch.isalnum() or ch in "._-" for ch in name)


def test_a_one_character_collection_name_actually_works():
    """A 1-char name is not a legal collection/directory name; the store still serves it."""
    store = _store()
    store.ensure_collection("c", dim=2)
    store.upsert("c", ids=["a"], vectors=[[1.0, 0.0]], payloads=[{"v": 1}])
    assert store.search("c", [1.0, 0.0], k=1)[0].id == "a"


def test_normalised_names_stay_distinct_collections():
    store = _store()
    store.ensure_collection("c", dim=2)
    store.ensure_collection("x", dim=2)
    store.upsert("c", ids=["a"], vectors=[[1.0, 0.0]], payloads=[{"in": "c"}])
    assert store.search("x", [1.0, 0.0], k=5) == []


# --------------------------------------------------------------------------- persistence


def test_on_disk_store_persists_across_clients(tmp_path):
    """The embedded on-disk mode is a genuine store, not a per-process scratch index."""
    path = str(tmp_path / "qdrant")
    first = QdrantVectorStore.local(path=path)
    assert first.mode == "local"
    assert first.location == path
    first.ensure_collection("aegis_persist", dim=2)
    first.upsert("aegis_persist", ids=["a"], vectors=[[1.0, 0.0]], payloads=[{"v": 7}])
    first.close()

    second = QdrantVectorStore.local(path=path)
    hits = second.search("aegis_persist", [1.0, 0.0], k=5)
    assert [h.id for h in hits] == ["a"]
    assert hits[0].payload["v"] == 7
    second.close()


# --------------------------------------------------------------- the multi-worker guard


def test_embedded_store_refuses_to_boot_under_workers_gt_1(monkeypatch):
    """§9.1: an embedded store in a multi-worker process refuses, naming the fix.

    This is the whole point of deleting Chroma rather than demoting it. An embedded
    store is single-process whichever engine backs it, and the failure it produces under
    a second worker looks like corruption rather than a configuration error — so the
    configuration is refused at construction instead of being survived.
    """
    monkeypatch.setattr(sys, "argv", ["uvicorn", "app.main:app", "--workers", "2"])
    with pytest.raises(EmbeddedVectorStoreMultiprocessError) as ei:
        QdrantVectorStore.local()
    message = str(ei.value)
    assert "2 workers" in message
    assert "AEGIS_VECTOR_STORE_URL" in message  # the message carries its own fix


def test_the_guard_reads_the_process_manager_environment_too(monkeypatch):
    """``WEB_CONCURRENCY`` asks for workers without ever touching ``sys.argv``."""
    monkeypatch.setattr(sys, "argv", ["uvicorn", "app.main:app"])
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    with pytest.raises(EmbeddedVectorStoreMultiprocessError):
        QdrantVectorStore.local()


def test_one_worker_is_not_refused(monkeypatch):
    """The guard must not fire on the single-worker dev/test case it is aimed past."""
    monkeypatch.setattr(sys, "argv", ["uvicorn", "app.main:app", "--workers=1"])
    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    assert QdrantVectorStore.local().mode == "local"


def test_a_server_store_is_never_refused_for_being_multi_worker(monkeypatch):
    """The server mode is the *answer* to more than one worker, so it must survive it."""
    monkeypatch.setattr(sys, "argv", ["uvicorn", "app.main:app", "--workers", "8"])
    assert configured_worker_count() == (8, "--workers on the command line")
    if not _live_server():
        pytest.skip(f"no Qdrant node on {_LIVE_URL}")
    assert QdrantVectorStore.server(url=_LIVE_URL, timeout=2.0).mode == "server"


# ------------------------------------------------------------------- the live server


#: The node the repo's own ``QDRANT_URL`` points at. These tests skip when it is down —
#: the offline in-process tests above still cover the contract — but when it is up they
#: are the only proof that the *server* path, the one Windows runs, actually works.
_LIVE_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")


def _live_server() -> bool:
    try:
        QdrantVectorStore.server(url=_LIVE_URL, timeout=2.0).close()
    except Exception:  # noqa: BLE001 - absence of a node is a skip, not a failure
        return False
    return True


def test_live_qdrant_server_round_trip():
    """Write through Aegis's own path into a real node, read it back, then clean up."""
    if not _live_server():
        pytest.skip(f"no Qdrant node on {_LIVE_URL}")
    store = QdrantVectorStore.server(url=_LIVE_URL, timeout=10.0)
    name = f"aegis-livetest-{uuid.uuid4().hex[:8]}"
    try:
        store.ensure_collection(name, dim=3)
        store.upsert(
            name,
            ids=["doc-0#3", "doc-1#0"],
            vectors=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            payloads=[{"tenant_id": None}, {"tenant_id": 7}],
        )
        hits = store.search(name, [1.0, 0.0, 0.0], k=5)
        assert [h.id for h in hits] == ["doc-0#3", "doc-1#0"]
        assert hits[0].score == pytest.approx(1.0)
        # The null-scope filter is exact against the real engine too, not just in-process.
        scoped = store.search(name, [1.0, 0.0, 0.0], k=5, filter={"tenant_id": None})
        assert [h.id for h in scoped] == ["doc-0#3"]
    finally:
        store._client.delete_collection(_safe_name(name))
        store.close()
