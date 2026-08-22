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

Heavy deps (``sqlalchemy`` / ``pyjwt`` / ``argon2-cffi``) live under the
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
from aegis.governance.config import (
    RBAC_LADDER,
    effective_config,
    role_rank,
)
from aegis.governance.context import (
    GovernanceContext,
    GovernanceLimits,
    get_governance_context,
    governed,
    reset_governance_context,
    set_governance_context,
)
from aegis.governance.dashboard import (
    budget_status,
    governance_dashboard,
    usage_summary,
)
from aegis.governance.enforcement import (
    CrossTenantBudgetError,
    DuplicateTenantError,
    DuplicateUserError,
    LastPlatformAdminError,
    UserCapAboveTenantCapError,
    configure_enforcement,
    create_tenant,
    create_user,
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
from aegis.governance.rls import (
    SERVING_ROLE,
    RlsBypassError,
    RlsEnforcement,
    audit_rls_enforcement,
    bootstrap_rls,
    grant_serving_role,
    report_rls_enforcement,
    set_tenant_scope,
)
from aegis.governance.schema import (
    SchemaDriftError,
    declared_enum_labels,
    plan_additive_columns,
    plan_enum_values,
    reconcile_additive_columns,
    reconcile_enum_values,
)
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
    BudgetDefaults,
    BudgetRow,
    BudgetStatusRow,
    GovernanceConfig,
    GovernanceDashboard,
    JwtConfig,
    RlsConfig,
    Role,
    RoleTier,
    TenantRow,
    UsageByModel,
    UsageSeriesPoint,
    UsageSummary,
)

__all__ = [
    "MEMBER",
    "PLATFORM_ADMIN",
    "RBAC_LADDER",
    "TENANT_ADMIN",
    "AdminUserRow",
    "AuditLog",
    "AuditLogRow",
    "Budget",
    "BudgetDefaults",
    "BudgetRow",
    "BudgetScope",
    "BudgetStatusRow",
    "BudgetWindow",
    "GovernanceConfig",
    "GovernanceContext",
    "GovernanceDashboard",
    "GovernanceLimits",
    "JwtConfig",
    "CrossTenantBudgetError",
    "LastPlatformAdminError",
    "UserCapAboveTenantCapError",
    "SERVING_ROLE",
    "RlsBypassError",
    "RlsConfig",
    "RlsEnforcement",
    "Role",
    "RoleTier",
    "SchemaDriftError",
    "SecurityConfig",
    "Tenant",
    "TenantRow",
    "TenantStatus",
    "TokenClaims",
    "UsageByModel",
    "UsageLedger",
    "UsageSeriesPoint",
    "UsageSummary",
    "User",
    "audit_rls_enforcement",
    "bootstrap_rls",
    "budget_status",
    "coarse_role_from_fine",
    "configure_audit",
    "configure_enforcement",
    "configure_governance",
    "configure_security",
    "create_access_token",
    "decode_access_token",
    "effective_config",
    "effective_limits",
    "enforce_governance",
    "get_governance_context",
    "governance_dashboard",
    "governed",
    "grant_serving_role",
    "hash_password",
    "list_budgets",
    "list_recent_audit",
    "DuplicateTenantError",
    "DuplicateUserError",
    "create_tenant",
    "create_user",
    "list_tenants",
    "list_users",
    "declared_enum_labels",
    "plan_additive_columns",
    "plan_enum_values",
    "principal_role",
    "reconcile_additive_columns",
    "reconcile_enum_values",
    "record_audit",
    "record_usage",
    "report_rls_enforcement",
    "reset_governance_context",
    "role_rank",
    "set_governance_context",
    "set_tenant_scope",
    "update_user_role",
    "upsert_budget",
    "usage_rollup",
    "usage_summary",
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
