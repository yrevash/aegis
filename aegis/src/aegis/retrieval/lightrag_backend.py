"""LightRAG-backed knowledge store (Neo4j graph + Qdrant vectors + Postgres KV).

LightRAG is the *pipeline* (chunk → extract entities/relationships → embed → write
graph and vectors → retrieve over both); Neo4j (graph), **Qdrant** (vectors), and
Postgres (KV + doc-status only) are the *stores*. Entity/relationship extraction and
embeddings run via the injected `complete`/`embed` callables, so nothing heavy runs
locally beyond LightRAG's own in-process bookkeeping.

**Why Qdrant, and what it replaced (§9.1).** This backend used to run
``NanoVectorDBStorage``, chosen because the target Windows machine forbids installing a
server. Its own docstring calls it a brute-force cosine scan held in memory, persisted
by rewriting a whole JSON file — so it is linear in corpus size, it assumes a **single
writing process**, and it is one of the two reasons ``uvicorn --workers 2`` could not
work. Qdrant v1.19.0 publishes ``qdrant-x86_64-pc-windows-msvc.zip``: Apache-2.0, a zip
with a binary, no Docker and no installer — the same operational shape as Superset,
which this deployment already runs. The premise that a vector service was
un-installable did not survive checking, so the option that removes the ceiling is
taken.

The options were enumerated against the installed 1.5.6 package rather than its docs:

* ``QdrantVectorDBStorage`` (``lightrag/kg/qdrant_impl.py``, registered in
  ``kg/__init__.py``) — batched upserts, payload-size limits, a ``QDRANT_WORKSPACE``
  namespace override, and it reads **``QDRANT_URL``**, the same variable
  :mod:`aegis.retrieval.vector_store` is pointed at. One engine for both consumers.
  **Chosen.**
* ``NanoVectorDBStorage`` — LightRAG's default, and what this replaces. See above.
* ``ChromaVectorDBStorage`` — declared in ``lightrag.kg.STORAGES`` but its module
  (``lightrag.kg.chroma_impl``) **does not ship** in 1.5.6, so it cannot be selected.
  Chroma could therefore never have served this half, which is why §9.1 deleted it from
  Aegis's half too rather than running two vector systems.
* ``FaissVectorDBStorage`` — needs the ``faiss`` wheel, an extra native dependency.
* ``PGVectorStorage`` — requires the ``pgvector`` **extension** installed into the
  server, exactly the kind of privileged native install the target box forbids (and this
  repo deliberately removed pgvector already).

The honest cost: Qdrant is a second process to keep alive next to Postgres, Neo4j and
Memurai, and existing NanoVectorDB vectors are **re-ingested, not migrated**.

**The lexical arm deliberately does not go through LightRAG.** :meth:`
LightRAGBackend.keyword_recall` queries the ``chunks`` table directly with PostgreSQL
full-text search, because LightRAG exposes no per-query metadata predicate to push a
tenant down into — the same limitation :meth:`LightRAGBackend._context` documents, where
foreign rows can only be discarded on the way *out*. A keyword arm built that way would
have to fetch another tenant's passages in order to drop them. Reading the corpus table
itself puts the tenant filter and the keyword match on one row and one ``WHERE`` clause,
which is the property D5 chose Postgres FTS for.

Everything that touches the `lightrag`, `neo4j`, `redis` or `sqlalchemy` packages is
imported lazily inside methods, so this module (and the whole `aegis.retrieval` package)
imports cleanly with no LightRAG install, no `aegis[data]` extra and no live stores.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from aegis.core.models import ModelRole
from aegis.retrieval.fusion import RankedList, RankedRecall
from aegis.retrieval.models import Candidate, Chunk, Recall
from aegis.retrieval.protocols import CompleteFn, EmbedFn
from aegis.retrieval.types import (
    TENANT_METADATA_KEY,
    GraphEdge,
    GraphNode,
    RetrievalOrigin,
    RetrievalScope,
    scoped_graph,
    tenant_metadata_value,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps sqlalchemy out of the import
    from collections.abc import Callable
    from contextlib import AbstractAsyncContextManager

    from sqlalchemy.ext.asyncio import AsyncSession

    #: Anything that opens a session when called — ``async_sessionmaker`` satisfies it,
    #: and so does a one-line lambda over a host's own request-path session factory.
    SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

logger = logging.getLogger(__name__)

#: Dimension of `text-embedding-3-large` (the default embedding model this backend
#: targets). Independent of :attr:`~aegis.retrieval.pipeline.RetrievalConfig.embed_dim`
#: so this module has no import-time dependency on `pipeline.py`.
_EMBED_DIM = 3072
_EMBED_MAX_TOKENS = 8192

#: Upper bound on the whole-graph snapshot used to measure ingest deltas. LightRAG's
#: ``get_knowledge_graph("*")`` caps its return at ``max_nodes``; we pass a large value
#: so demo/hackathon-scale graphs are counted in full (the count is best-effort at
#: production scale — an honest measured number, never a hardcoded constant).
_GRAPH_SNAPSHOT_CAP = 1_000_000

#: Domain-aware entity types steering LightRAG extraction into *typed* graph nodes
#: (vs the generic person/org/geo defaults). Passed via ``addon_params`` so the
#: extractor labels nodes by a broad, domain-agnostic vocabulary — richer, more
#: connectable graph, still fully API-driven (no local model).
#: Separator between the owning-tenant tag and the real source path in the ``file_path``
#: LightRAG stores per chunk. LightRAG has no metadata channel of its own — ``file_path``
#: is the only per-chunk field it round-trips — so the tenant tag rides in it and is
#: stripped back off on the way out, leaving citations exactly as they were.
_TENANT_TAG_SEP = "::"

#: The tag written for a chunk that genuinely belongs to the **shared, tenant-less**
#: corpus. It exists because "no tag" and "shared" used to be the same bytes, and they
#: are not the same fact: a stored path with no tag is one a writer that predates
#: tagging produced, or one a hand-loaded corpus put there, or one a LightRAG version
#: normalised — none of which is a claim about ownership. Tagging the shared corpus
#: explicitly is what lets an untagged path mean **unknown** and be refused, instead of
#: defaulting to the one value (:data:`None`) that every tenant is allowed to read.
#:
#: The cost is stated rather than hidden: rows written into a LightRAG working directory
#: *before* this tag existed are untagged, so they are no longer served to a
#: tenant-scoped query. That is the fail-closed direction, and re-ingesting the shared
#: corpus re-tags it.
_SHARED_TAG = "shared"

#: LightRAG joins the several ``file_path`` values a merged entity or relationship was
#: extracted from with this separator (``lightrag.constants.GRAPH_FIELD_SEP``). Read
#: rather than imported so this module keeps importing with no LightRAG install.
_GRAPH_FIELD_SEP = "<SEP>"

#: Candidate-metadata flag meaning "this row's owning tenant is known". Absent on the
#: whole-context fallback, which is a blend with no per-chunk provenance — the difference
#: between "owned by the shared corpus" and "we cannot tell", which must not be conflated
#: when the answer is about to cross a tenant boundary.
_ATTRIBUTED_KEY = "tenant_attributed"

#: The PostgreSQL text search configuration both halves of the lexical arm use. It is
#: the query-side twin of the ``to_tsvector('english', content)`` expression that
#: generates :attr:`aegis.jobs.models.Chunk.search_vector`: parse a query under one
#: configuration and index the corpus under another and the two stem differently, which
#: does not raise — it silently matches less.
_FTS_CONFIG = "english"

#: The query, turned into a ``tsquery`` whose terms are OR-ed rather than AND-ed.
#:
#: ``plainto_tsquery`` AND-s every term, so "what does clause 7.3.2 cap?" would only
#: match a passage containing *all* of those words — which is precisely the passage this
#: arm exists to find and precisely the constraint that would lose it. BM25, the ranker
#: this replaces, is disjunctive: a document matching one rare term scores, and ranking
#: (not matching) is what separates it from a document matching one common one. Rewriting
#: the connective preserves that, and it is done on ``plainto_tsquery``'s **output** —
#: already normalised, stemmed and stripped of operators — so no user text is ever
#: interpolated into SQL. ``plainto_tsquery`` emits ``&`` and nothing else (no ``!``, no
#: ``<->``), so the rewrite has exactly one thing to change.
_TSQUERY_SQL = f"replace(plainto_tsquery('{_FTS_CONFIG}', :query)::text, '&', '|')::tsquery"

#: PostgreSQL's length-normalisation flag: divide the rank by ``1 + log(length)`` of the
#: matched ``tsvector``. It is the closest thing the built-in rankers have to BM25's
#: length normalisation (``b``), and without it a long passage outranks a short one for
#: having more room to hold the query's words rather than for being a better answer.
_RANK_NORMALISATION = 1

#: Corpus-wide lexical recall over one tenant's chunks.
#:
#: The tenant predicate and the full-text predicate sit on the same row of the same
#: table, which is the whole argument for Postgres FTS over a second BM25 index (D5): the
#: filter is a ``WHERE`` clause, not another system to keep in sync. The ``documents``
#: join is a ``LEFT`` join on purpose — a citation with no filename is worse than useless
#: but a *lost hit* is worse still, so provenance degrades before recall does.
_KEYWORD_SQL = f"""
    SELECT c.id,
           c.content,
           c.tenant_id,
           c.document_id,
           c.meta,
           d.filename,
           ts_rank(c.search_vector, {_TSQUERY_SQL}, {_RANK_NORMALISATION})
               AS lexical_rank
      FROM chunks AS c
      LEFT JOIN documents AS d ON d.id = c.document_id
     WHERE c.tenant_id = :tenant
       AND c.search_vector @@ {_TSQUERY_SQL}
     ORDER BY lexical_rank DESC, c.id
     LIMIT :limit
