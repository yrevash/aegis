"""In-memory retrieval backend + cache for a databaseless / "lite" run mode.

Swaps the LightRAG (Neo4j + pgvector) backend and the Redis semantic cache for the
self-contained equivalents below, so the full agentic slice — recall, rerank,
spotlight, cache-hit accounting and the graph delta — runs with **no databases**.
Only a completer/embedder is still required (lite mode = real LLM, zero infra).

The in-memory backend is a genuine **hybrid-lite** retriever, not keyword-only: it
embeds every corpus chunk once into an in-process bag-of-words vector (local,
offline, no GPU/ANN), does brute-force cosine for a **vector** list, walks a
co-occurrence **graph** for a graph-expansion list, and hands both to the *same*
Reciprocal Rank Fusion the production path uses (plus the pipeline's BM25 list). Lite
and full therefore share one fusion+rerank core; only the stores differ.

Unlike the backend this was extracted from, :meth:`InMemoryKnowledgeBackend.from_corpus`
takes an explicit ``path`` or ``docs`` argument — this package has no notion of a host
application's bundled adapter corpus, so the caller supplies one (or gets an honestly
empty backend).
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from pathlib import Path

from aegis.retrieval import chunker
from aegis.retrieval.cache import SemanticCache
from aegis.retrieval.fusion import RankedList, RankedRecall, reciprocal_rank_fusion
from aegis.retrieval.graph_extract import (
    Entity,
    Extractor,
    Relation,
    build_extractor,
    find_mentions,
)
from aegis.retrieval.models import Candidate, Chunk, Recall
from aegis.retrieval.pipeline import RetrievalConfig, Retriever
from aegis.retrieval.protocols import CompleteFn, EmbedFn
from aegis.retrieval.types import GraphEdge, GraphNode, RetrievalOrigin

_WORD = re.compile(r"[a-z0-9]+")

#: Dimensionality of the local hashing embedding (small; brute-force cosine is cheap).
_EMBED_DIM = 256
#: Minimum shared-token count for a co-occurrence graph edge between two chunks.
_GRAPH_MIN_OVERLAP = 2
#: Max neighbours kept per chunk in the co-occurrence adjacency.
_GRAPH_FANOUT = 6


def _tokens(text: str) -> set[str]:
    """Return the lowercase word tokens of ``text`` (for overlap scoring)."""
    return set(_WORD.findall(text.lower()))


def _local_embed(text: str, *, dim: int = _EMBED_DIM) -> list[float]:
    """Embed ``text`` into an L2-normalised hashing bag-of-words vector.

    A deterministic, offline stand-in for a gateway embedding: each token is hashed
    (SHA-1, stable across processes unlike ``hash()``) into one of ``dim`` buckets and
    its term frequency accumulated, then the vector is L2-normalised so cosine reduces
    to a dot product. Good enough for real in-memory semantic recall over a synthetic
    corpus of hundreds of chunks — no NumPy, no Faiss, no GPU.

    Args:
        text: The text to embed.
        dim: Vector dimensionality (bucket count).

    Returns:
        A length-``dim`` unit vector (all-zero for empty text).
    """
    vec = [0.0] * dim
    for token in _WORD.findall(text.lower()):
        bucket = int(hashlib.sha1(token.encode()).hexdigest(), 16) % dim
        vec[bucket] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm:
        vec = [v / norm for v in vec]
    return vec


class InMemoryRedis:
    """A dict-backed async stand-in for the subset of Redis the cache uses.

    Implements the :class:`~aegis.retrieval.cache.RedisLike` surface so the real
    :class:`~aegis.retrieval.cache.SemanticCache` (exact + semantic tiers, real
    cache-hit accounting) works with no Redis server. TTLs are accepted and
    ignored — the process lifetime is the demo lifetime.
    """

    def __init__(self) -> None:
        """Initialise empty key/value and set stores."""
        self._kv: dict[str, str] = {}
        self._sets: dict[str, set[str]] = {}

    async def get(self, key: str) -> str | None:
        """Return the value at ``key`` or ``None``."""
        return self._kv.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None) -> object:
        """Set ``key`` to ``value`` (TTL accepted for parity, not enforced)."""
        self._kv[key] = value
        return True

    async def sadd(self, key: str, *values: str) -> object:
        """Add ``values`` to the set at ``key``."""
        self._sets.setdefault(key, set()).update(values)
        return len(values)

    async def smembers(self, key: str) -> set[str]:
        """Return a copy of the set at ``key``."""
        return set(self._sets.get(key, set()))


class InMemoryKnowledgeBackend:
    """Hybrid-lite recall over an in-process corpus — a databaseless KnowledgeBackend.

    Implements both :class:`~aegis.retrieval.protocols.KnowledgeBackend` and the
    optional :class:`~aegis.retrieval.protocols.MultiListBackend`: :meth:`recall_ranked`
    hands the pipeline a **vector** list (brute-force cosine over local chunk
    embeddings) and a **graph** list (co-occurrence expansion), which RRF fuses with
    the pipeline's BM25 list. :meth:`recall` returns those two fused (for direct
    callers).

    Alongside recall it maintains a *genuine* knowledge graph: an injected
    :class:`~aegis.retrieval.graph_extract.Extractor` turns chunk text into typed
    entities and labelled relations, entities are **merged by normalised id** (so one
    entity mentioned across many chunks is one node linking them all), and a literal
    surface-form mention index records which chunk mentions which entity. The graph
    slice the live viz animates is therefore the real entity subgraph the retrieved
    chunks touched — not a chain of document nodes.
    """

    #: Advertised origins if a caller uses the single-list ``recall`` fallback path.
    recall_origins: tuple[RetrievalOrigin, ...] = (
        RetrievalOrigin.VECTOR,
        RetrievalOrigin.GRAPH,
    )

    def __init__(self, chunks: list[Chunk], *, extractor: Extractor | None = None) -> None:
        """Hold the corpus + graph extractor; precompute vectors + co-occurrence adjacency.

        Args:
            chunks: The initial corpus (may be empty).
            extractor: Entity/relation extractor; defaults to the best available
                deterministic one (spaCy, else a logged no-op) when none is injected.
        """
        self._chunks = chunks
        self._extractor = extractor or build_extractor()
        self._vectors: list[list[float]] = []
        self._tokens: list[set[str]] = []
        self._adjacency: list[list[tuple[int, float]]] = []
        # Genuine in-memory knowledge graph, merged across chunks by entity id.
        self.entities: dict[str, Entity] = {}
        self.relations: set[Relation] = set()
        self.mentions: dict[str, set[str]] = {}  # chunk_id -> {entity_id}
        self.entity_chunks: dict[str, set[str]] = {}  # entity_id -> {chunk_id} (inverse)
        self._extracted_ids: set[str] = set()  # chunk ids already run through extraction
        self._reindex()

    def _reindex(self) -> None:
        """(Re)compute chunk embeddings, token sets, and the co-occurrence graph."""
        self._vectors = [_local_embed(ch.text) for ch in self._chunks]
        self._tokens = [_tokens(ch.text) for ch in self._chunks]
        self._adjacency = self._build_adjacency()

    def _build_adjacency(self) -> list[list[tuple[int, float]]]:
        """Build a chunk↔chunk co-occurrence graph weighted by Jaccard token overlap."""
        n = len(self._chunks)
        adjacency: list[list[tuple[int, float]]] = []
        for i in range(n):
            neighbours: list[tuple[int, float]] = []
            for j in range(n):
                if i == j:
                    continue
                shared = len(self._tokens[i] & self._tokens[j])
                if shared >= _GRAPH_MIN_OVERLAP:
                    union = len(self._tokens[i] | self._tokens[j]) or 1
                    neighbours.append((j, shared / union))
            neighbours.sort(key=lambda pair: pair[1], reverse=True)
            adjacency.append(neighbours[:_GRAPH_FANOUT])
        return adjacency

    @classmethod
    def from_corpus(
        cls,
        *,
        path: str | Path | None = None,
        docs: Sequence[str] | Sequence[tuple[str, str]] | None = None,
        chunk_size: int = 400,
        overlap: int = 60,
        extractor: Extractor | None = None,
    ) -> InMemoryKnowledgeBackend:
        """Build a backend by chunking a caller-supplied corpus.

        Exactly one of ``path`` or ``docs`` is typically given (``docs`` wins if both
        are). With neither, this returns a valid but honestly **empty** backend —
        retrieval then returns no candidates rather than silently reaching for some
        implicit corpus, so a caller who forgot to supply one sees an empty result, not
        a magically-populated one.

        Args:
            path: Directory containing ``*.md`` files to chunk (each file one document).
            docs: An explicit corpus: an iterable of raw strings, or of
                ``(doc_id, text)`` pairs.
            chunk_size: Target chunk size in words, forwarded to the chunker.
            overlap: Word overlap between consecutive chunks, forwarded to the chunker.
            extractor: Optional entity/relation extractor; defaults to the best
                available deterministic one (see
                :func:`~aegis.retrieval.graph_extract.build_extractor`).

        Returns:
            An :class:`InMemoryKnowledgeBackend` over the resulting chunks.
        """
        chunks: list[Chunk] = []
        if docs is not None:
            for doc_index, doc in enumerate(docs):
                doc_id, text = (
                    doc if isinstance(doc, tuple) else (f"doc-{doc_index}", doc)
                )
                chunks.extend(
                    _chunk_document(doc_id, text, chunk_size=chunk_size, overlap=overlap)
                )
        elif path is not None:
            for entry in sorted(Path(path).iterdir()):
                if not entry.name.endswith(".md"):
                    continue
                text = entry.read_text(encoding="utf-8")
                chunks.extend(
                    _chunk_document(
                        entry.name, text, chunk_size=chunk_size, overlap=overlap
                    )
                )
        return cls(chunks, extractor=extractor)

    async def ingest_chunks(
        self, chunks: Sequence[Chunk]
    ) -> tuple[int | None, int | None]:
        """Append genuinely-new chunks (by id), reindex, extract the graph; return counts.

        De-duplicating by chunk id makes a direct re-ingest idempotent even for callers
        that bypass the pipeline's content-hash ledger, so the lite store never grows on
        a repeated corpus load.

        After the new chunks are added, the injected extractor runs over **them only**
        and its entities are merged into the knowledge graph by normalised id, so the
        returned counts are the **real** number of new entities and new relations this
        ingest contributed — not ``None`` and never a fabricated number. Extraction is
        cached (on disk for the LLM extractor), so a re-ingest replays for free/offline.
        """
        known = {c.id for c in self._chunks}
        fresh = [c for c in chunks if c.id not in known]
        if not fresh:
            return (0, 0)  # nothing new added → honest zero delta, not a fake count
        self._chunks.extend(fresh)
        self._reindex()
        return await self._ensure_extracted()

    async def _ensure_extracted(self) -> tuple[int, int]:
        """Run the extractor over any not-yet-extracted chunks; merge into the graph.

        Idempotent and lazy: each chunk is extracted at most once (guarded by
        ``_extracted_ids``), so calling this from both :meth:`ingest_chunks` and the
        recall path is safe and cheap. Entities merge by id — the same entity seen in
        many chunks stays one node — and only relations whose *both* endpoints are known
        entities are kept (no dangling or fabricated edges). Returns the ``(entities,
        relations)`` this call newly added.
        """
        pending = [c for c in self._chunks if c.id not in self._extracted_ids]
        if not pending:
            return (0, 0)

        new_entity_ids: set[str] = set()
        new_relations = 0
        for chunk in pending:
            entities, relations = await self._extractor.extract(chunk.text)
            for ent in entities:
                if ent.id not in self.entities:
                    self.entities[ent.id] = ent
                    new_entity_ids.add(ent.id)
            for rel in relations:
                if (
                    rel.src_id in self.entities
                    and rel.tgt_id in self.entities
                    and rel not in self.relations
                ):
                    self.relations.add(rel)
                    new_relations += 1
            self._extracted_ids.add(chunk.id)

        self._rebuild_mentions()
        return (len(new_entity_ids), new_relations)

    def _rebuild_mentions(self) -> None:
        """Recompute the literal entity↔chunk mention index over the whole corpus.

        A full recompute keeps the index correct when a newly-extracted entity turns out
        to be mentioned in *older* chunks too — that cross-chunk linking is exactly what
        makes the graph connected. Every link is a verifiable literal surface-form match
        (see :func:`~aegis.retrieval.graph_extract.find_mentions`).
        """
        entities = list(self.entities.values())
        self.mentions = {}
        self.entity_chunks = {}
        for chunk in self._chunks:
            hits = find_mentions(chunk.text, entities)
            if hits:
                self.mentions[chunk.id] = hits
                for entity_id in hits:
                    self.entity_chunks.setdefault(entity_id, set()).add(chunk.id)

    def _vector_list(self, query: str, top_k: int) -> RankedList:
        """Rank chunks by cosine of their local embedding against the query (vector)."""
        q_vec = _local_embed(query)
        scored: list[tuple[float, int]] = []
        for index, vec in enumerate(self._vectors):
            score = sum(a * b for a, b in zip(q_vec, vec, strict=True))
            if score > 0.0:
                scored.append((score, index))
        scored.sort(key=lambda row: (-row[0], row[1]))
        candidates = [self._candidate(index, score) for score, index in scored[:top_k]]
        return RankedList(origins=(RetrievalOrigin.VECTOR,), candidates=candidates)

    def _graph_list(self, query: str, top_k: int) -> RankedList:
        """Rank chunks by graph proximity to keyword-seed chunks (graph expansion).

        Seeds are chunks that directly share query terms; each seed then propagates its
        weight one hop along the co-occurrence graph, so strongly-connected neighbours
        surface even when they do not themselves match the query — a real graph slice.
        """
        q = _tokens(query)
        scores: dict[int, float] = {}
        for index, toks in enumerate(self._tokens):
            seed = len(q & toks)
            if seed <= 0:
                continue
            scores[index] = scores.get(index, 0.0) + float(seed)
            for neighbour, weight in self._adjacency[index]:
                scores[neighbour] = scores.get(neighbour, 0.0) + seed * weight
        ranked = sorted(scores.items(), key=lambda row: (-row[1], row[0]))
        candidates = [self._candidate(index, score) for index, score in ranked[:top_k]]
        return RankedList(origins=(RetrievalOrigin.GRAPH,), candidates=candidates)

    def _candidate(self, index: int, score: float) -> Candidate:
        """Wrap chunk ``index`` as a scored :class:`Candidate` (carrying its doc id)."""
        ch = self._chunks[index]
        return Candidate(id=ch.id, text=ch.text, score=score, metadata={"doc": ch.doc_id})

    def _graph_slice(
        self, candidates: Sequence[Candidate]
    ) -> tuple[list[GraphNode], list[GraphEdge]]:
        """Emit the real entity subgraph the retrieved chunks touched.

        Nodes are exactly the entities *mentioned* by the seed candidates' chunks (via
        the literal mention index), carrying their true entity ``kind``. Edges are every
        extracted relation whose **both** endpoints are among those touched entities — so
        an edge only appears when a real relation between two shown nodes was extracted
        (no relation ⇒ no edge; honesty by construction). There is no synthetic
        document-to-document chain.
        """
        touched: set[str] = set()
        for candidate in candidates:
            touched |= self.mentions.get(candidate.id, set())

        nodes = [
            GraphNode(id=e.id, label=e.label, kind=e.kind)
            for eid in touched
            if (e := self.entities.get(eid)) is not None
        ]
        edges = [
            GraphEdge(source=r.src_id, target=r.tgt_id, relation=r.phrase)
            for r in self.relations
            if r.src_id in touched and r.tgt_id in touched
        ]
        return nodes, edges

    async def recall_ranked(
        self, query: str, *, top_k: int, persona: str | None = None
    ) -> RankedRecall:
        """Return split vector + graph ranked lists plus the entity graph slice."""
        await self._ensure_extracted()  # lazily populate the KG (e.g. after from_corpus)
        vector_list = self._vector_list(query, top_k)
        graph_list = self._graph_list(query, top_k)
        seed = list(vector_list.candidates) or list(graph_list.candidates)
        nodes, edges = self._graph_slice(seed)
        return RankedRecall(lists=[vector_list, graph_list], nodes=nodes, edges=edges)

    async def recall(self, query: str, *, top_k: int, persona: str | None = None) -> Recall:
        """Fuse the vector + graph lists via RRF for direct callers (protocol path)."""
        ranked = await self.recall_ranked(query, top_k=top_k, persona=persona)
        fused = reciprocal_rank_fusion(ranked.lists)[:top_k]
        return Recall(candidates=fused, nodes=ranked.nodes, edges=ranked.edges)


def _chunk_document(
    doc_id: str, text: str, *, chunk_size: int, overlap: int
) -> list[Chunk]:
    """Split one document's ``text`` into :class:`Chunk` records via the fixed chunker."""
    pieces = chunker.chunk_text(text, chunk_size=chunk_size, overlap=overlap)
    return [
        Chunk(id=f"{doc_id}#{ordinal}", doc_id=doc_id, ordinal=ordinal, text=piece)
        for ordinal, piece in enumerate(pieces)
    ]


