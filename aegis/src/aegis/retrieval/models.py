"""Typed data models for the retrieval pipeline.

These are the public shapes crossing the retrieval boundary. `RetrievalResult` and
`IngestReport` are the two module-level contract types; the rest are
internal-but-typed structures used between the recall, rerank, and assembly stages.

The graph delta reuses the `GraphNode` / `GraphEdge` schemas from
:mod:`aegis.retrieval.types` (never redefined here) so a frontend viz can animate
straight from a retrieval result.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from aegis.retrieval.types import FusionMethod, GraphEdge, GraphNode, RetrievalOrigin

# ─────────────────────────────────────────────────────────────────────────────
# Ingestion-side models
# ─────────────────────────────────────────────────────────────────────────────


class Chunk(BaseModel):
    """A single chunk of a source document, ready for extraction/embedding."""

    id: str = Field(description="Stable content-addressed id for the chunk.")
    doc_id: str = Field(description="Id of the document this chunk came from.")
    ordinal: int = Field(description="0-based position of the chunk within its document.")
    text: str = Field(description="The chunk's text content.")
    metadata: dict = Field(default_factory=dict, description="Free-form provenance metadata.")


class IngestReport(BaseModel):
    """Summary of an `ingest()` run (what was written, skipped, and rejected)."""

    documents: int = Field(default=0, description="Number of source documents processed.")
    chunks_written: int = Field(default=0, description="Chunks accepted and written to stores.")
    chunks_skipped: int = Field(
        default=0, description="Chunks dropped as exact/near duplicates within this batch."
    )
    chunks_duplicate: int = Field(
        default=0,
        description="Chunks skipped because identical content was already ingested "
        "(idempotency / incremental re-ingest).",
    )
    chunks_rejected: int = Field(
        default=0, description="Chunks rejected by content validation (poisoning defense)."
    )
    entities: int | None = Field(
        default=0,
        description="Entities extracted into the graph by this ingest, or ``None`` when "
        "the active backend cannot report a real count (never a fabricated number).",
    )
    relations: int | None = Field(
        default=0,
        description="Relationships extracted into the graph by this ingest, or ``None`` "
        "when the active backend cannot report a real count (never fabricated).",
    )
    rejections: list[str] = Field(
        default_factory=list, description="Human-readable reasons for each rejected chunk."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Retrieval-side models
# ─────────────────────────────────────────────────────────────────────────────


class Candidate(BaseModel):
    """A wide-recall candidate passage awaiting rerank."""

    id: str = Field(description="Identifier for the candidate (chunk id or synthetic index).")
    text: str = Field(description="Raw candidate text (NOT yet spotlighted).")
    score: float = Field(default=0.0, description="Recall or rerank score (higher is better).")
    metadata: dict = Field(default_factory=dict, description="Provenance metadata.")


class Source(BaseModel):
    """A citation-grade source backing the assembled answer context."""

    id: str = Field(description="Source/chunk identifier.")
    text: str = Field(description="The raw source text (for citation display).")
    score: float = Field(default=0.0, description="Final rerank relevance score.")
    metadata: dict = Field(default_factory=dict, description="Provenance metadata.")


class GraphDelta(BaseModel):
    """Nodes and edges touched by a retrieval, for the live graph visualisation."""

    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class Recall(BaseModel):
    """Output of the wide-recall stage: candidates plus the touched graph slice."""

    candidates: list[Candidate] = Field(default_factory=list)
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class CacheProvenance(BaseModel):
    """Where a cache-served result actually came from — never silent.

    Present only when a result was served from the cache, so the UI/audit can show
    "answered from cache of query X at T" instead of laundering a stale answer.
    """

    kind: str | None = Field(
        default=None, description="'cache-exact' | 'cache-near' when cache-served."
    )
    original_query: str | None = Field(
        default=None, description="The original query whose result was reused."
    )
    cached_at: str | None = Field(
        default=None, description="ISO 8601 UTC time the cached entry was written."
    )


class Provenance(BaseModel):
    """Per-result retrieval provenance: origins, fusion method, cache lineage.

    Defaults are empty so existing code that constructs :class:`RetrievalResult`
    without provenance is unaffected; the hybrid-retrieval phase populates it.

    Attributes:
        origins: Which recall sources contributed (vector / graph / bm25 / cache).
        fusion: How the ranked lists were combined (none / rrf / mix).
        cache: Cache lineage when the result was served from cache, else ``None``.
    """

    origins: list[RetrievalOrigin] = Field(default_factory=list)
    fusion: FusionMethod = FusionMethod.NONE
    cache: CacheProvenance | None = None


class RetrievalResult(BaseModel):
    """The public result of `retrieve()`.

    Attributes:
        answer_context: Spotlighted, rerank-ordered context to feed the generator.
        sources: The top sources backing the context (for citations).
        num_candidates: The wide-recall pool size *before* rerank (the "N recalled"
            in the "N recalled → K survivors" funnel). ``len(sources)`` is the
            post-rerank survivor count K; this is the honest N it was drawn from.
        graph_delta: Graph nodes/edges touched, for the frontend viz.
        cache_hit: Whether this result was served from the semantic cache.
        provenance: Per-result origin/fusion/cache lineage. Empty by default.
        query_vec: The query embedding computed this turn, surfaced for the "free"
            episodic-write reuse — populated **only** when it is a real gateway vector
            of the configured embedding dimension. ``None`` on an exact/near cache hit
            (never computed) and in lite/reduced-dim mode (not recall-comparable), so a
            downstream write never stores a mismatched-dim vector.
        query_vec_dim: The dimensionality of the computed query embedding (even when
            ``query_vec`` is left ``None`` because it did not match the configured
            dimension), or ``None`` when no embedding was computed (cache hit).
    """

    answer_context: str
    sources: list[Source] = Field(default_factory=list)
    num_candidates: int = Field(
        default=0,
        description="Wide-recall candidate count before rerank (>= len(sources)).",
    )
    graph_delta: GraphDelta = Field(default_factory=GraphDelta)
    cache_hit: bool = False
    provenance: Provenance = Field(default_factory=Provenance)
    query_vec: list[float] | None = Field(
        default=None,
        description="Query embedding reusable for episodic write; only a real "
        "configured-dimension gateway vector, else None (cache hit / lite mode).",
    )
    query_vec_dim: int | None = Field(
        default=None, description="Dimensionality of the computed query embedding, if any."
    )
