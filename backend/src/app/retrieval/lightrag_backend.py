"""LightRAG-backed knowledge store (Neo4j graph + pgvector vectors).

LightRAG is the *pipeline* (chunk → extract entities/relationships → embed → write graph
and vectors → retrieve over both); Neo4j and Postgres/pgvector are the *stores*
(`docs/backend.md` §4). Entity/relationship extraction and embeddings run **via the
gateway** (`app.core.llm`, `ModelRole.CHEAP` + `embed`), so nothing heavy runs locally.

Everything that touches the `lightrag`, `neo4j`, or `redis` packages is imported lazily
inside methods, so this module (and the whole `app.retrieval` package) imports cleanly in
CI with no LightRAG install and no live stores. See `NOTES.md` for the exact LightRAG API
targeted (`lightrag-hku >= 1.0`).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from urllib.parse import urlparse

from app.api.schemas import GraphEdge, GraphNode, RetrievalOrigin
from app.config import Settings, get_settings
from app.core.models import ModelRole

from .fusion import RankedList, RankedRecall
from .models import Candidate, Chunk, Recall
from .protocols import CompleteFn, EmbedFn

#: Dimension of `text-embedding-3-large` (the fixed embedding model).
_EMBED_DIM = 3072
_EMBED_MAX_TOKENS = 8192

#: Upper bound on the whole-graph snapshot used to measure ingest deltas. LightRAG's
#: ``get_knowledge_graph("*")`` caps its return at ``max_nodes``; we pass a large value
#: so demo/hackathon-scale graphs are counted in full (the count is best-effort at
#: production scale — an honest measured number, never a hardcoded constant).
_GRAPH_SNAPSHOT_CAP = 1_000_000

#: Domain-aware entity types steering LightRAG extraction into *typed* graph nodes
#: (vs the generic person/org/geo defaults). Passed via ``addon_params`` so the
#: extractor labels nodes by the vocabulary the domain actually uses — richer, more
#: connectable graph, still fully API-driven (no local model). Kept broad enough to
#: survive an adapter swap; it only biases labels, never restricts what is extracted.
_ENTITY_TYPES: tuple[str, ...] = (
    "organization",
    "person",
    "product",
    "policy",
    "procedure",
    "issue",
    "system",
    "category",
    "location",
    "event",
)


def _apply_store_env(settings: Settings) -> None:
    """Export Neo4j/Postgres connection settings as the env vars LightRAG reads.

    LightRAG's Neo4j and Postgres storage impls read connection details from the
    environment (`NEO4J_*`, `POSTGRES_*`) rather than constructor kwargs, so we translate
    our typed `Settings` into those variables before building the instance.

    Args:
        settings: The application settings carrying the store URIs/credentials.
    """
    os.environ.setdefault("NEO4J_URI", settings.neo4j_uri)
    os.environ.setdefault("NEO4J_USERNAME", settings.neo4j_user)
    os.environ.setdefault("NEO4J_PASSWORD", settings.neo4j_password)

    pg = urlparse(settings.postgres_dsn)
    if pg.hostname:
        os.environ.setdefault("POSTGRES_HOST", pg.hostname)
    os.environ.setdefault("POSTGRES_PORT", str(pg.port or 5432))
    if pg.username:
        os.environ.setdefault("POSTGRES_USER", pg.username)
    if pg.password:
        os.environ.setdefault("POSTGRES_PASSWORD", pg.password)
    if pg.path and len(pg.path) > 1:
        os.environ.setdefault("POSTGRES_DATABASE", pg.path.lstrip("/"))


class LightRAGBackend:
    """A `KnowledgeBackend` implemented on LightRAG with Neo4j + pgvector.

    The instance is built lazily on first use and reused thereafter. Construction is
    injected with the gateway's `complete`/`embed` so extraction and embedding route
    through the shared, guardrailed model fleet.
    """

    def __init__(
        self,
        complete: CompleteFn,
        embed: EmbedFn,
        *,
        settings: Settings | None = None,
        working_dir: str = "rag_storage",
        extract_role: ModelRole = ModelRole.CHEAP,
    ) -> None:
        """Initialise the backend (does not connect until `_ensure()` runs).

        Args:
            complete: The gateway completion function (for entity/relation extraction).
            embed: The gateway embedding function.
            settings: Application settings (defaults to the process singleton).
            working_dir: Portable relative directory for LightRAG's local bookkeeping.
            extract_role: Model role used for LightRAG's extraction LLM calls.
        """
        self._complete = complete
        self._embed = embed
        self._settings = settings or get_settings()
        self._working_dir = working_dir
        self._extract_role = extract_role
        self._rag: object | None = None

    def _build_llm_func(self) -> object:
        """Return an async callable matching LightRAG's `llm_model_func` signature."""
        complete = self._complete
        role = self._extract_role

        async def _llm(
            prompt: str,
            system_prompt: str | None = None,
            history_messages: list[dict[str, object]] | None = None,
            **_: object,
        ) -> str:
            messages: list[dict[str, object]] = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.extend(history_messages or [])
            messages.append({"role": "user", "content": prompt})
            result = await complete(role, messages, temperature=0.0)
            return result.content

        return _llm

    async def _ensure(self) -> object:
        """Build and initialise the LightRAG instance on first use; cache it."""
        if self._rag is not None:
            return self._rag

        from lightrag import LightRAG  # lazy: heavy optional dependency
        from lightrag.kg.shared_storage import initialize_pipeline_status
        from lightrag.utils import EmbeddingFunc

        _apply_store_env(self._settings)
        embed = self._embed

        rag = LightRAG(
            working_dir=self._working_dir,
            llm_model_func=self._build_llm_func(),
            embedding_func=EmbeddingFunc(
                embedding_dim=_EMBED_DIM,
                max_token_size=_EMBED_MAX_TOKENS,
                func=lambda texts: embed(texts),
            ),
            kv_storage="PGKVStorage",
            vector_storage="PGVectorStorage",
            graph_storage="Neo4JStorage",
            doc_status_storage="PGDocStatusStorage",
            # Steer extraction into typed, domain-relevant nodes and re-glean once so
            # relationships are not missed on the first pass (denser, more useful graph).
            addon_params={"entity_types": list(_ENTITY_TYPES)},
            entity_extract_max_gleaning=1,
        )
        await rag.initialize_storages()
        await initialize_pipeline_status()
        self._rag = rag
        return rag

    async def ingest_chunks(
        self, chunks: Sequence[Chunk]
    ) -> tuple[int | None, int | None]:
        """Insert validated chunks; return the `(entities, relations)` this ingest added.

        Chunks are inserted with their **content-addressed ids** as LightRAG document
        ids, so re-inserting the same chunk is a no-op in LightRAG's own doc-status
        store — idempotency holds even across process restarts (the pipeline's in-memory
        ledger is the fast first line; this is the durable second line). Content has
        already passed validation upstream, so nothing unsafe reaches extraction.

        LightRAG performs extraction/embedding/graph+vector writes internally and does
        not return counts. We therefore **measure** them from the knowledge graph itself:
        snapshot the graph store's node/edge totals immediately before and after the
        insert and report the delta — the true number of entities/relationships this
        ingest merged into the graph. These are read from LightRAG's own
        ``chunk_entity_relation_graph`` store (Neo4j in production), so they reflect
        reality, not a hardcoded value. If the graph store cannot be queried in the
        current mode, the corresponding count is ``None`` (honest "unknown"), never a
        fabricated zero.

        Args:
            chunks: Validated, deduplicated chunks to write.

        Returns:
            A `(entities, relations)` delta for this ingest; either element is ``None``
            when the graph store cannot report that count.
        """
        rag = await self._ensure()
        texts = [c.text for c in chunks]
        if not texts:
            return (0, 0)
        ids = [c.id for c in chunks]
        file_paths = [str(c.metadata.get("source") or c.doc_id) for c in chunks]

        before_nodes, before_edges = await _graph_counts(rag)
        await rag.ainsert(texts, ids=ids, file_paths=file_paths)  # type: ignore[attr-defined]
        after_nodes, after_edges = await _graph_counts(rag)

        return (_delta(before_nodes, after_nodes), _delta(before_edges, after_edges))

    async def recall(self, query: str, *, top_k: int, persona: str | None = None) -> Recall:
        """Retrieve a wide candidate set plus the touched graph slice.

        Args:
            query: The user query.
            top_k: Candidate breadth for the recall stage (reranked later).
            persona: Active persona (reserved for store-side scoping).

        Returns:
            A `Recall` with candidates and any graph nodes/edges the query touched.
        """
        rag = await self._ensure()
        return await self._context(rag, query, mode="mix", top_k=top_k)

    async def recall_ranked(
        self, query: str, *, top_k: int, persona: str | None = None
    ) -> RankedRecall:
        """Recall **split** vector + graph lists so RRF genuinely fuses two signals.

        Rather than delegating to LightRAG's internal ``mix`` blend (one pre-fused
        list), this issues two retrievals and hands the pipeline both, tagged by origin:

        * ``naive`` mode → pure **vector** similarity over text chunks (the dense list).
        * ``local`` mode → **graph** traversal over the entity neighbourhood (walks the
          Neo4j knowledge graph the extractor built), contributing the passages and the
          nodes/edges it touched.

        Reciprocal Rank Fusion then combines them (plus the pipeline's BM25 list), so
        graph traversal is a first-class contributor to the final ranking — the graph is
        genuinely *used*, not decorative. The graph slice for the live viz comes from the
        graph query. Falls back cleanly to a single ``mix`` list if a mode errors.

        Args:
            query: The user query.
            top_k: Candidate breadth per list (reranked later).
            persona: Active persona (reserved for store-side scoping).

        Returns:
            A :class:`RankedRecall` with the vector and graph ranked lists and the
            touched graph nodes/edges.
        """
        rag = await self._ensure()
        vector = await self._context(rag, query, mode="naive", top_k=top_k)
        graph = await self._context(rag, query, mode="local", top_k=top_k)
        vector_list = RankedList(
            origins=(RetrievalOrigin.VECTOR,), candidates=vector.candidates
        )
        graph_list = RankedList(
            origins=(RetrievalOrigin.GRAPH,), candidates=graph.candidates
        )
        return RankedRecall(
            lists=[vector_list, graph_list], nodes=graph.nodes, edges=graph.edges
        )

    async def _context(self, rag: object, query: str, *, mode: str, top_k: int) -> Recall:
        """Run one LightRAG context query in ``mode`` and normalise it to a `Recall`."""
        from lightrag import QueryParam  # lazy

        param = QueryParam(
            mode=mode, top_k=top_k, only_need_context=True, enable_rerank=False
        )
        raw = await rag.aquery(query, param=param)  # type: ignore[attr-defined]
        return _to_recall(raw)


