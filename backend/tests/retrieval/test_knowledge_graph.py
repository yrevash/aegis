"""Tests for the durable Neo4j knowledge graph behind ``GET /graph``.

The visualisation reads the *whole* graph LightRAG's extractor built in Neo4j, not
the per-process slice one query happened to touch. These tests pin the two properties
that matter and need no live Neo4j: the capability is detected structurally, and an
unreadable store yields an honest ``None`` rather than a fabricated empty graph.
"""

from __future__ import annotations

import pytest
from aegis.retrieval.protocols import GraphBackend
from aegis.retrieval.types import GraphEdge, GraphNode, RetrievalScope


class _GraphfulBackend:
    """A backend that can return its whole graph."""

    async def knowledge_graph(
        self, *, max_nodes: int = 500
    ) -> tuple[list[GraphNode], list[GraphEdge]] | None:
        return (
            [GraphNode(id="n1", label="Aegis Gateway", kind="system")],
            [GraphEdge(source="n1", target="n1", relation="self")],
        )


class _GraphlessBackend:
    """A backend with no graph at all (the databaseless lite retriever)."""

    async def recall(self, query: str, *, top_k: int, scope: RetrievalScope):
        raise NotImplementedError


def test_graph_capability_is_detected_structurally():
    """A backend is treated as graph-capable iff it implements the protocol."""
    assert isinstance(_GraphfulBackend(), GraphBackend)
    assert not isinstance(_GraphlessBackend(), GraphBackend)


@pytest.mark.asyncio
async def test_graphless_backend_yields_none_not_empty(monkeypatch):
    """A backend with no graph returns None — 'unknown', never a fake empty graph.

    'We know nothing' and 'we cannot see what we know' are different claims, and the
    viz must not render the second as the first.
    """
    from app.retrieval import pipeline

    class _R:
        backend = _GraphlessBackend()

    monkeypatch.setattr(pipeline, "_get_retriever", lambda: _R())
    assert await pipeline.knowledge_graph() is None


@pytest.mark.asyncio
async def test_graph_backend_returns_the_whole_graph(monkeypatch):
    """A graph-capable backend's nodes/edges are passed through verbatim."""
    from app.retrieval import pipeline

    class _R:
        backend = _GraphfulBackend()

    monkeypatch.setattr(pipeline, "_get_retriever", lambda: _R())
    result = await pipeline.knowledge_graph()
    assert result is not None
    nodes, edges = result
    assert [n.label for n in nodes] == ["Aegis Gateway"]
    assert [e.relation for e in edges] == ["self"]


@pytest.mark.asyncio
async def test_unreadable_store_yields_none(monkeypatch):
    """When the graph store is unreachable the accessor reports None."""
    from aegis.retrieval import lightrag_backend as lb

    class _NoGraphRag:
        chunk_entity_relation_graph = None

    assert await lb._read_knowledge_graph(_NoGraphRag()) is None

    class _RaisingStore:
        async def get_knowledge_graph(self, *_a, **_k):
            raise RuntimeError("neo4j unreachable")

    class _RaisingRag:
        chunk_entity_relation_graph = _RaisingStore()

    assert await lb._read_knowledge_graph(_RaisingRag()) is None
