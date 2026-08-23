"""Retrieval orchestration: cache → recall → rerank → spotlight → assemble.

This module wires the stages behind the `Retriever` dataclass's two entry points:

* `Retriever.retrieve(query, *, scope) -> RetrievalResult`
* `Retriever.ingest(docs, *, scope) -> IngestReport`

Both take a required :class:`~aegis.retrieval.types.RetrievalScope`. Tenant scope is a
parameter, not a convention: it keys the cache, filters the backend, and is stamped onto
every chunk written, so what one tenant stores is what that tenant — and only that
tenant — can read back. There is deliberately **no default**; an omitted scope is a
call-site error the type checker reports, not a silent unscoped query.

The rerank stage is the **local ONNX cross-encoder** (:mod:`aegis.retrieval.local_reranker`)
when the retriever was built with one, and the LLM-as-reranker
(:mod:`aegis.retrieval.reranker`) behind it on a loud failure — never a silent fall-through
to the unranked fused order. ``observability.rerank.engine`` records which one ran.

`Retriever` holds its collaborators (knowledge backend, semantic cache, completer,
embedder, local reranker) by dependency injection, so the whole flow is unit-testable with fakes and
no live Neo4j/Redis/network. There is no process-wide default instance and no
LLM-gateway binding here — a host application constructs a `Retriever` (directly, or
via :func:`build_default_retriever` / :func:`aegis.retrieval.memory.build_lite_retriever`)
with its own completer/embedder/store config.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from aegis.core.models import ModelRole
from aegis.retrieval import chunker
from aegis.retrieval.cache import SemanticCache
from aegis.retrieval.fusion import RankedList, collect_origins, reciprocal_rank_fusion
from aegis.retrieval.lightrag_backend import LightRAGBackend
from aegis.retrieval.local_reranker import (
    DEFAULT_LOCAL_RERANK_MODEL,
    LocalCrossEncoderReranker,
    rerank_scored_local_first,
)
from aegis.retrieval.models import (
    ArmReport,
    Candidate,
    Chunk,
    GraphDelta,
    IngestReport,
    KeywordReport,
    Provenance,
    RerankReport,
    RetrievalObservability,
    RetrievalResult,
    Source,
)
from aegis.retrieval.protocols import (
    CompleteFn,
    EmbedFn,
    KeywordBackend,
    KnowledgeBackend,
    MultiListBackend,
)
from aegis.retrieval.spotlight import build_plain_context, build_spotlighted_context
from aegis.retrieval.types import (
    TENANT_METADATA_KEY,
    FusionMethod,
    GraphEdge,
    GraphNode,
    RetrievalOrigin,
    RetrievalScope,
)
from aegis.retrieval.validation import validate_content

#: Dimension of the default gateway embedding (`text-embedding-3-large`); the default
#: for :attr:`RetrievalConfig.embed_dim`. A host with a different embedder overrides
#: the field on its own `RetrievalConfig`.
EMBED_DIM = 3072

#: Origins assumed for a backend that returns one pre-blended recall list (e.g. a
#: LightRAG ``mode="mix"`` blend of graph + vector), used when it does not expose
#: split lists.
_DEFAULT_RECALL_ORIGINS: tuple[RetrievalOrigin, ...] = (
    RetrievalOrigin.VECTOR,
    RetrievalOrigin.GRAPH,
)


@dataclass
class RetrievalConfig:
    """Tunables for the retrieval pipeline, plus store connection settings.

    ``semantic_threshold`` is deliberately high (near-identity): the semantic cache is a
    conservative near-exact front layer, not a broad quality shortcut. Below it a
    "semantic" match is only a prefetch hint — full recall+fusion still runs.

    The store fields (``postgres_dsn``, ``neo4j_*``, ``redis_url``, ``stores_enabled``)
    exist so :class:`~aegis.retrieval.lightrag_backend.LightRAGBackend` and
    :func:`build_default_retriever` need no host application settings object — a host
    maps its own config onto these fields (or overrides them per instance).
    """

    recall_top_k: int = 20
    final_top_k: int = 6
    rerank_role: ModelRole = ModelRole.CHEAP
    #: Second-stage rerank toggle. When ``False`` the fused RRF order is kept
    #: (truncated to ``final_top_k``) with **no** rerank model call — a real behaviour
    #: change a consumer can observe via ``observability.rerank.ran``.
    rerank_enabled: bool = True
    #: Whether the **local ONNX cross-encoder** is used when the retriever was built with
    #: one. ``False`` demotes reranking to the API reranker for the whole process — the
    #: operator kill switch for a box where the weights cannot be cached. This does not
    #: turn reranking *off*; ``rerank_enabled`` is the knob that does that.
    local_rerank_enabled: bool = True
    #: Which cross-encoder the composition roots load. A different model is a different
    #: answer order, so it is configuration, not a hard-coded literal, and it is recorded
    #: here where an eval run can read it back.
    local_rerank_model: str = DEFAULT_LOCAL_RERANK_MODEL
    #: Microsoft-Spotlighting toggle for the assembled answer context. When ``False``
    #: the context is assembled un-spotlighted (raw fenced sources) — observable via
    #: ``observability.spotlight_applied``. Defaults ON (the injection defence).
    spotlight_enabled: bool = True
    chunk_size: int = 400
    chunk_overlap: int = 60
    cache_ttl_seconds: int = 3600
    semantic_threshold: float = 0.985
    #: RRF damping constant (the only RRF tunable — RRF is deliberately *rank-based and
    #: weightless* per ``docs/architecture/system-architecture.md``, so there are no per-arm
    #: fusion weights to expose; a higher ``k`` flattens the rank weighting).
    rrf_k: int = 60
    #: Default upper bound on Self-RAG retrieval passes, read by
    #: :func:`aegis.retrieval.agentic.agentic_retrieve` when a caller does not override
    #: ``max_rounds``. ``1`` disables iteration (single-shot).
    agentic_max_rounds: int = 2
    #: Whether a context-aware query rewrite should run before retrieval. Declarative
    #: default for the orchestration layer that owns the rewrite call (the rewrite
    #: itself lives in :mod:`aegis.retrieval.query_rewrite`, outside ``retrieve()``).
    query_rewrite_enabled: bool = True
    #: Dimensionality of a "real" (non-lite) query embedding; gates `query_vec` reuse.
    embed_dim: int = EMBED_DIM
    #: Store connection settings (only read by the LightRAG-backed production path).
    postgres_dsn: str = "postgresql://postgres:postgres@localhost:5432/aegis"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    redis_url: str = "redis://localhost:6379/0"
    #: URL of the **Qdrant node** both vector consumers write to: LightRAG's
    #: ``QdrantVectorDBStorage`` and :mod:`aegis.retrieval.vector_store` (Postgres stays
    #: for KV/doc-status only). One engine, one URL — §9.1. Qdrant v1.19.0 ships as a
    #: single Windows binary in a zip, so this is still an install with no Docker and no
    #: installer; it is simply not *in-process* any more, which is what lets more than one
    #: uvicorn worker share an index.
    qdrant_url: str = "http://localhost:6333"
    #: Optional API key for a secured Qdrant node (empty means an unauthenticated node).
    qdrant_api_key: str = ""
    #: LightRAG's local **working directory** — its own bookkeeping, no longer any
    #: vectors. Kept because LightRAG requires the argument; the vector tier moved to
    #: ``qdrant_url`` and the KV/doc-status tier is on Postgres.
    vector_store_path: str = "vector_storage"
    #: Whether the real databases (Neo4j/Redis) are expected to be in use.
    #: Purely informational at this layer — callers decide what to build from it
    #: (e.g. `build_default_retriever` vs `aegis.retrieval.memory.build_lite_retriever`).
    stores_enabled: bool = True


@dataclass
class Retriever:
    """Agentic-RAG retriever: semantic cache in front of two-stage graph+vector retrieval."""

    backend: KnowledgeBackend
    cache: SemanticCache
    complete: CompleteFn
    embed: EmbedFn
    config: RetrievalConfig = field(default_factory=RetrievalConfig)
    #: The **local ONNX cross-encoder** used for second-stage reranking, injected like every
    #: other collaborator. ``None`` means this retriever reranks through the API reranker —
    #: which is a legitimate configuration but a worse-ordered one (our A3 → A4 ablation
    #: puts the local encoder at **MRR@20 +12.9 pp** and recall@6 +0.009 — it reorders the
    #: pool, it does not widen it; the +12.1 pp recall@5 figure quoted elsewhere is
    #: T2-RAGBench's, not ours), so the composition roots
    #: (:func:`build_default_retriever`, :func:`aegis.retrieval.memory.build_lite_retriever`)
    #: wire one in and only ``local_rerank_enabled=False`` takes it back out. Constructing
    #: it is free; the weights load on the first query that uses it.
    local_reranker: LocalCrossEncoderReranker | None = None
    #: ``(tenant_id, content_hash)`` pairs already accepted, so re-ingesting a corpus is
    #: idempotent and incremental (only genuinely new chunks reach the backend).
    #: Process-scoped; the LightRAG backend additionally dedupes by content hash in its
    #: own store. The tenant is part of the key because the *same* document ingested by
    #: two tenants is two genuinely distinct rows — deduping it away would leave the
    #: second tenant unable to retrieve its own document.
    _seen_hashes: set[tuple[int | None, str]] = field(default_factory=set)

    async def retrieve(self, query: str, *, scope: RetrievalScope) -> RetrievalResult:
        """Answer a retrieval request end-to-end, inside ``scope``.

        Flow: near-exact cache → **hybrid wide recall** (vector + graph, plus a BM25
        keyword arm when the backend can search its corpus by keyword, fused by
        Reciprocal Rank Fusion) → cross-encoder rerank → spotlight → assemble → write back to
        cache. An exact or near-exact (cosine ≥ ``semantic_threshold``) cache hit
        returns immediately with `cache_hit=True` and honest cache provenance; anything
        below that runs the full fused pipeline (the sub-threshold match is only a
        prefetch hint, never a substituted answer).

        ``scope`` reaches every stage that could otherwise cross a tenant boundary: both
        cache tiers are *partitioned* by it (not filtered after the fact), and it is
        forwarded to the backend so each recall arm applies the tenant predicate to the
        rows it scans.

        Args:
            query: The user query.
            scope: The tenant / persona / corpus-version this request runs inside.
                **Required, with no default** — see the module docstring.

        Returns:
            A `RetrievalResult` with spotlighted context, sources, a graph delta, the
            cache-hit flag, and populated `provenance` (origins + RRF fusion).
        """
        exact = await self.cache.get_exact(query, scope)
        if exact is not None:
            return exact

        query_vec = (await self.embed([query]))[0]
        semantic = await self.cache.get_semantic(query_vec, scope)
        if semantic is not None:
            return semantic

        fused, origins, nodes, edges, arms, keyword = await self._recall_and_fuse(
            query, scope
        )
        if self.config.rerank_enabled:
            # Local cross-encoder first; the API reranker behind it on a LOUD failure. The
            # one thing that never happens here is falling through to no rerank at all.
            outcome = await rerank_scored_local_first(
                query,
                fused,
                complete=self.complete,
                top_k=self.config.final_top_k,
                local=(
                    self.local_reranker if self.config.local_rerank_enabled else None
                ),
                role=self.config.rerank_role,
            )
            top = outcome.candidates
            rerank_report = RerankReport(
                ran=True,
                engine=outcome.engine,
                graded=outcome.graded,
                input_candidates=len(fused),
                kept=len(top),
                ungraded=outcome.ungraded,
                degraded_reason=outcome.reason,
                top_scores=[c.score for c in top],
            )
        else:
            # Knob off: keep the fused RRF order, no rerank model call. Nothing is
            # graded and nothing degraded — the scores are honestly RRF scores.
            top = fused[: self.config.final_top_k]
            rerank_report = RerankReport(
                ran=False,
                engine="none",
                graded=False,
                input_candidates=len(fused),
                kept=len(top),
                ungraded=len(top),
                top_scores=[c.score for c in top],
            )
        result = self._assemble(
            top,
            num_candidates=len(fused),
            recall_nodes=nodes,
            recall_edges=edges,
            origins=origins,
            arms=arms,
            rerank=rerank_report,
            keyword=keyword,
        )
        await self.cache.set(query, scope, query_vec, result)
        # Surface the query embedding for the "free" episodic-write reuse — but only on
        # the returned object, AFTER caching, so the (serialised) cache never stores a
        # large float blob and a later exact/near cache hit correctly yields
        # query_vec=None (that path recomputes no embedding). Reuse is gated to a real
        # embed_dim-dimensioned vector; a lite/reduced-dim vector is recorded by its
        # dimension but not reused.
        vec_dim = len(query_vec)
        result.query_vec = query_vec if vec_dim == self.config.embed_dim else None
        result.query_vec_dim = vec_dim
        return result

    async def _recall_and_fuse(
        self, query: str, scope: RetrievalScope
    ) -> tuple[
        list[Candidate],
        list[RetrievalOrigin],
        list[GraphNode],
        list[GraphEdge],
        list[ArmReport],
        KeywordReport,
    ]:
        """Run hybrid wide recall and fuse the ranked lists with RRF.

        Produces the dense/graph list(s) from the backend, adds the BM25/keyword list
        (see :meth:`_keyword_signal` for what that list *is* in each case), and fuses
        everything with Reciprocal Rank Fusion. The fused pool is the honest wide-recall
        count (N) fed to rerank.

        Args:
            query: The user query.
            scope: The tenant/persona partition; forwarded to every recall arm so each
                one filters the rows it scans rather than trusting a later stage to.

        Returns:
            A tuple ``(fused_candidates, origins, nodes, edges, arms, keyword)`` where
            ``origins`` are the distinct retrieval origins that produced a surviving
            candidate, ``arms`` are the per-*recall-arm* measured candidate counts
            *before* fusion, and ``keyword`` records what the keyword signal was. A
            pool-scoped keyword pass is fused but is neither an arm nor an origin.
        """
        lists, nodes, edges = await self._recall_lists(query, scope)
        keyword_list, keyword = await self._keyword_signal(query, lists, scope)
        recall_arms = [*lists, keyword_list] if keyword.adds_recall else list(lists)
        fused = reciprocal_rank_fusion([*lists, keyword_list], k=self.config.rrf_k)
        arms = [
            ArmReport(
                origins=list(ranked.origins),
                candidates=len(ranked.candidates),
                fired=bool(ranked.candidates),
            )
            for ranked in recall_arms
        ]
        return fused, collect_origins(fused), nodes, edges, arms, keyword

    async def _keyword_signal(
        self, query: str, lists: Sequence[RankedList], scope: RetrievalScope
    ) -> tuple[RankedList, KeywordReport]:
        """Produce the BM25/keyword ranked list — and say honestly what it is.

        There are two genuinely different things a keyword pass can be here, and the
        pipeline refuses to blur them (this is the honest-provenance rule applied to our
        own arsenal claims):

        * The backend implements :class:`~aegis.retrieval.protocols.KeywordBackend`, so
          BM25 runs over the **whole corpus** with corpus-wide IDF. It can surface a
          document no dense/graph arm returned, so it is a real recall arm: tagged
          ``BM25``, counted in ``arms``, and present in ``provenance.origins``.
        * The backend cannot search by keyword. Then all we can score is the pool the
          dense/graph arms already recalled — perhaps 20 documents. That reorders the
          pool (which is worth doing, and RRF still fuses it) but it **cannot add
          recall**, and its IDF over 20 documents is not a corpus statistic. Reporting
          it as a firing retrieval arm would claim recall that never happened, so the
          list carries **no origin** and the pass is reported only as what it is: a
          re-ranking step, via ``KeywordReport(scope="pool")``.

        Args:
            query: The user query.
            lists: The dense/graph ranked lists already recalled this turn.
            scope: The tenant/persona partition. The corpus-wide branch forwards it so
                the keyword arm carries the same tenant predicate as the dense arms; the
                pool branch inherits scoping for free, because the pool it re-scores was
                itself recalled under ``scope``.

        Returns:
            ``(ranked_list, report)`` — the list to fuse and the honest label for it.
        """
        if isinstance(self.backend, KeywordBackend):
            hits = list(
                await self.backend.keyword_recall(
                    query, top_k=self.config.recall_top_k, scope=scope
                )
            )
            return (
                RankedList(origins=(RetrievalOrigin.BM25,), candidates=hits),
                KeywordReport(
                    ran=True, scope="corpus", matched=len(hits), adds_recall=True
                ),
            )

        scored = bm25_ranked(query, _unique_candidates(lists))
        return (
            RankedList(origins=(), candidates=scored),
            KeywordReport(ran=True, scope="pool", matched=len(scored), adds_recall=False),
        )

    async def _recall_lists(
        self, query: str, scope: RetrievalScope
    ) -> tuple[list[RankedList], list[GraphNode], list[GraphEdge]]:
        """Recall origin-tagged ranked lists from the backend, scoped to ``scope``.

        Backends implementing :class:`~aegis.retrieval.protocols.MultiListBackend` hand
        back split per-signal lists (e.g. vector + graph); a plain backend returns one
        pre-blended list, tagged with its declared origins (default: vector+graph mix).
        Either way the scope goes to the backend, which is the only layer that can apply
        the tenant predicate where the rows actually are.
        """
        if isinstance(self.backend, MultiListBackend):
            recall = await self.backend.recall_ranked(
                query, top_k=self.config.recall_top_k, scope=scope
            )
            return list(recall.lists), recall.nodes, recall.edges

        recall = await self.backend.recall(
            query, top_k=self.config.recall_top_k, scope=scope
        )
        origins = getattr(self.backend, "recall_origins", _DEFAULT_RECALL_ORIGINS)
        dense = RankedList(origins=tuple(origins), candidates=recall.candidates)
        return [dense], recall.nodes, recall.edges

    def _assemble(
        self,
        top: list[Candidate],
        *,
        num_candidates: int,
        recall_nodes: list[GraphNode],
        recall_edges: list[GraphEdge],
        origins: list[RetrievalOrigin],
        arms: list[ArmReport],
        rerank: RerankReport,
        keyword: KeywordReport,
    ) -> RetrievalResult:
        """Build a `RetrievalResult` from reranked candidates and the graph slice.

        Args:
            top: The rerank survivors (the K sources backing the answer).
            num_candidates: The fused wide-recall pool size *before* rerank (the honest
                N in the "N recalled → K survivors" funnel), carried through so a caller
                sees the true recall count, not ``len(top)``.
            recall_nodes: Graph nodes touched during recall.
            recall_edges: Graph edges touched during recall.
            origins: Distinct retrieval origins that contributed to the fused pool.
            arms: Per-recall-arm measured candidate counts.
            rerank: Whether rerank ran, whether it graded, and the top scores.
            keyword: What the BM25/keyword signal was (corpus arm vs pool re-ranking).
        """
        spotlight_on = self.config.spotlight_enabled
        answer_context = (
            build_spotlighted_context([c.text for c in top])
            if spotlight_on
            else build_plain_context([c.text for c in top])
        )
        sources = [
            Source(id=c.id, text=c.text, score=c.score, metadata=c.metadata) for c in top
        ]
        return RetrievalResult(
            answer_context=answer_context,
            sources=sources,
            num_candidates=num_candidates,
            graph_delta=GraphDelta(nodes=recall_nodes, edges=recall_edges),
            cache_hit=False,
            provenance=Provenance(origins=origins, fusion=FusionMethod.RRF),
            observability=RetrievalObservability(
                arms=arms,
                fusion=FusionMethod.RRF,
                fused_candidates=num_candidates,
                rerank=rerank,
                keyword=keyword,
                spotlight_applied=spotlight_on and bool(top),
            ),
        )

    async def ingest(
        self, docs: Sequence[object], *, scope: RetrievalScope
    ) -> IngestReport:
        """Chunk, validate, dedup, and write documents into ``scope``'s knowledge store.

        The ingestion path:

        1. **Structure-aware recursive chunking** (:func:`chunker.chunk_structured`):
           heading-scoped, paragraph/sentence-packed chunks with word overlap and a
           section-path prefix (contextual retrieval), not naive fixed windows.
        2. **Robust dedup** (:func:`chunker.dedup_pieces`): exact *and* near-duplicate
           removal within the batch, then an **idempotency ledger** so re-ingesting the
           same corpus writes nothing new (incremental by construction).
        3. **Content validation before any write** (poisoning defense): suspected
           injections/junk are rejected and reported, never stored — the same gate
           protects the vector store *and* the graph.
        4. **Provenance metadata** on every chunk (doc id, ordinal, section, word span,
           content hash, source) so citations and the graph carry lineage.
        5. **Tenant ownership** stamped on every chunk from ``scope``, which is what the
           recall-side tenant predicate later matches on. A ``None`` tenant writes a
           *shared*-corpus chunk (readable by every tenant, e.g. a bundled adapter
           corpus), not a chunk that belongs to whoever asks next.

        Both the idempotency ledger and the chunk id are keyed by tenant as well as by
        content. Without that, two tenants ingesting the same document would collide:
        the second would be counted as an already-ingested duplicate and never written
        for its own tenant, and its chunk id would overwrite the first tenant's row in
        the vector store. An unscoped ingest keeps its historical ids exactly, so the
        shared corpus is byte-identical to before.

        Args:
            docs: Documents to ingest. Each item may be a raw string or a mapping with a
                `text`/`content` field and optional `id`.
            scope: The tenant that will own these documents. **Required, with no
                default.**

        Returns:
            An honest `IngestReport`: documents, chunks written, in-batch duplicates,
            already-ingested duplicates, rejected/poisoned chunks, entities, relations.
        """
        report = IngestReport(documents=0)
        accepted: list[Chunk] = []
        tenant_value = scope.tenant_value()
        id_prefix = "" if tenant_value is None else f"{tenant_value}:"

        for doc_index, doc in enumerate(docs):
            report.documents += 1
            doc_id, text = _coerce_doc(doc, doc_index)
            pieces = chunker.chunk_structured(
                text, chunk_size=self.config.chunk_size, overlap=self.config.chunk_overlap
            )
            deduped = chunker.dedup_pieces(pieces)
            report.chunks_skipped += deduped.exact_duplicates + deduped.near_duplicates
            for piece in deduped.kept:
                content_hash = piece.content_id()
                ledger_key = (scope.tenant_id, content_hash)
                if ledger_key in self._seen_hashes:
                    report.chunks_duplicate += 1
                    continue
                verdict = validate_content(piece.text)
                if not verdict.ok:
                    report.chunks_rejected += 1
                    report.rejections.append(f"{doc_id}#{piece.ordinal}: {verdict.reason}")
                    continue
                self._seen_hashes.add(ledger_key)
                accepted.append(
                    Chunk(
                        id=f"{id_prefix}{doc_id}#{piece.ordinal}-{content_hash[:8]}",
                        doc_id=doc_id,
                        ordinal=piece.ordinal,
                        text=piece.contextualized(),
                        metadata={
                            "section": piece.section,
                            "word_start": piece.word_start,
                            "word_count": piece.word_count,
                            "content_hash": content_hash,
                            "source": doc_id,
                            TENANT_METADATA_KEY: tenant_value,
                        },
                    )
                )

        report.chunks_written = len(accepted)
        if accepted:
            entities, relations = await self.backend.ingest_chunks(accepted)
            report.entities = entities
            report.relations = relations
        return report


def _coerce_doc(doc: object, index: int) -> tuple[str, str]:
    """Normalise a document into `(doc_id, text)`.

    Args:
        doc: A raw string or a mapping with `text`/`content` and optional `id`.
        index: Positional index, used to synthesise an id when none is provided.

    Returns:
        A `(doc_id, text)` tuple; text is empty when the shape is unrecognised.
    """
    if isinstance(doc, str):
        return (f"doc-{index}", doc)
    if isinstance(doc, dict):
        text = str(doc.get("text") or doc.get("content") or "")
        return (str(doc.get("id", f"doc-{index}")), text)
    return (f"doc-{index}", str(getattr(doc, "text", "")))


# ─────────────────────────────────────────────────────────────────────────────
# Hybrid recall helpers (BM25 keyword list + list de-duplication)
# ─────────────────────────────────────────────────────────────────────────────

_WORD_RE = re.compile(r"[a-z0-9]+")
_BM25_K1 = 1.5
_BM25_B = 0.75


def _keyword_tokens(text: str) -> list[str]:
    """Return the lowercase word tokens of ``text`` (BM25 term stream)."""
    return _WORD_RE.findall(text.lower())


def _unique_candidates(lists: Sequence[RankedList]) -> list[Candidate]:
    """Return the de-duplicated candidate pool across ranked lists (first wins)."""
    seen: dict[str, Candidate] = {}
    for ranked in lists:
        for cand in ranked.candidates:
            seen.setdefault(cand.id, cand)
    return list(seen.values())


def bm25_ranked(query: str, corpus: Sequence[Candidate]) -> list[Candidate]:
    """Rank ``corpus`` by Okapi BM25 relevance to ``query`` (dependency-free).

    The shared keyword-scoring core, used for *both* shapes of the keyword signal: a
    backend's corpus-wide keyword recall (where ``corpus`` really is the corpus and the
    IDF weights are real corpus statistics) and the pipeline's fallback pass over an
    already-recalled pool (where they are not — see
    :meth:`Retriever._keyword_signal`). What differs is what a caller may claim from the
    result, not the arithmetic, so the arithmetic lives in one place.

    Only candidates with a positive score are returned, so an empty list honestly means
    "no keyword match" rather than "everything, weakly".

    Args:
        query: The user query.
        corpus: The candidates to score (deduplicated by the caller).

    Returns:
        The matching candidates in descending BM25 order (ties by input order).
    """
    q_terms = _keyword_tokens(query)
    if not corpus or not q_terms:
        return []

    pool = list(corpus)
    docs = [_keyword_tokens(c.text) for c in pool]
    n_docs = len(docs)
    avgdl = (sum(len(d) for d in docs) / n_docs) or 1.0
    df: Counter[str] = Counter()
    for doc in docs:
        df.update(set(doc))

    scored: list[tuple[float, int, Candidate]] = []
    for index, (cand, doc) in enumerate(zip(pool, docs, strict=True)):
        dl = len(doc) or 1
        tf = Counter(doc)
        score = 0.0
        for term in set(q_terms):
            freq = tf.get(term, 0)
            if not freq:
                continue
            idf = math.log(1 + (n_docs - df[term] + 0.5) / (df[term] + 0.5))
            denom = freq + _BM25_K1 * (1 - _BM25_B + _BM25_B * dl / avgdl)
            score += idf * (freq * (_BM25_K1 + 1)) / denom
        if score > 0.0:
            scored.append((score, index, cand))

    scored.sort(key=lambda row: (-row[0], row[1]))
    return [cand for _, _, cand in scored]


# ─────────────────────────────────────────────────────────────────────────────
# Production wiring helper (still requires an injected completer/embedder)
# ─────────────────────────────────────────────────────────────────────────────


def build_local_reranker(config: RetrievalConfig) -> LocalCrossEncoderReranker | None:
    """Build the local cross-encoder a composition root should hand to its `Retriever`.

    One function so the three composition roots cannot drift apart on the kill switch —
    "the lite retriever quietly reranks differently from the full one" is exactly the sort
    of divergence nobody notices until an eval disagrees with production.

    Args:
        config: The retrieval config; ``local_rerank_enabled`` decides, and
            ``local_rerank_model`` names the checkpoint.

    Returns:
        A :class:`~aegis.retrieval.local_reranker.LocalCrossEncoderReranker` (unloaded — the
        weights arrive on first use), or ``None`` when the operator turned it off, which
        demotes reranking to the API reranker rather than switching it off.
    """
    if not config.local_rerank_enabled:
        return None
    return LocalCrossEncoderReranker(model_name=config.local_rerank_model)


def build_default_retriever(
    *, complete: CompleteFn, embed: EmbedFn, config: RetrievalConfig | None = None
) -> Retriever:
    """Construct a production `Retriever` (LightRAG backend + Redis semantic cache).

    LLM-agnostic: the caller injects `complete`/`embed` (no gateway import here).

    Args:
        complete: The chat-completion callable used for extraction and reranking.
        embed: The embedding callable used for chunk/query vectors.
        config: Tunables + store connection settings; defaults to `RetrievalConfig()`.

    Returns:
        A `Retriever` wired to a `LightRAGBackend` (Neo4j + Qdrant), a Redis
        `SemanticCache`, and the local ONNX cross-encoder reranker (unless
        ``config.local_rerank_enabled`` is off).
    """
    config = config or RetrievalConfig()
    backend = LightRAGBackend(complete, embed, config=config)
    cache = SemanticCache.from_url(
        config.redis_url,
        ttl_seconds=config.cache_ttl_seconds,
        similarity_threshold=config.semantic_threshold,
    )
    return Retriever(
        backend=backend,
        cache=cache,
        complete=complete,
        embed=embed,
        config=config,
        local_reranker=build_local_reranker(config),
    )