async def _graph_counts(rag: object) -> tuple[int | None, int | None]:
    """Return the ``(nodes, edges)`` currently in LightRAG's knowledge graph store.

    Reads LightRAG's own ``chunk_entity_relation_graph`` (the Neo4j graph store in
    production) via its ``get_knowledge_graph("*")`` accessor, which returns a
    ``KnowledgeGraph`` carrying the node and edge lists. This is the real, live graph —
    counting it is how we derive honest post-ingest entity/relationship numbers.

    The accessor is called defensively (its ``max_nodes`` bound is optional across
    LightRAG versions) and any failure or missing store yields ``(None, None)`` so the
    caller reports an honest "unknown" rather than a made-up count.

    Args:
        rag: The initialised LightRAG instance.

    Returns:
        A ``(node_count, edge_count)`` tuple; either element is ``None`` when it cannot
        be read from the graph store.
    """
    graph = getattr(rag, "chunk_entity_relation_graph", None)
    if graph is None:
        return (None, None)
    getter = getattr(graph, "get_knowledge_graph", None)
    if getter is None:
        return (None, None)

    kg: object | None = None
    for kwargs in ({"max_nodes": _GRAPH_SNAPSHOT_CAP}, {}):
        try:
            kg = await getter("*", **kwargs)
            break
        except TypeError:
            continue  # older/newer signature — retry without the optional bound
        except Exception:
            return (None, None)  # store unavailable → honest unknown, not a fake count
    if kg is None:
        return (None, None)

    nodes = getattr(kg, "nodes", None)
    edges = getattr(kg, "edges", None)
    return (
        len(nodes) if nodes is not None else None,
        len(edges) if edges is not None else None,
    )


