"""Backend shim: hybrid retrieval now lives in the standalone ``aegis.retrieval`` package.

This package used to own the full retrieval implementation (structure-aware
chunking, dedup, poisoning validation, hybrid vector+graph+BM25 recall, Reciprocal
Rank Fusion, reranking, spotlighted assembly, the two-tier semantic cache, and
the LightRAG/in-memory backends). That implementation has moved to the standalone,
LLM-agnostic ``aegis.retrieval`` package (see ``/aegis``) so it can be imported by any
component without pulling in this platform's LLM gateway or config. This module (and
its siblings) is the **strangler shim**: each submodule re-exports the package's
public API, and ``pipeline.py`` wires the platform's LiteLLM gateway
(``app.core.llm.complete``/``embed``, via ``gateway.py``) and ``app.config.Settings``
as the injected completer/embedder/store-config, preserving the previous public
surface and call sites (notably ``app.agent.deps.AgentDeps.default``'s
``from app.retrieval import retrieve``).

Public surface (the `app.retrieval` contract, unchanged since before the migration):

* `retrieve(query, *, scope) -> RetrievalResult` — semantic cache in front of
  two-stage graph+vector retrieval with a local cross-encoder rerank (LLM-as-reranker
  behind it) and Spotlighting. The
  `RetrievalScope` is required and is reconciled against the request's governance
  tenant before it reaches the process-wide retriever.
* `ingest(docs, *, scope) -> IngestReport` — validated ingestion into LightRAG (Neo4j +
  vectors), with the scope's tenant stamped onto every chunk written.

The pipeline (LightRAG), stores (Neo4j graph + embedded NanoVectorDB vectors + Postgres
KV), reranker (a local ONNX cross-encoder, with the LLM-as-reranker via the gateway as its
loud fallback), and semantic cache (Redis) are documented in `NOTES.md`.
"""

from __future__ import annotations

from .models import (
    CacheProvenance,
    Candidate,
    Chunk,
    GraphDelta,
    IngestReport,
    Provenance,
    RetrievalResult,
    Source,
)
from .pipeline import (
    RetrievalConfig,
    RetrievalScope,
    Retriever,
    TenantScopeMismatch,
    build_default_retriever,
    ingest,
    knowledge_graph,
    retrieve,
)

__all__ = [
    "CacheProvenance",
    "Candidate",
    "Chunk",
    "GraphDelta",
    "IngestReport",
    "Provenance",
    "RetrievalConfig",
    "RetrievalResult",
    "RetrievalScope",
    "Retriever",
    "Source",
    "TenantScopeMismatch",
    "build_default_retriever",
    "ingest",
    "knowledge_graph",
    "retrieve",
]
