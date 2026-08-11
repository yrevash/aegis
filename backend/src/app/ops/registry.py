"""Strangler shim: ``app.ops.registry`` delegates to :mod:`aegis.ops.registry`.

The versioned prompt registry + its process-wide active-prompt cache now live in the
standalone ``aegis.ops`` package. Re-exported here (including the shared ``_ACTIVE_CACHE``
dict, by identity, so the harness's synchronous hot-path read and any test that pokes the
cache observe the same object) so every ``app.ops.registry.*`` call site is unchanged.
"""

from __future__ import annotations

from aegis.ops.registry import (
    _ACTIVE_CACHE,
    clear_cache,
    create_draft,
    get_active,
    get_cached_active,
    list_versions,
    promote,
    refresh_cache,
    rollback,
)

__all__ = [
    "_ACTIVE_CACHE",
    "clear_cache",
    "create_draft",
    "get_active",
    "get_cached_active",
    "list_versions",
    "promote",
    "refresh_cache",
    "rollback",
]
