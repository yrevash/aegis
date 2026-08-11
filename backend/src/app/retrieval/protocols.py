"""Backend shim: retrieval Protocols now live in ``aegis.retrieval.protocols``."""

from __future__ import annotations

from aegis.retrieval.protocols import (
    CompleteFn,
    CompletionResult,
    EmbedFn,
    KnowledgeBackend,
    MultiListBackend,
)

__all__ = [
    "CompleteFn",
    "CompletionResult",
    "EmbedFn",
    "KnowledgeBackend",
    "MultiListBackend",
]