"""

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


def _apply_store_env(config: object) -> None:
    """Export Neo4j/Postgres/Qdrant connection settings as the env vars LightRAG reads.

    LightRAG's storage impls read connection details from the environment (`NEO4J_*`,
    `POSTGRES_*`, `QDRANT_URL`) rather than constructor kwargs, so we translate the store
    config into those variables before building the instance. ``QDRANT_URL`` is the same
    variable :mod:`aegis.retrieval.vector_store` is configured from, which is the point:
    both vector consumers name one node, so they cannot drift onto two.

    ``setdefault`` throughout — an operator who already exported a value in the
    environment outranks a config default, and silently overwriting it would send a
    process at a node nobody chose.

    Args:
        config: An object exposing ``neo4j_uri``/``neo4j_user``/``neo4j_password``/
            ``postgres_dsn``/``qdrant_url`` (duck-typed — a `RetrievalConfig` in practice).
    """
    qdrant_url = getattr(config, "qdrant_url", "") or ""
    if qdrant_url:
        os.environ.setdefault("QDRANT_URL", qdrant_url)
    qdrant_api_key = getattr(config, "qdrant_api_key", "") or ""
    if qdrant_api_key:
        os.environ.setdefault("QDRANT_API_KEY", qdrant_api_key)
    os.environ.setdefault("NEO4J_URI", config.neo4j_uri)
    os.environ.setdefault("NEO4J_USERNAME", config.neo4j_user)
    os.environ.setdefault("NEO4J_PASSWORD", config.neo4j_password)

    pg = urlparse(config.postgres_dsn)
    if pg.hostname:
        os.environ.setdefault("POSTGRES_HOST", pg.hostname)
    os.environ.setdefault("POSTGRES_PORT", str(pg.port or 5432))
    if pg.username:
        os.environ.setdefault("POSTGRES_USER", pg.username)
    if pg.password:
        os.environ.setdefault("POSTGRES_PASSWORD", pg.password)
    if pg.path and len(pg.path) > 1:
        os.environ.setdefault("POSTGRES_DATABASE", pg.path.lstrip("/"))


def lightrag_embedding_adapter(embed: EmbedFn) -> object:
    """Wrap an :class:`~aegis.retrieval.protocols.EmbedFn` for LightRAG's ``EmbeddingFunc``.

    Two adaptations, both load-bearing rather than cosmetic — and both verified against
    the installed ``lightrag==1.5.6`` rather than its docs:

    * **numpy, not a list.** ``EmbeddingFunc.__call__`` validates its result with
      ``result.size`` (``lightrag/utils.py``) and the vector storages then
      ``np.concatenate`` it. ``EmbedFn`` returns ``list[list[float]]``, so handing it over
      unwrapped raises ``AttributeError: 'list' object has no attribute 'size'`` on the
      **first embed of every ingest**. This is not specific to Qdrant: ``NanoVectorDB``
      called the very same wrapper, so the LightRAG path had this defect all along and
      §9.1 is simply the change that made someone run it.
    * **Tolerate LightRAG's own keyword arguments.** It calls the wrapped function with
      ``context="document"``, and its priority limiter adds ``_priority``. A
      single-parameter lambda raises ``TypeError`` on either.

    Args:
        embed: The host's embedding callable.

    Returns:
        An async callable LightRAG can hand to ``EmbeddingFunc(func=...)``.
    """

    async def _embed_for_lightrag(texts: Sequence[str], **_: object) -> object:
        import numpy as np  # lazy: numpy arrives with lightrag, not at package import

        return np.asarray(await embed(list(texts)), dtype=np.float32)

    return _embed_for_lightrag


class LightRAGBackend:
    """A `KnowledgeBackend` on LightRAG with Neo4j + Qdrant (+ Postgres KV).

    The instance is built lazily on first use and reused thereafter. Construction is
    injected with `complete`/`embed` so extraction and embedding route through
    whatever model gateway the host application wires in.
    """

    def __init__(
        self,
        complete: CompleteFn,
        embed: EmbedFn,
        *,
        config: object | None = None,
        working_dir: str = "rag_storage",
        extract_role: ModelRole = ModelRole.CHEAP,
        session_factory: SessionFactory | None = None,
    ) -> None:
        """Initialise the backend (does not connect until `_ensure()` runs).

        Args:
            complete: The completion function (for entity/relation extraction).
            embed: The embedding function.
            config: Store connection settings (duck-typed `RetrievalConfig`); defaults
                to a fresh `~aegis.retrieval.pipeline.RetrievalConfig()`.
            working_dir: Portable relative directory for LightRAG's local bookkeeping.
            extract_role: Model role used for LightRAG's extraction LLM calls.
            session_factory: Opens the sessions :meth:`keyword_recall` reads ``chunks``
                over. A host that already has a pooled, **unprivileged** serving engine
                should inject it: reusing that connection means the RLS policy on
                ``chunks`` applies to the lexical arm as well, so the tenant predicate
                below is the second line of defence rather than the only one. Left
                unset, the backend builds its own engine from ``config.postgres_dsn``
                on first use.
        """
        self._complete = complete
        self._embed = embed
        if config is None:
            from aegis.retrieval.pipeline import RetrievalConfig

            config = RetrievalConfig()
        self._config = config
        self._working_dir = working_dir
        self._extract_role = extract_role
        self._rag: object | None = None
        self._session_factory = session_factory

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

        _apply_store_env(self._config)

        rag = LightRAG(
            working_dir=self._working_dir,
            llm_model_func=self._build_llm_func(),
            embedding_func=EmbeddingFunc(
                embedding_dim=_EMBED_DIM,
                max_token_size=_EMBED_MAX_TOKENS,
                func=lightrag_embedding_adapter(self._embed),
            ),
            kv_storage="PGKVStorage",
            # Vectors live in Qdrant — the same node aegis.retrieval writes to, reached
            # through ``QDRANT_URL`` exported above (§9.1). This replaces
            # ``NanoVectorDBStorage``, whose own docstring calls it a brute-force cosine
            # scan held in memory and persisted as a whole-file JSON rewrite: linear in
            # corpus size and single-writer, i.e. one of the two reasons a second uvicorn
            # worker could not work. Postgres remains only for the KV + doc-status stores.
            vector_storage="QdrantVectorDBStorage",
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

        Each chunk's owning tenant is tagged into the ``file_path`` LightRAG stores for
        it (see :func:`_tag_file_path`), because that is the only per-chunk field
        LightRAG hands back at recall time. Recall parses the tag off again, so this is
        invisible to citations. It is what makes :meth:`recall`'s tenant filter possible
        at all; per-tenant LightRAG instances — the real partition — are a later phase.

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
        file_paths = [
            _tag_file_path(
                str(c.metadata.get("source") or c.doc_id),
                c.metadata.get(TENANT_METADATA_KEY),
            )
            for c in chunks
        ]

        before_nodes, before_edges = await _graph_counts(rag)
        await rag.ainsert(texts, ids=ids, file_paths=file_paths)  # type: ignore[attr-defined]
        after_nodes, after_edges = await _graph_counts(rag)

        return (_delta(before_nodes, after_nodes), _delta(before_edges, after_edges))

    async def recall(self, query: str, *, top_k: int, scope: RetrievalScope) -> Recall:
        """Retrieve a wide candidate set plus the touched graph slice, scoped to a tenant.

        Args:
            query: The user query.
            top_k: Candidate breadth for the recall stage (reranked later).
            scope: The request's retrieval scope; rows owned by another tenant are
                dropped from the returned candidates (see :meth:`_context`).

        Returns:
            A `Recall` with candidates and any graph nodes/edges the query touched.
        """
        rag = await self._ensure()
        return await self._context(rag, query, mode="mix", top_k=top_k, scope=scope)

    async def recall_ranked(
        self, query: str, *, top_k: int, scope: RetrievalScope
    ) -> RankedRecall:
        """Recall **split** vector + graph lists so RRF genuinely fuses two signals.

        Rather than delegating to LightRAG's internal ``mix`` blend (one pre-fused
        list), this issues two retrievals and hands the pipeline both, tagged by origin:

        * ``naive`` mode → pure **vector** similarity over text chunks (the dense list).
        * ``local`` mode → **graph** traversal over the entity neighbourhood (walks the
          Neo4j knowledge graph the extractor built), contributing the passages and the
          nodes/edges it touched.

        Reciprocal Rank Fusion then combines them (plus the pipeline's BM25 list), so
        graph traversal is a **fused** contributor to the final ranking rather than a
        display-only side channel. The graph slice for the live viz comes from the graph
        query.

        **How much the graph arm is worth, measured rather than asserted.** This docstring
        used to say the graph was "genuinely used, not decorative", which is a statement
        about the wiring being read as a statement about the value. Our own ablation says
        the opposite about the value: arm **L1 (A4 minus the graph arm) beats the shipped
        A4 on every metric** — recall@20 0.915 → 0.972, recall@6 0.830 → 0.849, MRR@20
        0.686 → 0.692 (``runs/eval-goldset-20260819.json``, n=53, neither delta
        significant at n=53). The graph arm is kept because it is what the entity/relation
        view is drawn from and because a 53-case gold set cannot defend a small
        difference — **not** because it was shown to improve retrieval. The external
        result the design came from (LightRAG, ``arXiv:2410.05779``) is theirs, on their
        corpora, and is cited as theirs.

        **If the graph query fails, the vector arm still answers.** A ``local``-mode error
        is logged at ERROR and the recall is returned with the vector list alone — no
        graph list, no nodes, no edges. It is not silently re-issued as a ``mix`` query:
        ``mix`` is LightRAG's own pre-fused blend, so substituting it would put a
        differently-constructed list under the same origin tag and make the arm's
        contribution unattributable, which is precisely what the ablation above measures.
        Degrading to vector-only is also, on our numbers, the arm that scores higher. A
        ``naive``-mode error is **not** caught: there is no recall without the dense list,
        and pretending otherwise would return an empty answer as a successful one.

        Args:
            query: The user query.
            top_k: Candidate breadth per list (reranked later).
            scope: The request's retrieval scope; applied to both lists.

        Returns:
            A :class:`RankedRecall` with the vector list, the graph list when the graph
            query succeeded, and the touched graph nodes/edges.

        Raises:
            Exception: Whatever the dense (``naive``) query raises, unchanged.
        """
        rag = await self._ensure()
        vector = await self._context(rag, query, mode="naive", top_k=top_k, scope=scope)
        vector_list = RankedList(
            origins=(RetrievalOrigin.VECTOR,), candidates=vector.candidates
        )
        try:
            graph = await self._context(
                rag, query, mode="local", top_k=top_k, scope=scope
            )
        except Exception:
            # Loud, and with the query on it: a graph arm that has quietly stopped
            # contributing looks exactly like a graph arm that found nothing, and the
            # difference is a broken Neo4j versus a thin neighbourhood.
            logger.exception(
                "the graph arm failed for query %r (tenant %s); answering on the vector "
                "list alone — the entity/relation view will be empty for this query",
                query,
                scope.tenant_id,
            )
            return RankedRecall(lists=[vector_list], nodes=[], edges=[])
        graph_list = RankedList(
            origins=(RetrievalOrigin.GRAPH,), candidates=graph.candidates
        )
        return RankedRecall(
            lists=[vector_list, graph_list], nodes=graph.nodes, edges=graph.edges
        )

    def _sessions(self) -> SessionFactory:
        """Return the session factory the lexical arm reads ``chunks`` over.

        Built from ``config.postgres_dsn`` on first use when the host injected nothing,
        and cached: one engine (and one connection pool) per backend instance, with the
        same lifetime as the LightRAG instance beside it. The import is local because
        ``aegis.retrieval`` must stay importable without the ``aegis[data]`` extra — the
        guard in ``tests/retrieval/test_isolation.py`` is what holds that to it.

        Returns:
            A callable that opens an :class:`~sqlalchemy.ext.asyncio.AsyncSession`.
        """
        if self._session_factory is None:
            from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

            engine = create_async_engine(_asyncpg_dsn(self._config.postgres_dsn))
            self._session_factory = async_sessionmaker(engine, expire_on_commit=False)
        return self._session_factory

    async def keyword_recall(
        self, query: str, *, top_k: int, scope: RetrievalScope
    ) -> list[Candidate]:
        """Search the tenant's **whole corpus** by keyword, via PostgreSQL FTS.

        This is :class:`~aegis.retrieval.protocols.KeywordBackend`, and implementing it
        is what turns the pipeline's BM25 pass from a re-ranking of the ~20 candidates
        the dense arms already found into a genuine third recall arm. The distinction is
        not cosmetic: an exact identifier — a clause number, a case number, a part number
        — is what dense embeddings are worst at, and a pass that can only reorder what
        the dense arms returned can never surface the passage they missed.

        **Honestly, what the ranking is.** ``ts_rank`` is **not** Okapi BM25. It shares
        two of BM25's three ideas — repeated occurrences of one term saturate rather than
        accumulate (BM25's ``k1``), and :data:`_RANK_NORMALISATION` divides by document
        length (BM25's ``b``) — and it is missing the third and most important one, IDF.
        Nothing here weights a rare identifier above a common word; a passage ranks above
        another because it covers more of the query's terms, not because the terms it
        covers are rarer. **What would be better** is a real BM25 index, rejected in D5 on
        tenant-isolation and dependency grounds, and listed there under more-time.

        That gap matters less than it sounds, for a specific reason: RRF fuses the arms on
        **rank**, not on score. Two rankers that agree on an ordering fuse identically
        however differently they number it, so what this arm owes the pipeline is a
        sensible order, not a calibrated score. What the missing IDF does cost is ordering
        quality *within* this arm.

        ``ts_rank`` rather than ``ts_rank_cd``, and that choice was measured rather than
        assumed. ``ts_rank_cd`` is proportional to the number of covers, so on this
        query class a passage repeating one common query word ("clause", four times)
        outranks the passage that actually carries the identifier — the exact failure the
        arm exists to fix. ``ts_rank``'s saturation puts the identifier first.

        The scope resolves to exactly one tenant. ``chunks.tenant_id`` is ``NOT NULL``
        (see :class:`aegis.jobs.models.Chunk`), so the shared, tenant-less corpus owns no
        rows in this table and an unscoped request has nothing here to read — it returns
        empty rather than widening to everyone's rows. A scope that cannot be resolved at
        all raises, via :meth:`~aegis.retrieval.types.RetrievalScope.resolved_tenant_id`.

        A database that is unreachable raises rather than returning ``[]``: an empty list
        from this method means "no passage matched", and a retrieval arm that quietly
        stops running while still reporting itself as having run is the defect class this
        pipeline's provenance exists to prevent.

        Args:
            query: The user query.
            top_k: Maximum number of matches to return.
            scope: The request's retrieval scope.

        Returns:
            Up to ``top_k`` matching chunks as candidates, best first; empty when the
            query has no searchable terms or nothing in the tenant's corpus matches.
        """
        tenant_id = scope.resolved_tenant_id()
        if tenant_id is None or top_k <= 0:
            return []

        from sqlalchemy import text

        from aegis.governance.rls import set_tenant_scope

        async with self._sessions()() as session:
            # Bind the tenant GUC on this session before reading: it makes the
            # ``tenant_isolation`` policy engage for this transaction, so on a serving
            # role the database enforces the boundary the WHERE clause below asks for.
            # Both are kept — the predicate is what an engine-less deployment relies on,
            # the policy is what a mistake in the predicate runs into.
            await set_tenant_scope(session, tenant_id)
            rows = (
                await session.execute(
                    text(_KEYWORD_SQL),
                    {"query": query, "tenant": tenant_id, "limit": top_k},
                )
            ).mappings().all()
            await session.rollback()

        return [_keyword_candidate(row) for row in rows]

    async def knowledge_graph(
        self, *, max_nodes: int = 500
    ) -> tuple[list[GraphNode], list[GraphEdge]] | None:
        """Return the live Neo4j knowledge graph as viz-ready nodes and edges.

        This is the **whole** graph LightRAG's entity/relationship extractor has built
        in Neo4j — not the slice one query happened to touch. It is what backs
        ``GET /graph``, so the visualisation shows the platform's real accumulated
        knowledge and survives a process restart (the in-memory alternative does not).

        **It is whole, and every tenant is in it.** Neo4j has no row-level security and
        ``get_knowledge_graph("*")`` takes no predicate, so this method cannot narrow the
        read. What it can do — and now does — is carry each element's provenance out on
        ``GraphNode.owners`` / ``GraphEdge.owners``, so the caller can apply
        :func:`~aegis.retrieval.types.scoped_graph` before anything reaches a response.
        **A caller that skips that step serves every tenant's entities to whoever asked**,
        which is exactly what ``GET /graph`` did before Phase 4's ``index`` stage started
        writing every tenant's document into this one graph and made it a live path.

        Args:
            max_nodes: Upper bound on nodes requested from the store, so a large graph
                cannot blow up the payload or the force-directed layout.

        Returns:
            A ``(nodes, edges)`` tuple whose elements carry their owning tenants, or
            ``None`` when the graph store is absent or unreachable — the caller then
            reports honestly rather than showing an empty graph that looks like "no
            knowledge".
        """
        rag = await self._ensure()
        kg = await _read_knowledge_graph(rag, max_nodes=max_nodes)
        if kg is None:
            return None

        raw_nodes = getattr(kg, "nodes", None) or []
        raw_edges = getattr(kg, "edges", None) or []

        nodes: list[GraphNode] = []
        seen: set[str] = set()
        for raw in raw_nodes:
            node_id = str(getattr(raw, "id", "") or "")
            if not node_id or node_id in seen:
                continue
            seen.add(node_id)
            props = getattr(raw, "properties", None) or {}
            # LightRAG stores the human name and entity type in node properties; fall
            # back to the id and a neutral kind rather than inventing either.
            label = str(props.get("entity_id") or props.get("entity_name") or node_id)
            kind = str(props.get("entity_type") or "entity").strip().lower() or "entity"
            nodes.append(
                GraphNode(
                    id=node_id,
                    label=label,
                    kind=kind,
                    owners=_owners_of(props.get("file_path")),
                )
            )

        edges: list[GraphEdge] = []
        for raw in raw_edges:
            source = str(getattr(raw, "source", "") or "")
            target = str(getattr(raw, "target", "") or "")
            # Drop dangling edges: a force-directed viz cannot lay out an edge whose
            # endpoint was trimmed by max_nodes.
            if source not in seen or target not in seen:
                continue
            props = getattr(raw, "properties", None) or {}
            relation = str(props.get("keywords") or getattr(raw, "type", "") or "related")
            edges.append(
                GraphEdge(
                    source=source,
                    target=target,
                    relation=relation,
                    owners=_owners_of(props.get("file_path")),
                )
            )

        return (nodes, edges)

    async def _context(
        self, rag: object, query: str, *, mode: str, top_k: int, scope: RetrievalScope
    ) -> Recall:
        """Run one LightRAG context query in ``mode``, tenant-filter it, return a `Recall`.

        **This is a filter, not a partition, and that is a deliberate interim.** LightRAG
        owns its own retrieval internals: one instance means one working directory, one
        vector index and one Neo4j graph for every tenant in the process, and it exposes
        no per-query metadata predicate to push the tenant down into. So the search space
        is still shared and foreign rows are discarded on the way out. Giving each tenant
        its own instance is the real fix and is scheduled as its own phase; until then the
        row-level filter is what prevents another tenant's *content* from reaching the
        answer, and it is documented as the weaker guarantee it is — unlike the vector,
        keyword and cache tiers, which are genuinely partitioned.

        Args:
            rag: The initialised LightRAG instance.
            query: The user query.
            mode: LightRAG retrieval mode (``naive``/``local``/``mix``).
            top_k: Candidate breadth.
            scope: The request's retrieval scope.

        Returns:
            The recall with every candidate the scope may not read removed.
        """
        from lightrag import QueryParam  # lazy

        param = QueryParam(
            mode=mode, top_k=top_k, only_need_context=True, enable_rerank=False
        )
        raw = await rag.aquery(query, param=param)  # type: ignore[attr-defined]
        return _scoped_recall(_to_recall(raw), scope)


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
    kg = await _read_knowledge_graph(rag)
    if kg is None:
        return (None, None)

    nodes = getattr(kg, "nodes", None)
    edges = getattr(kg, "edges", None)
    return (
        len(nodes) if nodes is not None else None,
        len(edges) if edges is not None else None,
    )


async def _read_knowledge_graph(
    rag: object, *, max_nodes: int = _GRAPH_SNAPSHOT_CAP
) -> object | None:
    """Return LightRAG's live ``KnowledgeGraph`` (the Neo4j store), or ``None``.

    The single place that talks to the graph store, shared by :func:`_graph_counts`
    (which needs only the sizes) and :meth:`LightRAGBackend.knowledge_graph` (which
    needs the nodes and edges themselves) so both report the *same* graph.

    Args:
        rag: The initialised LightRAG instance.
        max_nodes: Upper bound passed to the accessor when its signature accepts one.

    Returns:
        The ``KnowledgeGraph`` object, or ``None`` when the store is missing or
        unreachable — never a fabricated empty graph.
    """
    graph = getattr(rag, "chunk_entity_relation_graph", None)
    if graph is None:
        return None
    getter = getattr(graph, "get_knowledge_graph", None)
    if getter is None:
        return None

    for kwargs in ({"max_nodes": max_nodes}, {}):
        try:
            return await getter("*", **kwargs)
        except TypeError:
            continue  # older/newer signature — retry without the optional bound
        except Exception:
            return None  # store unavailable → honest unknown, not a fake graph
    return None


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


def _asyncpg_dsn(dsn: str) -> str:
    """Return ``dsn`` with an async driver, so SQLAlchemy can open it from async code.

    The store settings are written once, in one form (``postgresql://…``), and read by
    both LightRAG's own synchronous storages and the async engine the lexical arm needs.
    Rewriting the scheme here rather than asking a host to configure the DSN twice keeps
    those two from drifting apart.

    Args:
        dsn: The configured PostgreSQL DSN.

    Returns:
        The same DSN carrying the ``asyncpg`` driver; a DSN that already names a driver
        is returned untouched, so an explicit choice is never overridden.
    """
    if dsn.startswith("postgresql+"):
        return dsn
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    if dsn.startswith("postgres://"):
        return dsn.replace("postgres://", "postgresql+asyncpg://", 1)
    return dsn


def _keyword_candidate(row: object) -> Candidate:
    """Turn one ``chunks`` row from the FTS query into a rerankable candidate.

    The metadata is deliberately the *same shape* the vector/graph arms produce (see
    :func:`_candidates_from_payload`), so a fused list is uniform whichever arm recalled
    a given passage. :data:`_ATTRIBUTED_KEY` is ``True`` because ownership here is not
    inferred from a path this module wrote and parsed back: it is the row's own
    ``NOT NULL`` column, and it was the query's predicate.

    The ``ts_rank`` value is carried in the metadata rather than in ``Candidate.score``.
    That field is filled by the reranker on a scale comparable across arms; a text-search
    rank is not on that scale, and putting it there would make the two look
    interchangeable in a sorted list.

    Args:
        row: A mapping row from :data:`_KEYWORD_SQL`.

    Returns:
        The candidate, carrying its source path, its owning tenant and the rank that
        selected it.
    """
    meta = row["meta"] if isinstance(row["meta"], dict) else {}
    return Candidate(
        id=str(row["id"]),
        text=str(row["content"]),
        metadata={
            # The chunk's own recorded source if the ingest wrote one, else the
            # document's filename. Never a synthesised path: a citation that points at
            # something invented is worse than one that points at nothing.
            "file_path": meta.get("source") or row["filename"],
            "document_id": row["document_id"],
            TENANT_METADATA_KEY: tenant_metadata_value(row["tenant_id"]),
            _ATTRIBUTED_KEY: True,
            "ts_rank": float(row["lexical_rank"]),
        },
    )


def _tag_file_path(source: str, tenant_value: str | None) -> str:
    """Return ``source`` tagged with the tenant that owns it, for LightRAG storage.

    **Every** chunk is tagged, including a shared-corpus one — see :data:`_SHARED_TAG`
    for why the untagged form had to stop meaning "shared". An untagged path now carries
    no ownership claim at all, which is what makes :func:`_scoped_recall` able to refuse
    it instead of handing it to whoever asks.

    Args:
        source: The real source path/id for the chunk.
        tenant_value: The owning tenant's metadata value
            (:func:`~aegis.retrieval.types.tenant_metadata_value`), or ``None`` for the
            shared corpus.

    Returns:
        The tagged path, e.g. ``"t7::handbook.md"`` or ``"shared::handbook.md"``.
    """
    tag = _SHARED_TAG if tenant_value is None else tenant_value
    return f"{tag}{_TENANT_TAG_SEP}{source}"


def _untag_file_path(file_path: object) -> tuple[str | None, str | None, bool]:
    """Split a stored ``file_path`` into ``(tenant_value, source, attributed)``.

    Only a tag this module wrote is recognised — ``t<digits>`` or :data:`_SHARED_TAG`
    before the separator. Anything else is an ordinary path that happens to contain the
    separator, so a real source path is never mistaken for a tenant tag.

    The third element is the whole point of this signature. It used to return two, and
    an untagged path came back as ``(None, path)`` — indistinguishable from the shared
    corpus, which every scope may read. Ownership was therefore *asserted* rather than
    established, and the caller stamped :data:`_ATTRIBUTED_KEY` ``True`` on it. Now the
    two are separate values: ``tenant_value is None`` means "owned by the shared corpus"
    and is only ever returned alongside ``attributed=True``.

    Args:
        file_path: The value LightRAG returned for the chunk (may be ``None``).

    Returns:
        ``(tenant_value, source, attributed)``. ``source`` is ``None`` when LightRAG
        reported no path at all; ``attributed`` is ``False`` whenever the owner could
        not be established from the path.
    """
    if file_path is None:
        return (None, None, False)
    text = str(file_path)
    tag, sep, rest = text.partition(_TENANT_TAG_SEP)
    if sep and tag == _SHARED_TAG:
        return (None, rest, True)
    if sep and tag.startswith("t") and tag[1:].isdigit():
        return (tag, rest, True)
    return (None, text, False)


def _owners_of(file_path: object) -> tuple[str | None, ...] | None:
    """Return every tenant that contributed ``file_path``, or ``None`` if unknowable.

    LightRAG merges an entity (and a relationship) across every document it was seen in
    and joins their paths with :data:`_GRAPH_FIELD_SEP`, so provenance here is a *set*,
    not a single owner. It also writes the literal ``"unknown_source"`` when a document
    reached it with no path at all.

    One unattributable contributor poisons the whole element: if any source cannot be
    owned, the merged label/description may have come from it, so the answer is "unknown"
    rather than "the ones I could read". That is what makes :func:`~aegis.retrieval.types.
    scoped_graph` able to fail closed.

    Args:
        file_path: LightRAG's ``file_path`` value for a node or edge.

    Returns:
        The owning tenant metadata values (``None`` inside the tuple means the shared
        corpus), or ``None`` when provenance could not be established at all.
    """
    if file_path is None:
        return None
    parts = [p.strip() for p in str(file_path).split(_GRAPH_FIELD_SEP) if p.strip()]
    if not parts:
        return None
    owners: list[str | None] = []
    for part in parts:
        tenant_value, _source, attributed = _untag_file_path(part)
        if not attributed:
            return None
        owners.append(tenant_value)
    return tuple(dict.fromkeys(owners))


def _scoped_recall(recall: Recall, scope: RetrievalScope) -> Recall:
    """Drop everything in ``recall`` that ``scope``'s tenant may not read.

    **Nodes and edges are filtered too, and they did not used to be.** The old contract
    said they "are entity labels for the visualisation, not document content". A node's
    label is an entity name lifted verbatim out of a document, and an edge's ``relation``
    is :func:`_edges_from_payload`'s reading of LightRAG's relationship *description* —
    a sentence the extractor wrote from the source text. Both are document content, and
    both reach the browser on the SSE ``retrieval.done`` event. See
    :func:`~aegis.retrieval.types.scoped_graph` for the two rules and why they differ.

    An unscoped run (``tenant_id is None``) has no boundary to cross, so its graph passes
    through whole — the same asymmetry the candidate loop below already had.

    Args:
        recall: The recall as LightRAG returned it.
        scope: The request's retrieval scope.

    Returns:
        The recall with only visible candidates, nodes and edges.

    Raises:
        RuntimeError: If a tenant-scoped request receives LightRAG's **whole-context
            fallback** — a blend with no per-chunk path at all, which cannot be shown to
            belong to this tenant. Serving it would be the leak; quietly dropping it
            would hide a store that cannot be scoped at all. It fails loudly instead, and
            only ever for a tenant-scoped run.
        UnresolvedTenantScopeError: If ``scope``'s tenant is present but is not an
            integer — resolved here rather than read off the attribute, so a scope that
            lost its type upstream cannot widen this filter.
    """
    tenant_id = scope.resolved_tenant_id()
    visible = scope.visible_tenant_values()
    kept = []
    for candidate in recall.candidates:
        if candidate.metadata.get(_ATTRIBUTED_KEY) is not True:
            if tenant_id is None:
                # Unscoped run: the whole corpus is the shared corpus.
                kept.append(candidate)
                continue
            if not candidate.metadata.get("file_path"):
                raise RuntimeError(
                    "LightRAG returned an unattributable blended context for a "
                    f"tenant-scoped request ({scope!r}); it cannot be shown to belong "
                    "to this tenant, so it is refused rather than served. Per-tenant "
                    "LightRAG instances are required for this store/version."
                )
            # A per-chunk row whose stored path carries no owner tag. The store *is*
            # attributable — this one row is not — so the honest answer is to refuse the
            # row rather than the request. Logged at ERROR because an untagged row in a
            # tagged store is a migration/ingest defect, not a routine miss.
            logger.error(
                "Refusing a LightRAG chunk with no owner tag (%r) for tenant-scoped "
                "request %r: its owning tenant cannot be established, so it is not "
                "served. Re-ingest the corpus so its file paths carry a tenant tag.",
                candidate.metadata.get("file_path"),
                scope,
            )
            continue
        if candidate.metadata.get(TENANT_METADATA_KEY) in visible:
            kept.append(candidate)
    nodes, edges = scoped_graph(
        recall.nodes, recall.edges, visible=None if tenant_id is None else visible
    )
    return Recall(candidates=kept, nodes=nodes, edges=edges)


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
    """Extract rerankable candidates from LightRAG chunk data (with a text fallback).

    Per-chunk candidates carry their owning tenant, parsed back out of the tagged
    ``file_path`` (see :func:`_untag_file_path`), plus :data:`_ATTRIBUTED_KEY` recording
    whether that ownership is genuinely *known* — which is a fact read off the path, not
    a constant. The whole-context fallback carries neither: it is a blend with no
    per-chunk path, so its ownership is genuinely unknown and :func:`_scoped_recall`
    refuses it under a tenant scope rather than guessing.
    """
    chunks = payload.get("chunks") if isinstance(payload, dict) else None
    candidates: list[Candidate] = []
    if isinstance(chunks, list):
        for i, chunk in enumerate(chunks):
            if not isinstance(chunk, dict):
                continue
            text = chunk.get("content") or chunk.get("text") or ""
            if not text:
                continue
            tenant_value, source, attributed = _untag_file_path(chunk.get("file_path"))
            candidates.append(
                Candidate(
                    id=str(chunk.get("id", chunk.get("chunk_id", i))),
                    text=str(text),
                    metadata={
                        "file_path": source,
                        TENANT_METADATA_KEY: tenant_value,
                        # Recorded, never asserted: a path with no tag this module wrote
                        # establishes nothing, and stamping ``True`` on it was what made
                        # an unowned chunk readable by every tenant at once.
                        _ATTRIBUTED_KEY: attributed,
                    },
                )
            )
    if not candidates and fallback_context.strip():
        candidates.append(Candidate(id="context", text=fallback_context))
    return candidates


def _nodes_from_payload(payload: dict) -> list[GraphNode]:
    """Extract graph nodes from LightRAG entity data, carrying their provenance.

    ``owners`` comes off the entity's ``file_path`` (see :func:`_owners_of`) so
    :func:`_scoped_recall` has something to filter on. An entity LightRAG could not
    attribute gets ``None``, which every restricted scope refuses.
    """
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
                    owners=_owners_of(ent.get("file_path")),
                )
            )
    return nodes


def _edges_from_payload(payload: dict) -> list[GraphEdge]:
    """Extract graph edges from LightRAG relationship data, carrying their provenance.

    ``relation`` is LightRAG's relationship ``description`` — LLM-written prose derived
    from the *source document's text*, not a type name — so this is the single highest-
    value string in a recall for a cross-tenant leak. ``owners`` is recorded for exactly
    that reason; :func:`~aegis.retrieval.types.scoped_graph` requires every one of them
    to be visible before the edge is shown.
    """
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
                    owners=_owners_of(rel.get("file_path")),
                )
            )
    return edges