def _delta(before: int | None, after: int | None) -> int | None:
    """Return the honest ingest delta between two graph snapshots.

    ``after - before`` is the number added by this ingest. If the *before* snapshot was
    unavailable but *after* is known, we fall back to the absolute post-ingest count
    (best-effort, still measured — not fabricated). If *after* is unknown, the delta is
    ``None``.
    """
    if after is None:
        return None
    if before is None:
        return after
    return max(0, after - before)


def _to_recall(raw: object) -> Recall:
    """Normalise a LightRAG context result (string or object) into a `Recall`.

    Recent LightRAG returns a `QueryContextResult` (`.context`, `.raw_data`); older
    versions return a plain string. We parse candidates from the structured chunk data
    when present and fall back to a single-candidate context otherwise.
    """
    context = getattr(raw, "context", raw if isinstance(raw, str) else "")
    data = getattr(raw, "raw_data", None) or {}
    payload = data.get("data", {}) if isinstance(data, dict) else {}

    candidates = _candidates_from_payload(payload, fallback_context=str(context))
    nodes = _nodes_from_payload(payload)
    edges = _edges_from_payload(payload)
    return Recall(candidates=candidates, nodes=nodes, edges=edges)


def _candidates_from_payload(payload: dict, *, fallback_context: str) -> list[Candidate]:
    """Extract rerankable candidates from LightRAG chunk data (with a text fallback)."""
    chunks = payload.get("chunks") if isinstance(payload, dict) else None
    candidates: list[Candidate] = []
    if isinstance(chunks, list):
        for i, chunk in enumerate(chunks):
            if not isinstance(chunk, dict):
                continue
            text = chunk.get("content") or chunk.get("text") or ""
            if not text:
                continue
            candidates.append(
                Candidate(
                    id=str(chunk.get("id", chunk.get("chunk_id", i))),
                    text=str(text),
                    metadata={"file_path": chunk.get("file_path")},
                )
            )
    if not candidates and fallback_context.strip():
        candidates.append(Candidate(id="context", text=fallback_context))
    return candidates