def build_lite_retriever(
    *,
    complete: CompleteFn,
    embed: EmbedFn,
    config: RetrievalConfig | None = None,
    working_dir: str = "rag_storage",
) -> Retriever:
    """Build a databaseless :class:`Retriever`: corpus recall + in-memory cache.

    LLM-agnostic: the caller injects `complete`/`embed`. The backend starts with an
    empty corpus — call :meth:`Retriever.ingest` or replace ``retriever.backend``
    with one built from :meth:`InMemoryKnowledgeBackend.from_corpus` to seed it.

    The knowledge-graph extractor is the LLM-cached one (a completer is always available
    here), so ingested chunks yield a genuine typed-entity graph; its per-chunk results
    are cached under ``working_dir`` so re-ingest and restart replay offline. spaCy is the
    fallback only when no completer exists (not this path).

    Args:
        complete: The chat-completion callable used for reranking and graph extraction.
        embed: The embedding callable used for chunk/query vectors.
        config: Tunables; defaults to `RetrievalConfig()`.
        working_dir: Directory for the graph-extraction disk cache.

    Returns:
        A `Retriever` over an in-memory backend and an in-memory semantic cache.
    """
    config = config or RetrievalConfig()
    extractor = build_extractor(complete=complete, working_dir=working_dir, prefer="llm")
    backend = InMemoryKnowledgeBackend([], extractor=extractor)
    cache = SemanticCache(
        InMemoryRedis(),
        ttl_seconds=config.cache_ttl_seconds,
        similarity_threshold=config.semantic_threshold,
    )
    return Retriever(backend=backend, cache=cache, complete=complete, embed=embed, config=config)
