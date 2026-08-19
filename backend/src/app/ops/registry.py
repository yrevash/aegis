"""Strangler shim: ``app.ops.registry`` delegates to :mod:`aegis.ops.registry`.

The versioned prompt registry + its process-wide active-prompt cache now live in the
standalone ``aegis.ops`` package. Re-exported here (including the shared ``_ACTIVE_CACHE``
dict, by identity, so the harness's synchronous hot-path read and any test that pokes the
cache observe the same object) so every ``app.ops.registry.*`` call site is unchanged.

**This shim is also where the tenant comes from, and that is the point of it.**
``aegis.ops`` is host-agnostic and takes the tenant as an explicit argument; this host
knows the request's tenant without being told, from the sealed governance context that
``require_auth`` populates from :class:`~app.api.deps.AuthContext`. So the three
tenant-scoped reads — :func:`get_cached_active`, :func:`get_active`,
:func:`list_versions` — default their scope from that context rather than from a
parameter, which is what makes §7.16 row 12 hold here by construction: **there is no
argument a caller could pass to read another tenant's prompt**, and every existing call
site in ``app.api.routes`` became tenant-scoped without changing a line.

Passing ``tenant_id`` explicitly is still supported and is what the platform-staff
surfaces use (a tenant selector, and the startup cache warm-up, which has no request
context at all). ``None`` means the **platform** scope — the ``tenant_id IS NULL`` rows
every tenant falls back to — never "any tenant".
"""

from __future__ import annotations

import logging
from typing import Any

from aegis.ops.registry import (
    _ACTIVE_CACHE,
    PLATFORM_SCOPE,
    clear_cache,
    create_draft,
    promote,
    refresh_cache,
    rollback,
)
from aegis.ops.registry import get_active as _get_active
from aegis.ops.registry import get_cached_active as _get_cached_active
from aegis.ops.registry import list_versions as _list_versions

logger = logging.getLogger(__name__)

#: "No tenant argument was given" — distinct from ``None``, which is the platform scope.
_FROM_CONTEXT: Any = object()

__all__ = [
    "PLATFORM_SCOPE",
    "_ACTIVE_CACHE",
    "clear_cache",
    "create_draft",
    "current_tenant_id",
    "get_active",
    "get_cached_active",
    "list_versions",
    "promote",
    "refresh_cache",
    "rollback",
]


def current_tenant_id() -> int | None:
    """Return this request's sealed tenant id, or ``None`` (the platform scope).

    Read from the governance context ``require_auth`` binds from the caller's
    :class:`~app.api.deps.AuthContext` — never from a query parameter, a header or a
    request body. An absent context (a startup task, an offline run) is the platform
    scope, which is the correct reading: nothing tenant-owned is in play.
    """
    try:
        from app.core.governance import get_governance_context  # noqa: PLC0415

        gov = get_governance_context()
        return gov.tenant_id if gov is not None else None
    except Exception:  # noqa: BLE001 - governance is optional at this seam
        return None


def _scope(tenant_id: Any) -> int | None:  # noqa: ANN401 - int | None | _FROM_CONTEXT
    """Resolve an explicit tenant argument, or fall back to the sealed request scope."""
    return current_tenant_id() if tenant_id is _FROM_CONTEXT else tenant_id


def get_cached_active(
    prompt_key: str, tenant_id: Any = _FROM_CONTEXT  # noqa: ANN401 - see _scope
) -> tuple[str, dict[str, Any], int] | None:
    """Read the active prompt for ``prompt_key`` in **this request's** tenant scope.

    The synchronous hot-path read the harness makes on every run. Defaulting the tenant
    from the governance context is the fix for the leak this task exists to close: the
    cache was keyed on ``prompt_key`` alone, so whichever tenant promoted last was served
    to all of them.
    """
    return _get_cached_active(prompt_key, _scope(tenant_id))


async def get_active(
    session: Any,  # noqa: ANN401 - AsyncSession, kept loose like the rest of the shim
    prompt_key: str,
    tenant_id: Any = _FROM_CONTEXT,  # noqa: ANN401 - see _scope
) -> Any:  # noqa: ANN401 - PromptVersion | None
    """Read the active version row for ``prompt_key`` in this request's tenant scope."""
    return await _get_active(session, prompt_key, _scope(tenant_id))


async def list_versions(
    session: Any,  # noqa: ANN401 - AsyncSession, kept loose like the rest of the shim
    prompt_key: str,
    tenant_id: Any = _FROM_CONTEXT,  # noqa: ANN401 - see _scope
) -> Any:  # noqa: ANN401 - list[PromptVersion]
    """List this request's tenant's versions for ``prompt_key``, newest first."""
    return await _list_versions(session, prompt_key, _scope(tenant_id))
