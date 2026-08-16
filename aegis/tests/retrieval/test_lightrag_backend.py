"""LightRAG backend: entity/relation counts are MEASURED from the graph, not faked.

These tests pin the honesty fix for the graph-ingest count: ``ingest_chunks`` must
report the real ``(entities, relations)`` this ingest added to LightRAG's knowledge
graph (the node/edge delta read back from the graph store), and must NOT return a
hardcoded ``(0, 0)``. LightRAG itself is never imported: we inject a fake ``rag`` into
the backend (the same ``_rag`` cache ``_ensure`` fills), which is the real seam the
production path uses.
"""

from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace

import pytest

from aegis.retrieval.lightrag_backend import LightRAGBackend
from aegis.retrieval.models import Chunk
from aegis.retrieval.pipeline import RetrievalConfig, Retriever
from aegis.retrieval.types import RetrievalScope

from .conftest import FakeRedis, RecordingComplete, SequenceEmbed

#: The unscoped (no tenant) partition these tests run under.
_SCOPE = RetrievalScope(tenant_id=None)


class FakeGraphStore:
    """Stand-in for LightRAG's ``chunk_entity_relation_graph`` store.

    Exposes ``get_knowledge_graph`` exactly like LightRAG's ``BaseGraphStorage`` — the
    accessor the backend uses to count the live graph — over in-memory node/edge lists.
    """

    def __init__(self) -> None:
        self.nodes: list[str] = []
        self.edges: list[tuple[str, str]] = []

    async def get_knowledge_graph(self, node_label: str, max_nodes: int = 1000) -> object:
        assert node_label == "*"  # backend snapshots the whole graph
        return SimpleNamespace(nodes=list(self.nodes), edges=list(self.edges))


class FakeRag:
    """Fake LightRAG that 'extracts' entities/relations into its graph on insert.

    Each inserted chunk merges two entities and one relationship, so the graph grows by
    a known, measurable amount — the exact thing ``ingest_chunks`` must read back.
    """

    def __init__(self, *, graph: FakeGraphStore | None = None) -> None:
        self.chunk_entity_relation_graph = graph
        self.inserts: list[dict] = []

    async def ainsert(
        self,
        texts: Sequence[str],
        ids: Sequence[str] | None = None,
        file_paths: Sequence[str] | None = None,
    ) -> None:
        self.inserts.append(
            {
                "texts": list(texts),
                "ids": list(ids or []),
                "file_paths": list(file_paths or []),
            }
        )
        if self.chunk_entity_relation_graph is None:
            return
        graph = self.chunk_entity_relation_graph
        base = len(graph.nodes)
        for i in range(len(texts)):
            a, b = f"e{base + 2 * i}", f"e{base + 2 * i + 1}"
            graph.nodes.extend([a, b])
            graph.edges.append((a, b))


def _backend(rag: FakeRag) -> LightRAGBackend:
    backend = LightRAGBackend(
        complete=RecordingComplete("{}"), embed=SequenceEmbed([1.0, 0.0])
    )
    backend._rag = rag  # inject the fake so _ensure() returns it without importing LightRAG
    return backend


def _chunks(n: int, *, prefix: str = "c") -> list[Chunk]:
    return [
        Chunk(id=f"{prefix}{i}", doc_id="d", ordinal=i, text=f"chunk {prefix} number {i}")
        for i in range(n)
    ]


async def test_ingest_reports_measured_graph_delta_not_hardcoded_zero():
    rag = FakeRag(graph=FakeGraphStore())
    backend = _backend(rag)

    entities, relations = await backend.ingest_chunks(_chunks(2))

    # Two chunks → 4 entities + 2 relations merged into the graph. The count is the
    # REAL delta read from the graph store, never the old hardcoded (0, 0).
    assert (entities, relations) == (4, 2)
    assert (entities, relations) != (0, 0)
    assert rag.inserts, "the chunks were actually inserted"


async def test_ingest_delta_is_per_ingest_not_cumulative():
    rag = FakeRag(graph=FakeGraphStore())
    backend = _backend(rag)

    first = await backend.ingest_chunks(_chunks(2, prefix="a"))
    second = await backend.ingest_chunks(_chunks(3, prefix="b"))

    assert first == (4, 2)  # first ingest's own contribution
    assert second == (6, 3)  # second ingest reports only what IT added (delta, not total)


async def test_ingest_returns_none_when_graph_store_unavailable():
    # No graph store to query → honest "unknown", never a fabricated number.
    rag = FakeRag(graph=None)
    backend = _backend(rag)

    entities, relations = await backend.ingest_chunks(_chunks(2))

    assert (entities, relations) == (None, None)
    assert rag.inserts, "chunks are still inserted even when counts are unknown"


