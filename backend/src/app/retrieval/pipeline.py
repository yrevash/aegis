"""Backend shim: retrieval orchestration now lives in ``aegis.retrieval.pipeline``.

``RetrievalConfig``/``Retriever`` are re-exported by identity. This module keeps the
platform-specific pieces that don't belong in the standalone package: building a
``RetrievalConfig`` from ``app.config.Settings``, the module-level lazily-built
process-wide default retriever (honouring the ``STORES`` run-mode setting), and the
``retrieve``/``ingest`` module-level entry points the agent graph calls.
"""

from __future__ import annotations

from collections.abc import Sequence

from aegis.retrieval.pipeline import RetrievalConfig, Retriever

from app.config import Settings, get_settings

from . import gateway
from .cache import SemanticCache
from .lightrag_backend import LightRAGBackend
from .models import IngestReport, RetrievalResult

__all__ = [
    "RetrievalConfig",
    "Retriever",
    "build_default_retriever",
    "ingest",
    "retrieve",
]


def _config_from_settings(settings: Settings) -> RetrievalConfig:
    """Map the platform's `Settings` onto a package `RetrievalConfig`."""
    return RetrievalConfig(
        postgres_dsn=settings.postgres_dsn,
        neo4j_uri=settings.neo4j_uri,
        neo4j_user=settings.neo4j_user,
        neo4j_password=settings.neo4j_password,
        redis_url=settings.redis_url,
        qdrant_url=settings.qdrant_url,
        qdrant_api_key=settings.qdrant_api_key,
        stores_enabled=settings.stores_enabled,
    )


def build_default_retriever(settings: Settings | None = None) -> Retriever:
    """Construct the production `Retriever` from settings (real backend + Redis cache).

    Args:
        settings: Application settings (defaults to the process singleton).

    Returns:
        A `Retriever` wired to LightRAG (Neo4j + Qdrant), the Redis semantic cache, and
        the shared LLM gateway.
    """
    settings = settings or get_settings()
    config = _config_from_settings(settings)
    complete = gateway.default_complete()
    embed = gateway.default_embed()
    backend = LightRAGBackend(complete, embed, config=config)
    cache = SemanticCache.from_url(
        settings.redis_url,
        ttl_seconds=config.cache_ttl_seconds,
        similarity_threshold=config.semantic_threshold,
    )
    return Retriever(backend=backend, cache=cache, complete=complete, embed=embed, config=config)


_default_retriever: Retriever | None = None


def _get_retriever() -> Retriever:
    """Return the lazily-built process-wide retriever (real stores, or lite).

    Honours the ``STORES`` run-mode setting: ``off`` builds the databaseless
    in-memory retriever (corpus recall + in-memory cache) so ``/query`` streams
    with no Neo4j/Redis/Qdrant; anything else uses the real store-backed one.
    """
    global _default_retriever
    if _default_retriever is None:
        if get_settings().stores_enabled:
            _default_retriever = build_default_retriever()
        else:
            from .memory import build_lite_retriever

            _default_retriever = build_lite_retriever(get_settings())
    return _default_retriever


async def retrieve(query: str, *, persona: str | None = None) -> RetrievalResult:
    """Public entry point: run retrieval for `query` (see `Retriever.retrieve`)."""
    return await _get_retriever().retrieve(query, persona=persona)


async def ingest(docs: Sequence[object]) -> IngestReport:
    """Public entry point: ingest `docs` into the knowledge store (see `Retriever.ingest`)."""
    return await _get_retriever().ingest(docs)
