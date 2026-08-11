"""Aegis governance — the importable multi-tenant governance core.

JWT (HS256) + Argon2id auth, four-role RBAC tiers, a contextvar-threaded
:class:`GovernanceContext`, per-tenant/user budget enforcement (token/usd/rpm/tpm),
the durable usage ledger, the audit writer, the tenancy ORM (on
:class:`aegis.data.AegisBase`), and the Postgres RLS bootstrap.

**Config is injected** — there is no host config import:

- :func:`aegis.governance.security.configure_security` wires the JWT secret/algorithm/TTL.
- :func:`configure_governance` wires the session factory (the host owns the engine) and,
  optionally, the ``set_tenant_scope`` RLS binder, for both the enforcement and audit
  data layers.

Heavy deps (``sqlalchemy`` / ``pgvector`` / ``pyjwt`` / ``argon2-cffi``) live under the
``aegis[governance]`` extra. Importing this package pulls those, but none of the
retrieval/gateway/agent megadeps (no ``litellm`` / ``fastapi`` / ``langgraph``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from aegis.governance.audit import (
    configure_audit,
    list_recent_audit,
    record_audit,
)
from aegis.governance.context import (
    GovernanceContext,
    GovernanceLimits,
    get_governance_context,
    reset_governance_context,
    set_governance_context,
)
from aegis.governance.enforcement import (
    LastPlatformAdminError,
    configure_enforcement,
    effective_limits,
    enforce_governance,
    list_budgets,
    list_tenants,
    list_users,
    record_usage,
    update_user_role,
    upsert_budget,
    usage_rollup,
    user_tenant_id,
)
from aegis.governance.models import (
    AuditLog,
    Budget,
    BudgetScope,
    BudgetWindow,
    Tenant,
    TenantStatus,
    UsageLedger,
    User,
)
from aegis.governance.rls import bootstrap_rls, set_tenant_scope
from aegis.governance.security import (
    MEMBER,
    PLATFORM_ADMIN,
    TENANT_ADMIN,
    SecurityConfig,
    TokenClaims,
    coarse_role_from_fine,
    configure_security,
    create_access_token,
    decode_access_token,
    hash_password,
    principal_role,
    verify_password,
)
from aegis.governance.types import (
    AdminUserRow,
    AuditLogRow,
    BudgetRow,
    Role,
    TenantRow,
    UsageByModel,
    UsageSeriesPoint,
)

__all__ = [
    "MEMBER",
    "PLATFORM_ADMIN",
    "TENANT_ADMIN",
    "AdminUserRow",
    "AuditLog",
    "AuditLogRow",
    "Budget",
    "BudgetRow",
    "BudgetScope",
    "BudgetWindow",
    "GovernanceContext",
    "GovernanceLimits",
    "LastPlatformAdminError",
    "Role",
    "SecurityConfig",
    "Tenant",
    "TenantRow",
    "TenantStatus",
    "TokenClaims",
    "UsageByModel",
    "UsageLedger",
    "UsageSeriesPoint",
    "User",
    "bootstrap_rls",
    "coarse_role_from_fine",
    "configure_audit",
    "configure_enforcement",
    "configure_governance",
    "configure_security",
    "create_access_token",
    "decode_access_token",
    "effective_limits",
    "enforce_governance",
    "get_governance_context",
    "hash_password",
    "list_budgets",
    "list_recent_audit",
    "list_tenants",
    "list_users",
    "principal_role",
    "record_audit",
    "record_usage",
    "reset_governance_context",
    "set_governance_context",
    "set_tenant_scope",
    "update_user_role",
    "upsert_budget",
    "usage_rollup",
    "user_tenant_id",
    "verify_password",
]

_SetTenantScope = Callable[[AsyncSession, int | None], Awaitable[None]]


def configure_governance(
    *,
    session_factory: Callable[[], AsyncSession],
    set_tenant_scope: _SetTenantScope | None = None,
) -> None:
    """Wire the injected session factory (+ RLS binder) for enforcement and audit.

    Call once at host startup (or import time of a strangler shim). Both the budget
    enforcement / ledger data layer and the audit writer open their own short-lived
    sessions from this factory.

    Args:
        session_factory: A zero-arg callable returning an :class:`AsyncSession`
            (used as ``async with session_factory() as session``).
        set_tenant_scope: The RLS scope binder; defaults to
            :func:`aegis.governance.rls.set_tenant_scope` in each data-layer module.
    """
    configure_enforcement(session_factory=session_factory, set_tenant_scope=set_tenant_scope)
    configure_audit(session_factory=session_factory, set_tenant_scope=set_tenant_scope)
