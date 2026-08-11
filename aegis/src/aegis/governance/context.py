"""Per-request governance context, threaded through :mod:`contextvars`.

The tenancy/budget boundary must reach the single gateway chokepoint
(:func:`aegis.gateway.complete`) without threading a tenant id through every graph
node signature. A :class:`GovernanceContext` is therefore stashed in a
``ContextVar`` at the edge of a request and read at the chokepoint.

This module owns only the *seam*: the ``set`` / ``get`` / ``reset`` helpers over a
process-wide ``ContextVar``. The typed context itself lives in
:mod:`aegis.governance.types` (dependency-free) and is re-exported here for
convenience.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

from aegis.governance.types import GovernanceContext, GovernanceLimits

__all__ = [
    "GovernanceContext",
    "GovernanceLimits",
    "get_governance_context",
    "reset_governance_context",
    "set_governance_context",
]


# The process-wide context slot. ``None`` means "no governance in force" (the
# default), which keeps every existing flow behaving exactly as before.
_governance_context: ContextVar[GovernanceContext | None] = ContextVar(
    "governance_context", default=None
)


def set_governance_context(ctx: GovernanceContext) -> Token[GovernanceContext | None]:
    """Bind ``ctx`` for the current context and return a reset token.

    Args:
        ctx: The governance context to install for the request.

    Returns:
        A token to pass to :func:`reset_governance_context` to restore the prior
        value (use it in a ``try/finally`` around the request scope).
    """
    return _governance_context.set(ctx)


def get_governance_context() -> GovernanceContext | None:
    """Return the governance context in force, or ``None`` if none is bound."""
    return _governance_context.get()


def reset_governance_context(token: Token[GovernanceContext | None]) -> None:
    """Restore the governance context to the value captured by ``token``.

    Args:
        token: The token returned by :func:`set_governance_context`.
    """
    _governance_context.reset(token)
