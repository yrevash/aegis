"""Backend shim: the memory WRITE path now lives in :mod:`aegis.memory.consolidate`.

The injected completer/embedder are supplied by the caller (``app.agent.deps.MemoryDeps``
binds ``app.core.llm.complete`` + ``app.retrieval.gateway.default_embed``); the domain
:class:`~aegis.memory.spec.MemorySpec` is resolved from the process-wide default the
backend configures in :mod:`app.memory`. Re-exported so ``sweep_pending`` /
``enqueue_consolidation`` call sites (deps write path, the startup sweeper) are unchanged.
"""

from __future__ import annotations

from aegis.memory.consolidate import (
    CompleteFn,
    ConsolidationResult,
    EmbedFn,
    consolidate,
    enqueue_consolidation,
    prune_forgotten,
    sweep_pending,
)

__all__ = [
    "CompleteFn",
    "ConsolidationResult",
    "EmbedFn",
    "consolidate",
    "enqueue_consolidation",
    "prune_forgotten",
    "sweep_pending",
]
