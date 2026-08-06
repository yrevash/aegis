"""Per-request governance context, threaded through :mod:`contextvars` (§3.3).

The tenancy/budget boundary must reach the single LiteLLM chokepoint
(:func:`app.core.llm.complete`) without threading a tenant id through every graph
node signature. A :class:`GovernanceContext` is therefore stashed in a
``ContextVar`` at the edge of a request and read at the chokepoint.

This module freezes only the *contract and the seam*: the typed context plus
``set`` / ``get`` / ``reset`` helpers. Budget/rate **enforcement** that reads this
context lands in the governance phase; here nothing reads it yet, so behaviour is
unchanged (the default context is ``None``).
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field

from app.api.schemas import Role


@dataclass(frozen=True)
class GovernanceLimits:
    """Effective spend/rate caps for the current principal (nearest-binding).

    Attributes:
        token_cap: Max tokens over the budget window; ``None`` means uncapped.
        usd_cap: Max USD spend over the budget window; ``None`` means uncapped.
        rpm: Max requests per minute; ``None`` means unlimited.
        tpm: Max tokens per minute; ``None`` means unlimited.
    """

    token_cap: int | None = None
    usd_cap: float | None = None
    rpm: int | None = None
    tpm: int | None = None


@dataclass(frozen=True)
class GovernanceContext:
    """The tenant/user/role and limits bound to the current request.

    Attributes:
        tenant_id: The tenant the request is pinned to (``None`` == unscoped/system).
        user_id: The acting user, if resolved.
        role: The acting principal's RBAC role.
        limits: The effective, inward-enforced spend/rate caps.
    """

    tenant_id: int | None = None
    user_id: int | None = None
    role: Role | None = None
    limits: GovernanceLimits = field(default_factory=GovernanceLimits)


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
