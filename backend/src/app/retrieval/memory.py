"""Backend shim: the lite/no-database retrieval backend now lives in ``aegis.retrieval.memory``.

``STORES=off`` swaps the LightRAG (Neo4j + Qdrant) backend and the Redis semantic
cache for the self-contained equivalents, so the full agentic slice runs with no
databases. ``aegis.retrieval``'s ``InMemoryKnowledgeBackend.from_corpus`` takes an
explicit ``path``/``docs`` argument (the package has no notion of this platform's
bundled adapter corpus); :class:`InMemoryKnowledgeBackend` below is a thin subclass
that restores the old zero-argument behaviour by defaulting to
``app.adapter.corpus`` when the caller supplies neither.
"""

from __future__ import annotations

from importlib.resources import files

from aegis.retrieval.memory import InMemoryKnowledgeBackend as _InMemoryKnowledgeBackend
from aegis.retrieval.memory import (
    InMemoryRedis,
    _local_embed,  # noqa: F401 - re-exported for existing tests
)
from aegis.retrieval.pipeline import RetrievalConfig, Retriever, build_local_reranker
from aegis.retrieval.vector_store import QdrantVectorStore

from app.config import Settings

from . import gateway
from .cache import SemanticCache

__all__ = [
    "InMemoryKnowledgeBackend",
    "InMemoryRedis",
    "build_lite_retriever",
]


class InMemoryKnowledgeBackend(_InMemoryKnowledgeBackend):
    """The lite backend, defaulting :meth:`from_corpus` to this platform's seed corpus."""

    @classmethod
    def from_corpus(  # type: ignore[override]
        cls,
        *,
        path: object | None = None,
        docs: object | None = None,
        chunk_size: int = 400,
        overlap: int = 60,
        vector_store: QdrantVectorStore | None = None,
    ) -> InMemoryKnowledgeBackend:
        """Build a backend from ``path``/``docs``, or this app's adapter corpus.

        Falls back to an empty backend (still valid) if the adapter corpus package is
        absent — retrieval then returns no candidates and the agent answers from the
        model alone, degrading cleanly rather than erroring.

        ``vector_store`` is forwarded rather than dropped: since §8.4 a backend given no
        store and no process-wide declaration raises, so an override that silently
        swallowed the argument would turn a caller's explicit choice into that error.
        """
        if path is None and docs is None:
            try:
                corpus_dir = files("app.adapter.corpus")
            except (ModuleNotFoundError, FileNotFoundError):
                corpus_dir = None
            path = str(corpus_dir) if corpus_dir is not None else None
        return super().from_corpus(  # type: ignore[return-value]
            path=path,
            docs=docs,
            chunk_size=chunk_size,
            overlap=overlap,
            vector_store=vector_store,
        )


def build_lite_retriever(settings: Settings) -> Retriever:
    """Build a databaseless :class:`Retriever`: adapter-corpus recall + in-memory cache.

    Embeddings still run through the real gateway and reranking still runs on the local
    ONNX cross-encoder (lite mode drops the *databases*, nothing else), so retrieval
    quality and the cache-hit metric are genuine rather than a demo shape.
    """
    config = RetrievalConfig(local_rerank_enabled=settings.rerank_local)
    complete = gateway.default_complete()
    embed = gateway.default_embed()
    backend = InMemoryKnowledgeBackend.from_corpus(
        chunk_size=config.chunk_size,
        overlap=config.chunk_overlap,
        # Lite mode IS the ephemeral engine, so it says so rather than inheriting the
        # process-wide store declaration (§8.4). A retriever built here is deliberately
        # non-durable; a caller that wanted durability wired the full stores instead.
        vector_store=QdrantVectorStore.local(),
    )
    cache = SemanticCache(
        InMemoryRedis(),
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
