"""Hybrid retrieval — public interface.

Structure-aware chunking → dedup → poisoning validation → hybrid recall (vector +
graph + hand-rolled BM25) → Reciprocal Rank Fusion → LLM-as-reranker → spotlighted
assembly, with a two-tier semantic cache, an agentic Self-RAG loop, and honest
provenance/citations. LLM-agnostic (inject a completer + embedder); heavy deps
(lightrag/neo4j/redis/qdrant_client/asyncpg) are lazy-imported, so `import aegis.retrieval`
never requires them — see `aegis[retrieval]` and `tests/retrieval/test_isolation.py`.

Typical lifecycle::

    from aegis.retrieval import RetrievalConfig, build_default_retriever

    retriever = build_default_retriever(complete=my_complete, embed=my_embed)
    report = await retriever.ingest(["some document text", ...])
    result = await retriever.retrieve("a question about the corpus")
    result.answer_context   # spotlighted, rerank-ordered context for the generator
    result.sources          # citation-grade sources
    result.provenance       # origins + fusion method (+ cache lineage on a hit)

A databaseless equivalent lives in :mod:`aegis.retrieval.memory`
(`build_lite_retriever` + `InMemoryKnowledgeBackend.from_corpus`), and a bounded
Self-RAG/FLARE iterative loop in :mod:`aegis.retrieval.agentic` (`agentic_retrieve`).
"""

from __future__ import annotations

from aegis.retrieval.models import (
    CacheProvenance,
    Candidate,
    Chunk,
    GraphDelta,
    IngestReport,
    Provenance,
    Recall,
    RetrievalResult,
    Source,
)
from aegis.retrieval.pipeline import (
    EMBED_DIM,
    RetrievalConfig,
    Retriever,
    build_default_retriever,
)
from aegis.retrieval.types import FusionMethod, GraphEdge, GraphNode, RetrievalOrigin

__all__ = [
    "EMBED_DIM",
    "CacheProvenance",
    "Candidate",
    "Chunk",
    "FusionMethod",
    "GraphDelta",
    "GraphEdge",
    "GraphNode",
    "IngestReport",
    "Provenance",
    "Recall",
    "RetrievalConfig",
    "RetrievalOrigin",
    "RetrievalResult",
    "Retriever",
    "Source",
    "build_default_retriever",
]
