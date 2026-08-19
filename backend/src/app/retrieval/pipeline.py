"""Backend shim: retrieval orchestration now lives in ``aegis.retrieval.pipeline``.

``RetrievalConfig``/``Retriever`` are re-exported by identity. This module keeps the
platform-specific pieces that don't belong in the standalone package: building a
``RetrievalConfig`` from ``app.config.Settings``, the module-level lazily-built
process-wide default retriever (honouring the ``STORES`` run-mode setting), and the
``retrieve``/``ingest`` module-level entry points the agent graph calls.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from aegis.retrieval.pipeline import RetrievalConfig, Retriever, build_local_reranker
from aegis.retrieval.protocols import GraphBackend
from aegis.retrieval.types import GraphEdge, GraphNode, RetrievalScope
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.data import session

from . import gateway
from .cache import SemanticCache
from .lightrag_backend import LightRAGBackend
from .models import IngestReport, RetrievalResult

__all__ = [
    "RetrievalConfig",
    "RetrievalScope",
    "Retriever",
    "TenantScopeMismatch",
    "build_default_retriever",
    "get_retriever",
    "ingest",
    "knowledge_graph",
    "retrieve",
]


def _config_from_settings(settings: Settings) -> RetrievalConfig:
    """Map the platform's `Settings` onto a package `RetrievalConfig`."""
    return RetrievalConfig(
        local_rerank_enabled=settings.rerank_local,
        postgres_dsn=settings.postgres_dsn,
        neo4j_uri=settings.neo4j_uri,
        neo4j_user=settings.neo4j_user,
        neo4j_password=settings.neo4j_password,
        redis_url=settings.redis_url,
        qdrant_url=settings.qdrant_url,
        qdrant_api_key=settings.qdrant_api_key,
        vector_store_path=settings.vector_store_path,
        stores_enabled=settings.stores_enabled,
    )


def _chunk_session() -> AsyncSession:
    """Open a session for the keyword arm's corpus-wide read of ``chunks``.

    Deliberately the platform's **serving** session factory rather than an engine the
    retrieval package builds for itself. Two things follow from that, and both matter:
    the lexical arm shares the request path's connection pool instead of opening a
    second one, and it connects as the serving role — which carries neither ``SUPERUSER``
    nor ``BYPASSRLS``, so the ``tenant_isolation`` policy on ``chunks`` genuinely applies
    to it. The arm binds the tenant scope itself before reading (see
    :meth:`aegis.retrieval.lightrag_backend.LightRAGBackend.keyword_recall`), so the
    database enforces the same boundary the query's ``WHERE`` clause asks for.

    Resolved on every call rather than captured at build time, because the retriever is
    a process-wide singleton and the session factory is not: a test (or a re-bootstrap)
    that installs a different engine must not leave this arm reading the old one.

    Returns:
        A new :class:`~sqlalchemy.ext.asyncio.AsyncSession`, used as an async context
        manager by the caller.
    """
    return session.get_sessionmaker()()


