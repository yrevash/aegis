"""Backend shim: memory config now lives in the standalone :mod:`aegis.memory.config`.

Re-exports the package's :class:`MemoryConfig` / :data:`MemoryBackend` so existing
``from app.memory.config import MemoryConfig`` call sites are unchanged.
"""

from __future__ import annotations

from aegis.memory.config import MemoryBackend, MemoryConfig

__all__ = ["MemoryBackend", "MemoryConfig"]
