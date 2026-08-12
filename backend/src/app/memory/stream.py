"""Backend shim: memory lifecycle streaming lives in :mod:`aegis.memory.stream`.

Re-exports the à la carte AG-UI streamers for the whole memory lifecycle — recall/query
(with cache hit/miss), add/consolidate, and forget/delete — so backend call sites import
them from ``app.memory``. Each write commits the authoritative SQL before invalidating the
derived semantic cache.
"""

from __future__ import annotations

from aegis.memory.stream import AssembleLike, stream_add, stream_assemble, stream_forget

__all__ = ["AssembleLike", "stream_add", "stream_assemble", "stream_forget"]