def build_default_retriever(settings: Settings | None = None) -> Retriever:
    """Construct the production `Retriever` from settings (real backend + Redis cache).

    Args:
        settings: Application settings (defaults to the process singleton).

    Returns:
        A `Retriever` wired to LightRAG (Neo4j + Qdrant), the Redis semantic
        cache, the shared LLM gateway, and the local ONNX cross-encoder reranker
        (unless ``RERANK_LOCAL=false``).
    """
    settings = settings or get_settings()
    config = _config_from_settings(settings)
    complete = gateway.default_complete()
    embed = gateway.default_embed()
    backend = LightRAGBackend(
        complete, embed, config=config, session_factory=_chunk_session
    )
    cache = SemanticCache.from_url(
        settings.redis_url,
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


_default_retriever: Retriever | None = None


def _get_retriever() -> Retriever:
    """Return the lazily-built process-wide retriever (real stores, or lite).

    Honours the ``STORES`` run-mode setting: ``off`` builds the databaseless
    in-memory retriever (corpus recall + in-memory cache) so ``/query`` streams
    with no Neo4j/Redis/Postgres; anything else uses the real store-backed one.
    """
    global _default_retriever
    if _default_retriever is None:
        if get_settings().stores_enabled:
            _default_retriever = build_default_retriever()
        else:
            from .memory import build_lite_retriever

            _default_retriever = build_lite_retriever(get_settings())
    return _default_retriever


def get_retriever() -> Retriever:
    """Return the process-wide retriever, building it on first use.

    The public name for what ``retrieve``/``ingest`` use internally, for the one caller
    that needs the *backend* rather than a query: the ``index`` stage of ingestion, which
    publishes a freshly parsed document's chunks into whichever knowledge store this
    deployment actually searches. Going through the same singleton is the point — an
    ingest that wrote to a second, privately-built backend would index into a store no
    query reads, and nothing would report the corpus as missing.

    Returns:
        The shared :class:`Retriever` (real stores, or the lite one under ``STORES=off``).
    """
    return _get_retriever()


class TenantScopeMismatch(RuntimeError):
    """A caller's retrieval scope disagreed with the request's governance tenant.

    Raised, never reconciled: if the two disagree, one of them is wrong, and guessing
    which would mean either serving another tenant's documents or silently answering the
    wrong question. The caller is expected to build its scope from the same governance
    context this process bound at the edge of the request.
    """


def _governed_scope(scope: RetrievalScope) -> RetrievalScope:
    """Reconcile a caller's ``scope`` with the request's governance context.

    The governance context bound at the request edge (``app.core.governance``, the same
    contextvar the LLM gateway chokepoint and the memory stores read) is the authority on
    which tenant a request belongs to. This is the last point before the shared,
    process-wide retriever, so it is where that authority is applied:

    * No governance context, or one without a tenant → the caller's scope stands. This is
      the offline / single-tenant / ungoverned path, unchanged.
    * A governed request whose caller passed **no** tenant → the governance tenant is
      threaded in. Narrowing an unscoped request to the tenant it actually belongs to is
      strictly safer than running it unscoped, and it is what makes the request's tenant
      reach retrieval at all.
    * A governed request whose caller passed a **different** tenant → :class:`TenantScopeMismatch`.
      Never widened, never narrowed, never ignored.

    Args:
        scope: The scope the caller built.

    Returns:
        The scope to run under.

    Raises:
        TenantScopeMismatch: If ``scope`` names a different tenant from the governance
            context in force.
    """
    from app.core.governance import get_governance_context

    gov = get_governance_context()
    if gov is None or gov.tenant_id is None:
        return scope
    if scope.tenant_id is None:
        return replace(scope, tenant_id=gov.tenant_id)
    if scope.tenant_id != gov.tenant_id:
        raise TenantScopeMismatch(
            f"retrieval scope names tenant {scope.tenant_id} but the request's "
            f"governance context is tenant {gov.tenant_id}"
        )
    return scope


async def retrieve(query: str, *, scope: RetrievalScope) -> RetrievalResult:
    """Public entry point: run retrieval for `query` (see `Retriever.retrieve`).

    Args:
        query: The user query.
        scope: The caller's retrieval scope, reconciled against the request's governance
            tenant by :func:`_governed_scope` before it reaches the shared retriever.

    Returns:
        The `RetrievalResult` for `query` within the effective scope.

    Raises:
        TenantScopeMismatch: If the caller's scope contradicts the governance context.
    """
    return await _get_retriever().retrieve(query, scope=_governed_scope(scope))


async def ingest(docs: Sequence[object], *, scope: RetrievalScope) -> IngestReport:
    """Public entry point: ingest `docs` into the knowledge store (see `Retriever.ingest`).

    Args:
        docs: The documents to ingest.
        scope: The scope whose tenant will **own** the written chunks, reconciled against
            the request's governance tenant exactly as on the read path — writing under
            the wrong tenant is the same defect as reading under it.

    Returns:
        The `IngestReport` for this run.

    Raises:
        TenantScopeMismatch: If the caller's scope contradicts the governance context.
    """
    return await _get_retriever().ingest(docs, scope=_governed_scope(scope))


async def knowledge_graph(
    *, max_nodes: int = 500
) -> tuple[list[GraphNode], list[GraphEdge]] | None:
    """Return the durable Neo4j knowledge graph for ``GET /graph``, or ``None``.

    Reads the whole accumulated graph from the backend's store rather than the slice a
    single query touched, so the visualisation shows what the platform actually knows
    and survives a restart.

    ``None`` means "the graph store could not be read" — either the active backend has
    no graph (the databaseless ``STORES=off`` lite retriever) or Neo4j is unreachable.
    Callers must surface that as unknown, never as an empty graph: "we know nothing"
    and "we cannot see what we know" are different claims.

    Args:
        max_nodes: Upper bound on nodes read from the store.

    Returns:
        ``(nodes, edges)`` from Neo4j, or ``None`` when the graph is unreadable.
    """
    backend = getattr(_get_retriever(), "backend", None)
    if not isinstance(backend, GraphBackend):
        return None
    return await backend.knowledge_graph(max_nodes=max_nodes)