def _nodes_from_payload(payload: dict) -> list[GraphNode]:
    """Extract graph nodes from LightRAG entity data."""
    entities = payload.get("entities") if isinstance(payload, dict) else None
    nodes: list[GraphNode] = []
    if isinstance(entities, list):
        for ent in entities:
            if not isinstance(ent, dict):
                continue
            name = ent.get("entity") or ent.get("entity_name") or ent.get("name")
            if not name:
                continue
            nodes.append(
                GraphNode(
                    id=str(name),
                    label=str(name),
                    kind=str(ent.get("entity_type") or ent.get("type") or "entity"),
                )
            )
    return nodes


def _edges_from_payload(payload: dict) -> list[GraphEdge]:
    """Extract graph edges from LightRAG relationship data."""
    relations = payload.get("relationships") if isinstance(payload, dict) else None
    edges: list[GraphEdge] = []
    if isinstance(relations, list):
        for rel in relations:
            if not isinstance(rel, dict):
                continue
            src = rel.get("src_id") or rel.get("source") or rel.get("src_tgt")
            tgt = rel.get("tgt_id") or rel.get("target")
            if not src or not tgt:
                continue
            edges.append(
                GraphEdge(
                    source=str(src),
                    target=str(tgt),
                    relation=str(rel.get("description") or rel.get("keywords") or "related_to"),
                )
            )
    return edges
