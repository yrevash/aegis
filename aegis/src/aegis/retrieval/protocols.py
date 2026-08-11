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
from aegis.retrieval.models import Chunk, Recall


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

    async def recall(self, query: str, *, top_k: int, persona: str | None = None) -> Recall:
        """Return a wide candidate set plus the graph slice touched by the query."""
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
        self, query: str, *, top_k: int, persona: str | None = None
    ) -> RankedRecall:
        """Return per-signal ranked lists plus the touched graph slice."""
        ...