async def test_ingest_empty_is_noop_zero():
    rag = FakeRag(graph=FakeGraphStore())
    backend = _backend(rag)

    assert await backend.ingest_chunks([]) == (0, 0)
    assert not rag.inserts  # nothing inserted for an empty batch


class QueryingRag(FakeRag):
    """A fake LightRAG that answers ``aquery`` with canned chunk rows.

    Mirrors the shape recent LightRAG returns for ``only_need_context=True``: a
    ``QueryContextResult`` carrying ``.context`` plus ``.raw_data["data"]["chunks"]``,
    each chunk with the ``file_path`` the backend wrote at ingest.
    """

    def __init__(self, chunks: list[dict]) -> None:
        super().__init__()
        self._chunks = chunks

    async def aquery(self, query: str, param: object = None) -> object:
        return SimpleNamespace(
            context="blended context",
            raw_data={"data": {"chunks": self._chunks}},
        )


async def test_ingest_tags_the_owning_tenant_into_the_stored_file_path():
    """LightRAG round-trips ``file_path`` and nothing else, so the tenant rides in it."""
    rag = FakeRag(graph=FakeGraphStore())
    backend = _backend(rag)
    await backend.ingest_chunks(
        [
            Chunk(
                id="c0",
                doc_id="d",
                ordinal=0,
                text="t",
                metadata={"source": "handbook.md", "tenant_id": "t7"},
            ),
            Chunk(id="c1", doc_id="d", ordinal=1, text="t", metadata={"source": "public.md"}),
        ]
    )
    assert rag.inserts[0]["file_paths"] == ["t7::handbook.md", "public.md"]


async def test_recall_drops_another_tenants_rows_and_restores_the_clean_source():
    rag = QueryingRag(
        [
            {"id": "a", "content": "acme secret", "file_path": "t1::acme.md"},
            {"id": "b", "content": "globex secret", "file_path": "t2::globex.md"},
            {"id": "s", "content": "shared handbook", "file_path": "handbook.md"},
        ]
    )
    recall = await _backend(rag).recall("q", top_k=5, scope=RetrievalScope(tenant_id=1))

    assert [c.id for c in recall.candidates] == ["a", "s"]
    # The tag is stripped back off, so citations read exactly as they did before.
    assert [c.metadata["file_path"] for c in recall.candidates] == ["acme.md", "handbook.md"]


async def test_unscoped_recall_sees_only_untagged_rows():
    """A null tenant reads the shared corpus, never every tenant's."""
    rag = QueryingRag(
        [
            {"id": "a", "content": "acme secret", "file_path": "t1::acme.md"},
            {"id": "s", "content": "shared handbook", "file_path": "handbook.md"},
        ]
    )
    recall = await _backend(rag).recall("q", top_k=5, scope=_SCOPE)
    assert [c.id for c in recall.candidates] == ["s"]


async def test_unattributable_blended_context_is_refused_for_a_tenant():
    """A row with no per-chunk path cannot be shown to belong to this tenant → fail loud.

    Dropping it silently would hide a store that cannot be scoped; serving it is the
    leak. Only the tenant-scoped case raises — an unscoped run has no boundary to cross.
    """
    rag = QueryingRag([])  # no structured chunks → LightRAG's whole-context fallback
    backend = _backend(rag)

    with pytest.raises(RuntimeError, match="unattributable"):
        await backend.recall("q", top_k=5, scope=RetrievalScope(tenant_id=1))

    unscoped = await backend.recall("q", top_k=5, scope=_SCOPE)
    assert [c.id for c in unscoped.candidates] == ["context"]


async def test_ingest_report_carries_real_counts_through_pipeline():
    # End-to-end: the measured counts reach IngestReport instead of a hardcoded zero.
    from aegis.retrieval.cache import SemanticCache

    rag = FakeRag(graph=FakeGraphStore())
    backend = _backend(rag)
    retriever = Retriever(
        backend=backend,
        cache=SemanticCache(FakeRedis(), ttl_seconds=60, similarity_threshold=0.95),
        complete=RecordingComplete("{}"),
        embed=SequenceEmbed([1.0, 0.0]),
        config=RetrievalConfig(),
    )

    report = await retriever.ingest(
        [{"id": "kb", "text": "The Amazon river discharges more water than any other river."}],
        scope=_SCOPE,
    )

    assert report.chunks_written >= 1
    # Real, measured extraction counts flow into the report (not the old fake 0/0).
    assert report.entities is not None and report.entities > 0
    assert report.relations is not None and report.relations > 0
    assert report.entities == 2 * report.chunks_written
    assert report.relations == report.chunks_written
