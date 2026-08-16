"""Structural interfaces (typing Protocols) for the retrieval pipeline.

Depending on *behaviour*, not concrete classes, keeps the pipeline testable without
live infrastructure: unit tests inject fakes that satisfy these Protocols, while
production wiring injects a real LLM completer/embedder, a real knowledge backend
(LightRAG or otherwise), and a real Redis cache. Every heavy dependency stays
outside this module — it depends only on :mod:`aegis.core.models` and pure-Python
sibling modules.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from aegis.core.models import ModelRole
from aegis.retrieval.fusion import RankedRecall
from aegis.retrieval.models import Candidate, Chunk, Recall
from aegis.retrieval.types import GraphEdge, GraphNode, RetrievalScope


@runtime_checkable
class CompletionResult(Protocol):
    """The subset of a chat-completion result the retrieval pipeline reads."""

    content: str


class CompleteFn(Protocol):
    """Structural type of an injected chat-completion callable."""

    async def __call__(
        self,
        role: ModelRole,
        messages: list[dict[str, object]],
        *,
        temperature: float = 0.0,
        response_format: dict[str, object] | None = None,
    ) -> CompletionResult:
        """Complete a chat request for the given role and return the result."""
        ...


class EmbedFn(Protocol):
    """Structural type of an injected embedding callable."""

    async def __call__(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts into vectors."""
        ...


class KnowledgeBackend(Protocol):
    """A graph+vector knowledge store that ingests chunks and recalls candidates."""

    async def ingest_chunks(
        self, chunks: Sequence[Chunk]
    ) -> tuple[int | None, int | None]:
        """Write chunks to the store; return `(entities, relations)` extracted.

        Either element is ``None`` when the backend cannot honestly report that count
        for this run mode (e.g. a lite backend with no graph extraction). Callers must
        treat ``None`` as "unknown", never coerce it to ``0``.
        """
        ...

    async def recall(self, query: str, *, top_k: int, scope: RetrievalScope) -> Recall:
        """Return a wide candidate set plus the graph slice touched by the query.

        ``scope`` is **required and has no default**: an unscoped recall is a
        cross-tenant read, and a defaulted parameter is precisely how that gets forgotten
        at a call site. Implementations must restrict the rows they consider to
        :meth:`~aegis.retrieval.types.RetrievalScope.visible_tenant_values`.
        """
        ...


@runtime_checkable
class MultiListBackend(Protocol):
    """Optional capability: a backend that exposes recall as *split* ranked lists.

    A plain :class:`KnowledgeBackend` returns a single (already-blended) candidate
    list. A backend that also implements ``recall_ranked`` hands the pipeline one
    origin-tagged :class:`~aegis.retrieval.fusion.RankedList` *per retrieval signal*
    (e.g. a dense-vector list and a graph-expansion list), so RRF genuinely fuses
    them rather than re-splitting a pre-fused list. The pipeline prefers this method
    when present and falls back to :meth:`KnowledgeBackend.recall` otherwise.
    """

    async def recall_ranked(
        self, query: str, *, top_k: int, scope: RetrievalScope
    ) -> RankedRecall:
        """Return per-signal ranked lists plus the touched graph slice, within ``scope``."""
        ...


@runtime_checkable
class KeywordBackend(Protocol):
    """Optional capability: a backend that can search its **whole corpus** by keyword.

    Without this, the pipeline's BM25 pass can only score the candidates the dense /
    graph arms already returned — which reorders them but can never surface a
    keyword-only document, and computes its IDF over a ~20-document "corpus". A backend
    implementing ``keyword_recall`` runs the keyword match over everything it holds, so
    BM25 becomes a genuinely independent recall arm (and is reported as one). Backends
    that cannot do this are not faked into looking like they can: the pipeline demotes
    the pass to a labelled re-ranking step instead.
    """

    async def keyword_recall(
        self, query: str, *, top_k: int, scope: RetrievalScope
    ) -> Sequence[Candidate]:
        """Return up to ``top_k`` corpus-wide keyword matches within ``scope``, best first.

        Implementations rank over the entire corpus *visible to ``scope``* (not a
        pre-filtered pool, and not another tenant's rows) and return only genuine
        matches, so an empty list honestly means "no keyword hit". The tenant predicate
        belongs on the same row as the keyword match — a keyword arm that skips it
        re-opens the leak the vector arm just closed.
        """
        ...


@runtime_checkable
class GraphBackend(Protocol):
    """Optional capability: a backend whose knowledge graph can be read *whole*.

    :meth:`KnowledgeBackend.recall` returns only the graph slice one query touched.
    A backend implementing this protocol can additionally hand back the entire
    accumulated knowledge graph from its store (Neo4j in production), which is what
    the ``GET /graph`` visualisation reads. Because that store is durable, the graph
    survives a process restart — unlike an in-memory accumulator, which starts empty
    on every boot and so under-reports what the platform actually knows.
    """

    async def knowledge_graph(
        self, *, max_nodes: int = 500
    ) -> tuple[list[GraphNode], list[GraphEdge]] | None:
        """Return the whole graph as ``(nodes, edges)``, or ``None`` if unreadable."""
        ...
