"""Backend shim: the memory ORM models now live in :mod:`aegis.memory.stores`.

They register on :class:`aegis.data.AegisBase` (not the platform's ``app.data`` Base);
the backend's :func:`app.data.session.bootstrap` materialises that metadata alongside its
own. Re-exported here so every ``from app.memory.stores import ...`` call site (routes,
the agent deps write path, admin surfaces) is unchanged.
"""

from __future__ import annotations

from aegis.memory.stores import (
    ConsolidationStatus,
    MemoryConsolidationJob,
    MemoryFact,
    MemoryMessage,
    MemoryOrigin,
    MemoryProfile,
    MemorySession,
    MemoryWriteLog,
    WriteOp,
)

__all__ = [
    "ConsolidationStatus",
    "MemoryConsolidationJob",
    "MemoryFact",
    "MemoryMessage",
    "MemoryOrigin",
    "MemoryProfile",
    "MemorySession",
    "MemoryWriteLog",
    "WriteOp",
]
