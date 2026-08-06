"""Agentic-RAG retrieval package.

Public surface (the `app.retrieval` contract from `docs/AGENT_BRIEF.md`):

* `retrieve(query, *, persona=None) -> RetrievalResult` — semantic cache in front of
  two-stage graph+vector retrieval with LLM-as-reranker and Spotlighting.
* `ingest(docs) -> IngestReport` — validated ingestion into LightRAG (Neo4j + pgvector).

The pipeline (LightRAG), stores (Neo4j graph + Postgres/pgvector), reranker (LLM-as-
reranker via the gateway), and semantic cache (Redis) are documented in `NOTES.md`.
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
    Retriever,
    build_default_retriever,
    ingest,
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
    "Retriever",
    "Source",
    "build_default_retriever",
    "ingest",
    "retrieve",
]
