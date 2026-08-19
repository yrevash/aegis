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

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

from aegis.governance.types import GovernanceContext, GovernanceLimits, Role

__all__ = [
    "GovernanceContext",
    "GovernanceLimits",
    "get_governance_context",
    "governed",
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


@contextmanager
def governed(
    *, tenant_id: int | None, user_id: int | None = None, role: Role | None = None
) -> Iterator[GovernanceContext]:
    """Bind a governance context for the duration of a block, and always unbind it.

    The ``set``/``try``/``finally``/``reset`` quartet written out by hand at four call
    sites, three of which are background work. It exists because the failure mode of
    forgetting the ``finally`` is not a crash: the context simply *stays* bound, and the
    next unit of work on that task — a different tenant's, quite possibly — is billed to
    whoever the last one belonged to.

    **This is how background work becomes governed at all.** ``enforce_governance`` and
    the usage ledger both hang off the context bound at the gateway chokepoint, and a
    sweeper or an activity that binds nothing spends money that is neither capped nor
    recorded. The tenant passed here must come from the *work* — a job row, an activity
    argument — and never from whatever happened to be bound already.

    Args:
        tenant_id: The tenant to bill and cap, or ``None`` for platform-owned work,
            which is recorded in the ledger under a NULL tenant and capped by nothing at
            the gateway (there is no ``budgets`` row for "nobody"; see
            ``app.core.llm._governed``).
        user_id: The acting user, when the work belongs to one.
        role: The acting role, when it is known.

    Yields:
        The context that was bound, so a caller can log or assert on it.
    """
    ctx = GovernanceContext(tenant_id=tenant_id, user_id=user_id, role=role)
    token = set_governance_context(ctx)
    try:
        yield ctx
    finally:
        reset_governance_context(token)
