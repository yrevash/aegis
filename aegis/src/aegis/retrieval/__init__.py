"""Hybrid retrieval — public interface.

Structure-aware chunking → dedup → poisoning validation → hybrid recall (vector +
graph, plus a hand-rolled BM25 arm when the backend can search its corpus by keyword —
otherwise BM25 is a labelled re-ranking pass, never a claimed recall arm) → Reciprocal
Rank Fusion → local ONNX cross-encoder rerank (LLM-as-reranker behind it, loudly) →
spotlighted assembly, with a two-tier semantic cache,
an agentic Self-RAG loop, and honest provenance/citations. LLM-agnostic (inject a
completer + embedder); heavy deps
(lightrag/neo4j/redis/chromadb/asyncpg) are lazy-imported, so `import aegis.retrieval`
never requires them — see `aegis[retrieval]` and `tests/retrieval/test_isolation.py`.

Typical lifecycle::

    from aegis.retrieval import RetrievalConfig, RetrievalScope, build_default_retriever

    retriever = build_default_retriever(complete=my_complete, embed=my_embed)
    scope = RetrievalScope(tenant_id=7, persona="ops")   # required, never defaulted
    report = await retriever.ingest(["some document text", ...], scope=scope)
    result = await retriever.retrieve("a question about the corpus", scope=scope)
    result.answer_context   # spotlighted, rerank-ordered context for the generator
    result.sources          # citation-grade sources
    result.provenance       # origins + fusion method (+ cache lineage on a hit)

A databaseless equivalent lives in :mod:`aegis.retrieval.memory`
(`build_lite_retriever` + `InMemoryKnowledgeBackend.from_corpus`), and a bounded
Self-RAG/FLARE iterative loop in :mod:`aegis.retrieval.agentic` (`agentic_retrieve`).
"""

from __future__ import annotations

from aegis.retrieval.citations import (
    Citation,
    CitationCheck,
    CitationStatus,
    citation_validity,
    normalise_span,
    span_present,
    verify_citations,
)
from aegis.retrieval.local_reranker import (
    DEFAULT_LOCAL_RERANK_MODEL,
    LocalCrossEncoderReranker,
)
from aegis.retrieval.models import (
    AgenticReport,
    ArmReport,
    CacheProvenance,
    Candidate,
    Chunk,
    GraphDelta,
    IngestReport,
    KeywordReport,
    Provenance,
    Recall,
    RerankEngine,
    RerankReport,
    RetrievalObservability,
    RetrievalResult,
    RewriteReport,
    Source,
)
from aegis.retrieval.pipeline import (
    EMBED_DIM,
    RetrievalConfig,
    Retriever,
    build_default_retriever,
)
from aegis.retrieval.types import (
    ALL_TENANTS,
    TENANT_METADATA_KEY,
    AllTenants,
    FusionMethod,
    GraphEdge,
    GraphNode,
    RetrievalOrigin,
    RetrievalScope,
    TenantScope,
    UnresolvedTenantScopeError,
    UntenantedPrincipalError,
    principal_tenant_scope,
    tenant_filter,
)
from aegis.retrieval.vector_store import (
    ChromaVectorStore,
    VectorStoreNotConfiguredError,
    configure_vector_store,
    new_default_store,
    reset_vector_store,
)

__all__ = [
    "ChromaVectorStore",
    "VectorStoreNotConfiguredError",
    "configure_vector_store",
    "new_default_store",
    "reset_vector_store",
    "ALL_TENANTS",
    "DEFAULT_LOCAL_RERANK_MODEL",
    "EMBED_DIM",
    "TENANT_METADATA_KEY",
    "AgenticReport",
    "AllTenants",
    "ArmReport",
    "CacheProvenance",
    "Candidate",
    "Chunk",
    "Citation",
    "CitationCheck",
    "CitationStatus",
    "FusionMethod",
    "GraphDelta",
    "GraphEdge",
    "GraphNode",
    "IngestReport",
    "KeywordReport",
    "LocalCrossEncoderReranker",
    "Provenance",
    "Recall",
    "RerankEngine",
    "RerankReport",
    "RetrievalConfig",
    "RetrievalObservability",
    "RetrievalOrigin",
    "RetrievalResult",
    "RetrievalScope",
    "Retriever",
    "RewriteReport",
    "Source",
    "TenantScope",
    "UnresolvedTenantScopeError",
    "UntenantedPrincipalError",
    "build_default_retriever",
    "citation_validity",
    "normalise_span",
    "principal_tenant_scope",
    "span_present",
    "tenant_filter",
    "verify_citations",
]
