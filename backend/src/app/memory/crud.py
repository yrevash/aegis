"""Backend shim: explicit memory CRUD lives in :mod:`aegis.memory.crud`.

Re-exports the subject+tenant-scoped list/read/forget operations (``DELETE``-audited,
soft by default) so backend call sites import them from ``app.memory``.
"""

from __future__ import annotations

from aegis.memory.crud import forget_fact, get_fact, list_facts

__all__ = ["forget_fact", "get_fact", "list_facts"]
