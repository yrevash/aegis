"""Backend shim: the memory semantic cache lives in :mod:`aegis.memory.cache`.

Re-exports the package's :class:`MemorySemanticCache` (RedisVL in production, an explicit
labeled in-memory fallback offline) so backend call sites import it from ``app.memory``.
The host wires the concrete backend (``redis_url`` / ``require_redis``) at its composition
root; the durable SQL rows stay authoritative and the cache is invalidated on writes.
"""

from __future__ import annotations

from aegis.memory.cache import (
    BACKEND_MEMORY,
    BACKEND_REDIS,
    MemoryCacheHit,
    MemorySemanticCache,
)

__all__ = [
    "BACKEND_MEMORY",
    "BACKEND_REDIS",
    "MemoryCacheHit",
    "MemorySemanticCache",
]
