"""Embedded-Chroma "lite" retrieval backend + cache for a databaseless run mode.

Swaps the LightRAG (Neo4j + Chroma) backend and the Redis semantic cache for the
self-contained equivalents below, so the full agentic slice — recall, rerank,
spotlight, cache-hit accounting and the graph delta — runs with **no external
databases**. Only a completer/embedder is still required (lite mode = real LLM, zero
infra to stand up).

The backend is a genuine **hybrid-lite** retriever, not keyword-only, and — critically —
its **vector arm is a real vector engine, never a RAM dict**: chunk embeddings are
upserted into an embedded :class:`~aegis.retrieval.vector_store.ChromaVectorStore`
(the official chromadb embedded mode: on-disk or in-process, and **no server binary**),
and the vector list is a real Chroma ``search``, not a hand-rolled brute-force cosine
over a Python ``dict``.
Alongside it, a co-occurrence **graph** yields a graph-expansion list; both are handed to
the *same* Reciprocal Rank Fusion the production path uses (plus the pipeline's BM25
list). Lite and full therefore share one fusion+rerank core and both put vectors in
Chroma; only the mode (embedded vs server) and graph store differ.

Embeddings come from an **injected** embedder (:class:`~aegis.retrieval.protocols.EmbedFn`),
exactly as the production path — only the *store* is local. When no embedder is injected
(offline evals/tests/seed corpora), the backend falls back to :func:`_local_embed`, a
deterministic offline embedder — but the vectors it produces are still stored in and
searched by the real Chroma engine, so there is no dict-scan path left anywhere.

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
from aegis.retrieval.pipeline import RetrievalConfig, Retriever, bm25_ranked
from aegis.retrieval.protocols import CompleteFn, EmbedFn
from aegis.retrieval.types import (
    TENANT_METADATA_KEY,
    GraphEdge,
    GraphNode,
    RetrievalOrigin,
    RetrievalScope,
)
from aegis.retrieval.vector_store import ChromaVectorStore

_WORD = re.compile(r"[a-z0-9]+")

#: Dimensionality of the local hashing embedding (the offline default embedder).
_EMBED_DIM = 256
#: Default embedded-Chroma collection name for the lite backend's chunk vectors.
_LITE_COLLECTION = "aegis_lite_chunks"
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
    to a dot product. No NumPy, no Faiss, no GPU, no network.

    This is **an embedder, not a store**: the backend injects it (via
    :func:`_default_offline_embed`) only when no gateway embedder is supplied, and the
    vectors it returns are upserted into and searched by the real Chroma engine — there
    is no brute-force dict scan anywhere. Callers can also inject it directly as the
    ``embed`` for offline eval/test retrievers.

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


async def _default_offline_embed(texts: list[str]) -> list[list[float]]:
    """The backend's fallback embedder: deterministic, offline :func:`_local_embed`.

    Used only when no gateway :class:`~aegis.retrieval.protocols.EmbedFn` is injected
    (offline evals/tests/seed corpora). Its vectors still go through the Chroma store.
    """
    return [_local_embed(text) for text in texts]


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
    """Hybrid-lite recall over an embedded-Chroma KnowledgeBackend (no external DBs).

    Implements both :class:`~aegis.retrieval.protocols.KnowledgeBackend` and the
    optional :class:`~aegis.retrieval.protocols.MultiListBackend`: :meth:`recall_ranked`
    hands the pipeline a **vector** list (a real
    :class:`~aegis.retrieval.vector_store.ChromaVectorStore` ``search`` over chunk
    embeddings — embedded/local Chroma, *not* a RAM dict) and a **graph** list
    (co-occurrence expansion). It also implements
    :class:`~aegis.retrieval.protocols.KeywordBackend` (:meth:`keyword_recall`), so the
    BM25 arm RRF fuses in is a real corpus-wide keyword search rather than a re-scoring
    of what the other two arms already found. :meth:`recall` returns the vector+graph
    pair fused (for direct callers).

    The vector store is injectable; it defaults to an embedded ``:memory:`` Chroma so a
    directly-constructed backend (tests, offline evals) still uses the genuine engine.
    Optional ``tenant``/``subject`` scope every upsert payload and every search filter,
    so one process can hold isolated corpora. That construction-time ``tenant`` is a
    *corpus namespace* and is distinct from the per-request governance tenant carried by
    the :class:`~aegis.retrieval.types.RetrievalScope` every recall method now takes —
    the latter is matched against each row's own owner (see :meth:`_payload`), so a
    single shared backend still isolates tenants without one instance per tenant.

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

    def __init__(
        self,
        chunks: list[Chunk],
        *,
        embed: EmbedFn | None = None,
        extractor: Extractor | None = None,
        vector_store: ChromaVectorStore | None = None,
        collection: str = _LITE_COLLECTION,
        tenant: str | None = None,
        subject: str | None = None,
    ) -> None:
        """Hold the corpus + graph extractor; prepare the Chroma store + co-occurrence graph.

        Chunk embeddings are **not** computed here (embedding is async): they are lazily
        embedded and upserted into Chroma on the first ingest/recall via
        :meth:`_ensure_indexed`, mirroring how the knowledge graph is lazily extracted.

        Args:
            chunks: The initial corpus (may be empty).
            embed: Injected embedder for chunk/query vectors; defaults to the offline
                deterministic :func:`_default_offline_embed` when none is supplied.
            extractor: Entity/relation extractor; defaults to the best available
                deterministic one (spaCy, else a logged no-op) when none is injected.
            vector_store: The Chroma-backed vector store; defaults to an embedded
                ``:memory:`` store (the real engine, offline).
            collection: Chroma collection name for this backend's chunk vectors.
            tenant: Optional tenant id; scopes every payload and search filter.
            subject: Optional subject id; scopes every payload and search filter.
        """
        self._chunks = chunks
        self._embed = embed or _default_offline_embed
        self._extractor = extractor or build_extractor()
        self._vector_store = vector_store or ChromaVectorStore.local()
        self._collection = collection
        self._tenant = tenant
        self._subject = subject
        self._indexed_ids: set[str] = set()  # chunk ids already upserted to Chroma
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
        """(Re)compute token sets and the co-occurrence graph (BM25/graph arms).

        Vector indexing is deliberately *not* done here — it is async (embedding) and
        lives in :meth:`_ensure_indexed`.
        """
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

    async def _ensure_indexed(self) -> None:
        """Embed any not-yet-indexed chunks and upsert them into the Chroma collection.

        Idempotent and lazy (mirrors :meth:`_ensure_extracted`): each chunk is embedded
        and upserted at most once (guarded by ``_indexed_ids``), so calling this from
        both :meth:`ingest_chunks` and the recall path is safe and cheap. The collection
        is created on first use with the injected embedder's own dimensionality.
        """
        pending = [c for c in self._chunks if c.id not in self._indexed_ids]
        if not pending:
            return
        vectors = await self._embed([c.text for c in pending])
        if not vectors or not vectors[0]:
            return
        self._vector_store.ensure_collection(self._collection, dim=len(vectors[0]))
        self._vector_store.upsert(
            self._collection,
            ids=[c.id for c in pending],
            vectors=vectors,
            payloads=[self._payload(c) for c in pending],
        )
        self._indexed_ids.update(c.id for c in pending)

    def _payload(self, chunk: Chunk) -> dict[str, object]:
        """Build the Chroma metadata for ``chunk`` (doc + text + both scopes).

        Two independent scopes are recorded, and they are not the same thing:

        * ``tenant``/``subject`` — the *corpus namespace this backend instance was built
          for*, fixed at construction. It exists so one process can hold several isolated
          corpora in one embedded store.
        * :data:`~aegis.retrieval.types.TENANT_METADATA_KEY` — the *governance tenant
          that owns this row*, carried per-chunk from the ingest
          :class:`~aegis.retrieval.types.RetrievalScope`. This is the one a per-request
          scope matches against, and it is written for every chunk (``None`` for the
          shared corpus) so the key is always present to filter on.
        """
        payload: dict[str, object] = {
            "doc": chunk.doc_id,
            "text": chunk.text,
            # Always written, even when ``None``: the store encodes ``None`` as its
            # explicit null sentinel, and a key that is sometimes absent cannot be
            # filtered on at all (Chroma drops missing keys from a ``where`` match).
            TENANT_METADATA_KEY: chunk.metadata.get(TENANT_METADATA_KEY),
        }
        if self._tenant is not None:
            payload["tenant"] = self._tenant
        if self._subject is not None:
            payload["subject"] = self._subject
        return payload

    def _scope_filter(self, scope: RetrievalScope) -> dict[str, object] | None:
        """Return the Chroma ``where`` filter for a search under ``scope``.

        Combines this instance's construction-time corpus namespace with the per-request
        governance tenant. The tenant clause is a match-any over
        :meth:`~aegis.retrieval.types.RetrievalScope.visible_tenant_values` — this
        tenant's rows plus the shared, tenant-less corpus — and never a wildcard: an
        unscoped request narrows to the shared rows only.

        Args:
            scope: The request's retrieval scope.

        Returns:
            A ``{field: value | [values]}`` filter for
            :meth:`~aegis.retrieval.vector_store.ChromaVectorStore.search`.
        """
        flt: dict[str, object] = {TENANT_METADATA_KEY: scope.visible_tenant_values()}
        if self._tenant is not None:
            flt["tenant"] = self._tenant
        if self._subject is not None:
            flt["subject"] = self._subject
        return flt

    def _visible(self, chunk: Chunk, scope: RetrievalScope) -> bool:
        """Return whether ``scope`` may read ``chunk``.

        The in-Python twin of :meth:`_scope_filter`, for the arms that rank over
        ``self._chunks`` directly (BM25 and graph expansion) instead of through Chroma.
        It applies the *same* rule to the *same* row, because a keyword arm that ignores
        the tenant re-opens the leak the vector arm just closed.
        """
        return chunk.metadata.get(TENANT_METADATA_KEY) in scope.visible_tenant_values()

    @classmethod
    def from_corpus(
        cls,
        *,
        path: str | Path | None = None,
        docs: Sequence[str] | Sequence[tuple[str, str]] | None = None,
        chunk_size: int = 400,
        overlap: int = 60,
        extractor: Extractor | None = None,
        embed: EmbedFn | None = None,
        vector_store: ChromaVectorStore | None = None,
        collection: str = _LITE_COLLECTION,
        tenant: str | None = None,
        subject: str | None = None,
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
            embed: Optional injected embedder (defaults to the offline embedder).
            vector_store: Optional Chroma store (defaults to embedded ``:memory:``).
            collection: Chroma collection name for this backend's chunk vectors.
            tenant: Optional tenant id scoping every payload + search filter.
            subject: Optional subject id scoping every payload + search filter.

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
        return cls(
            chunks,
            embed=embed,
            extractor=extractor,
            vector_store=vector_store,
            collection=collection,
            tenant=tenant,
            subject=subject,
        )

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
        await self._ensure_indexed()  # embed + upsert the fresh chunks into Chroma
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

    async def _vector_list(
        self, query: str, top_k: int, scope: RetrievalScope
    ) -> RankedList:
        """Rank chunks by a real Chroma vector ``search`` against the embedded query.

        The query is embedded with the *same* injected embedder as the chunks, then the
        embedded-Chroma store returns the nearest chunks. The tenant predicate is part of
        the store's ``where`` filter, so it constrains the ANN query itself rather than
        being applied to whatever the query happened to return. No brute-force dict scan
        is involved.
        """
        vectors = await self._embed([query])
        q_vec = vectors[0] if vectors else []
        if not any(q_vec):  # empty/degenerate query embedding → honestly no vector hits
            return RankedList(origins=(RetrievalOrigin.VECTOR,), candidates=[])
        hits = self._vector_store.search(
            self._collection, q_vec, top_k, filter=self._scope_filter(scope)
        )
        candidates = [
            Candidate(
                id=hit.id,
                text=str(hit.payload.get("text", "")),
                score=hit.score,
                metadata={"doc": hit.payload.get("doc")},
            )
            for hit in hits
        ]
        return RankedList(origins=(RetrievalOrigin.VECTOR,), candidates=candidates)

    def _graph_list(self, query: str, top_k: int, scope: RetrievalScope) -> RankedList:
        """Rank chunks by graph proximity to keyword-seed chunks (graph expansion).

        Seeds are chunks that directly share query terms; each seed then propagates its
        weight one hop along the co-occurrence graph, so strongly-connected neighbours
        surface even when they do not themselves match the query — a real graph slice.

        The tenant predicate is applied to **both** ends of every hop: a row the scope
        cannot read is neither a seed nor a reachable neighbour. Filtering only the final
        ranking would let another tenant's chunk still influence which of *this* tenant's
        chunks surface, which is a quieter version of the same leak.
        """
        q = _tokens(query)
        visible = self._visible_indices(scope)
        scores: dict[int, float] = {}
        for index in visible:
            seed = len(q & self._tokens[index])
            if seed <= 0:
                continue
            scores[index] = scores.get(index, 0.0) + float(seed)
            for neighbour, weight in self._adjacency[index]:
                if neighbour not in visible:
                    continue
                scores[neighbour] = scores.get(neighbour, 0.0) + seed * weight
        ranked = sorted(scores.items(), key=lambda row: (-row[1], row[0]))
        candidates = [self._candidate(index, score) for index, score in ranked[:top_k]]
        return RankedList(origins=(RetrievalOrigin.GRAPH,), candidates=candidates)

    def _visible_indices(self, scope: RetrievalScope) -> set[int]:
        """Return the positions in ``self._chunks`` that ``scope`` is allowed to read."""
        return {
            index
            for index, chunk in enumerate(self._chunks)
            if self._visible(chunk, scope)
        }

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
        self, query: str, *, top_k: int, scope: RetrievalScope
    ) -> RankedRecall:
        """Return split vector + graph ranked lists plus the entity graph slice.

        Args:
            query: The user query.
            top_k: Candidate breadth per list.
            scope: The request's retrieval scope; both arms restrict themselves to the
                rows it may read before they rank anything.

        Returns:
            A :class:`RankedRecall` over this scope's visible corpus only.
        """
        await self._ensure_indexed()  # lazily embed + upsert chunk vectors into Chroma
        await self._ensure_extracted()  # lazily populate the KG (e.g. after from_corpus)
        vector_list = await self._vector_list(query, top_k, scope)
        graph_list = self._graph_list(query, top_k, scope)
        seed = list(vector_list.candidates) or list(graph_list.candidates)
        nodes, edges = self._graph_slice(seed)
        return RankedRecall(lists=[vector_list, graph_list], nodes=nodes, edges=edges)

    async def keyword_recall(
        self, query: str, *, top_k: int, scope: RetrievalScope
    ) -> list[Candidate]:
        """Return the best corpus-wide BM25 matches within ``scope`` (the keyword arm).

        Implements :class:`~aegis.retrieval.protocols.KeywordBackend`: BM25 is scored
        over **every** chunk this backend holds *that the scope may read*, not over what
        the vector/graph arms happened to return, so the IDF weights are real corpus
        statistics and a keyword-only chunk — one no dense or graph arm surfaced — can
        genuinely enter the fused pool. That is what earns this arm its ``bm25``
        provenance origin.

        The tenant predicate is applied to the corpus *before* scoring, not to the
        results after: excluded rows must not contribute document frequencies either, or
        one tenant's corpus would still be shaping another tenant's IDF weights.

        Args:
            query: The user query.
            top_k: Maximum number of matches to return.
            scope: The request's retrieval scope (tenant rows + the shared corpus).

        Returns:
            Up to ``top_k`` matching candidates, best first (empty when nothing matches).
        """
        corpus = [
            Candidate(id=ch.id, text=ch.text, metadata={"doc": ch.doc_id})
            for ch in self._chunks
            if self._visible(ch, scope)
        ]
        return bm25_ranked(query, corpus)[:top_k]

    async def recall(self, query: str, *, top_k: int, scope: RetrievalScope) -> Recall:
        """Fuse the vector + graph lists via RRF for direct callers (protocol path)."""
        ranked = await self.recall_ranked(query, top_k=top_k, scope=scope)
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
    # Real vectors in an embedded (:memory:) Chroma — no external DB, no server binary,
    # no RAM dict — with embeddings from the injected gateway embedder (same as the full
    # path; only the store is local).
    backend = InMemoryKnowledgeBackend([], embed=embed, extractor=extractor)
    cache = SemanticCache(
        InMemoryRedis(),
        ttl_seconds=config.cache_ttl_seconds,
        similarity_threshold=config.semantic_threshold,
    )
    return Retriever(backend=backend, cache=cache, complete=complete, embed=embed, config=config)
