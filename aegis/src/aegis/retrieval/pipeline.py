"""Retrieval orchestration: cache → recall → rerank → spotlight → assemble.

This module wires the stages behind the `Retriever` dataclass's two entry points:

* `Retriever.retrieve(query, *, persona=None) -> RetrievalResult`
* `Retriever.ingest(docs) -> IngestReport`

`Retriever` holds its collaborators (knowledge backend, semantic cache, completer,
embedder) by dependency injection, so the whole flow is unit-testable with fakes and
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
from aegis.retrieval.models import (
    Candidate,
    Chunk,
    GraphDelta,
    IngestReport,
    Provenance,
    RetrievalResult,
    Source,
)
from aegis.retrieval.protocols import CompleteFn, EmbedFn, KnowledgeBackend, MultiListBackend
from aegis.retrieval.reranker import rerank
from aegis.retrieval.spotlight import build_spotlighted_context
from aegis.retrieval.types import FusionMethod, GraphEdge, GraphNode, RetrievalOrigin
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
    chunk_size: int = 400
    chunk_overlap: int = 60
    cache_ttl_seconds: int = 3600
    semantic_threshold: float = 0.985
    rrf_k: int = 60
    #: Dimensionality of a "real" (non-lite) query embedding; gates `query_vec` reuse.
    embed_dim: int = EMBED_DIM
    #: Store connection settings (only read by the LightRAG-backed production path).
    postgres_dsn: str = "postgresql://postgres:postgres@localhost:5432/aegis"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    redis_url: str = "redis://localhost:6379/0"
    #: Qdrant is the **vector** store (Postgres stays for KV/doc-status only). ``qdrant_url``
    #: points the full path at a live Qdrant node; ``qdrant_api_key`` secures it if set.
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    #: Whether the real databases (Neo4j/pgvector/Redis) are expected to be in use.
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
    #: Content hashes already accepted, so re-ingesting a corpus is idempotent and
    #: incremental (only genuinely new chunks reach the backend). Process-scoped; the
    #: LightRAG backend additionally dedupes by content hash in its own store.
    _seen_hashes: set[str] = field(default_factory=set)

    async def retrieve(self, query: str, *, persona: str | None = None) -> RetrievalResult:
        """Answer a retrieval request end-to-end.

        Flow: near-exact cache → **hybrid wide recall** (vector + graph + BM25, fused
        by Reciprocal Rank Fusion) → LLM rerank → spotlight → assemble → write back to
        cache. An exact or near-exact (cosine ≥ ``semantic_threshold``) cache hit
        returns immediately with `cache_hit=True` and honest cache provenance; anything
        below that runs the full fused pipeline (the sub-threshold match is only a
        prefetch hint, never a substituted answer).

        Args:
            query: The user query.
            persona: Optional adapter persona id (scopes cache and, later, store access).

        Returns:
            A `RetrievalResult` with spotlighted context, sources, a graph delta, the
            cache-hit flag, and populated `provenance` (origins + RRF fusion).
        """
        exact = await self.cache.get_exact(query, persona)
        if exact is not None:
            return exact

        query_vec = (await self.embed([query]))[0]
        semantic = await self.cache.get_semantic(query_vec, persona)
        if semantic is not None:
            return semantic

        fused, origins, nodes, edges = await self._recall_and_fuse(query, persona)
        top = await rerank(
            query,
            fused,
            complete=self.complete,
            top_k=self.config.final_top_k,
            role=self.config.rerank_role,
        )

        result = self._assemble(
            top,
            num_candidates=len(fused),
            recall_nodes=nodes,
            recall_edges=edges,
            origins=origins,
        )
        await self.cache.set(query, persona, query_vec, result)
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
        self, query: str, persona: str | None
    ) -> tuple[list[Candidate], list[RetrievalOrigin], list[GraphNode], list[GraphEdge]]:
        """Run hybrid wide recall and fuse the ranked lists with RRF.

        Produces the dense/graph list(s) from the backend, adds a BM25/keyword list
        computed over the recalled pool, and fuses everything with Reciprocal Rank
        Fusion. The fused pool is the honest wide-recall count (N) fed to rerank.

        Args:
            query: The user query.
            persona: Active persona (scopes store access where supported).

        Returns:
            A tuple ``(fused_candidates, origins, nodes, edges)`` where ``origins`` are
            the distinct retrieval origins that produced a surviving candidate.
        """
        lists, nodes, edges = await self._recall_lists(query, persona)
        pool = _unique_candidates(lists)
        keyword = _bm25_ranked_list(query, pool)
        fused = reciprocal_rank_fusion([*lists, keyword], k=self.config.rrf_k)
        return fused, collect_origins(fused), nodes, edges

    async def _recall_lists(
        self, query: str, persona: str | None
    ) -> tuple[list[RankedList], list[GraphNode], list[GraphEdge]]:
        """Recall origin-tagged ranked lists from the backend.

        Backends implementing :class:`~aegis.retrieval.protocols.MultiListBackend` hand
        back split per-signal lists (e.g. vector + graph); a plain backend returns one
        pre-blended list, tagged with its declared origins (default: vector+graph mix).
        """
        if isinstance(self.backend, MultiListBackend):
            recall = await self.backend.recall_ranked(
                query, top_k=self.config.recall_top_k, persona=persona
            )
            return list(recall.lists), recall.nodes, recall.edges

        recall = await self.backend.recall(
            query, top_k=self.config.recall_top_k, persona=persona
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
        """
        answer_context = build_spotlighted_context([c.text for c in top])
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
        )

    async def ingest(self, docs: Sequence[object]) -> IngestReport:
        """Chunk, validate, dedup, and write documents into the knowledge store.

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

        Args:
            docs: Documents to ingest. Each item may be a raw string or a mapping with a
                `text`/`content` field and optional `id`.

        Returns:
            An honest `IngestReport`: documents, chunks written, in-batch duplicates,
            already-ingested duplicates, rejected/poisoned chunks, entities, relations.
        """
        report = IngestReport(documents=0)
        accepted: list[Chunk] = []

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
                if content_hash in self._seen_hashes:
                    report.chunks_duplicate += 1
                    continue
                verdict = validate_content(piece.text)
                if not verdict.ok:
                    report.chunks_rejected += 1
                    report.rejections.append(f"{doc_id}#{piece.ordinal}: {verdict.reason}")
                    continue
                self._seen_hashes.add(content_hash)
                accepted.append(
                    Chunk(
                        id=f"{doc_id}#{piece.ordinal}-{content_hash[:8]}",
                        doc_id=doc_id,
                        ordinal=piece.ordinal,
                        text=piece.contextualized(),
                        metadata={
                            "section": piece.section,
                            "word_start": piece.word_start,
                            "word_count": piece.word_count,
                            "content_hash": content_hash,
                            "source": doc_id,
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
    """Return the de-duplicated candidate pool across ranked lists (first wins).

    The BM25 list is scored over this union so keyword relevance is measured against
    the same pool the dense/graph retrievers surfaced.
    """
    seen: dict[str, Candidate] = {}
    for ranked in lists:
        for cand in ranked.candidates:
            seen.setdefault(cand.id, cand)
    return list(seen.values())


def _bm25_ranked_list(query: str, pool: Sequence[Candidate]) -> RankedList:
    """Rank ``pool`` by Okapi BM25 relevance to ``query`` (a keyword signal).

    A dependency-free BM25 over the recalled pool as its own mini-corpus. Only
    candidates with a positive score are returned (a genuine keyword match), so the
    ``bm25`` origin appears in provenance only when it actually contributed.

    Args:
        query: The user query.
        pool: The de-duplicated candidate pool to score.

    Returns:
        A :class:`RankedList` tagged ``(BM25,)`` in descending score order.
    """
    q_terms = _keyword_tokens(query)
    if not pool or not q_terms:
        return RankedList(origins=(RetrievalOrigin.BM25,), candidates=[])

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
    return RankedList(
        origins=(RetrievalOrigin.BM25,), candidates=[cand for _, _, cand in scored]
    )


# ─────────────────────────────────────────────────────────────────────────────
# Production wiring helper (still requires an injected completer/embedder)
# ─────────────────────────────────────────────────────────────────────────────


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
        A `Retriever` wired to a `LightRAGBackend` (Neo4j + pgvector) and a Redis
        `SemanticCache`.
    """
    config = config or RetrievalConfig()
    backend = LightRAGBackend(complete, embed, config=config)
    cache = SemanticCache.from_url(
        config.redis_url,
        ttl_seconds=config.cache_ttl_seconds,
        similarity_threshold=config.semantic_threshold,
    )
    return Retriever(backend=backend, cache=cache, complete=complete, embed=embed, config=config)
