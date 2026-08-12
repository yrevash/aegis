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


# ─────────────────────────────────────────────────────────────────────────────
# Observability: WHICH arsenal methods actually ran, with REAL measured numbers.
# ─────────────────────────────────────────────────────────────────────────────


class ArmReport(BaseModel):
    """One recall arm's measured contribution, before fusion.

    A "fired" arm is one that produced at least one candidate for this query — so a
    consumer can see, honestly, that (say) the graph arm ran but returned nothing
    while the vector and bm25 arms did produce candidates.

    Attributes:
        origins: The retrieval origin(s) this arm represents (usually one; a
            pre-blended backend list may carry several, e.g. vector+graph).
        candidates: The number of candidates this arm produced (pre-fusion, measured).
        fired: Whether this arm produced any candidate (``candidates > 0``).
    """

    origins: list[RetrievalOrigin] = Field(default_factory=list)
    candidates: int = 0
    fired: bool = False


class RerankReport(BaseModel):
    """Whether the LLM reranker ran, and the top rerank scores it produced.

    Attributes:
        ran: Whether the second-stage LLM rerank executed (``False`` when the
            ``rerank_enabled`` knob is off — the fused RRF order is kept instead).
        input_candidates: How many fused candidates were offered to rerank (measured).
        kept: How many candidates survived into the final sources (``final_top_k`` cap).
        top_scores: The survivors' scores in final order — real rerank grades when
            ``ran`` is true, else the fused RRF scores that were kept.
    """

    ran: bool = False
    input_candidates: int = 0
    kept: int = 0
    top_scores: list[float] = Field(default_factory=list)


class RewriteReport(BaseModel):
    """Whether a context-aware query rewrite ran before retrieval.

    Populated by the layer that owns the rewrite (the agentic loop / orchestrator),
    ``None`` when this result came straight from a single ``retrieve()`` with no rewrite.

    Attributes:
        ran: Whether a rewrite call was made at all.
        changed: Whether the rewrite actually differed from the original query.
        original: The query as it came in.
        rewritten: The standalone query retrieval actually ran with.
    """

    ran: bool = False
    changed: bool = False
    original: str | None = None
    rewritten: str | None = None


class AgenticReport(BaseModel):
    """Whether the bounded Self-RAG loop iterated, and how many times.

    Populated by :func:`aegis.retrieval.agentic.agentic_retrieve`; ``None`` for a
    single-shot ``retrieve()``.

    Attributes:
        ran: Whether the agentic loop wrapped this retrieval.
        used_rounds: How many retrieval passes actually ran (``>= 1``, ``<= max``).
        max_rounds: The configured upper bound on retrieval passes.
        round_queries: The query actually retrieved with, per round, in order.
    """

    ran: bool = False
    used_rounds: int = 1
    max_rounds: int = 1
    round_queries: list[str] = Field(default_factory=list)


class RetrievalObservability(BaseModel):
    """The honest "which methods ran" record for one retrieval, with real numbers.

    Every field is *measured* by the pipeline, never fabricated: a consumer (or the
    UI) can read exactly which recall arms fired and how many candidates each
    produced, that fusion ran (and which method), whether rerank ran and its top
    scores, whether spotlighting was applied, and — when the higher layers wrapped the
    call — whether a query rewrite and the Self-RAG loop iterated.

    Attributes:
        arms: Per-recall-arm candidate counts (vector / graph / bm25), pre-fusion.
        fusion: The fusion method applied to the arms (RRF on the hybrid path).
        fused_candidates: The fused wide-recall pool size (the honest ``N``).
        rerank: Whether rerank ran and the top scores it produced.
        spotlight_applied: Whether the answer context was Microsoft-spotlighted.
        rewrite: Query-rewrite observability, or ``None`` if no rewrite layer ran.
        agentic: Self-RAG-loop observability, or ``None`` if single-shot.
    """

    arms: list[ArmReport] = Field(default_factory=list)
    fusion: FusionMethod = FusionMethod.NONE
    fused_candidates: int = 0
    rerank: RerankReport = Field(default_factory=RerankReport)
    spotlight_applied: bool = False
    rewrite: RewriteReport | None = None
    agentic: AgenticReport | None = None


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
        observability: The honest "which arsenal methods ran" record for this
            retrieval — measured arm counts, fusion, rerank scores, spotlight, and
            (when a higher layer wrapped the call) rewrite / Self-RAG iteration. Empty
            default so a bare-constructed result and a cache hit are unaffected.
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
    observability: RetrievalObservability = Field(default_factory=RetrievalObservability)
    query_vec: list[float] | None = Field(
        default=None,
        description="Query embedding reusable for episodic write; only a real "
        "configured-dimension gateway vector, else None (cache hit / lite mode).",
    )
    query_vec_dim: int | None = Field(
        default=None, description="Dimensionality of the computed query embedding, if any."
    )
