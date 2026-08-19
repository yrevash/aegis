"""Backend shim: the governance data layer now lives in ``aegis.governance``.

Budget/rate enforcement, the usage ledger, the nearest-binding limit resolution, the
last-platform-admin lockout, and the admin rollups (tenants / users / budgets / usage)
moved to the standalone, host-agnostic ``aegis.governance.enforcement`` (see ``/aegis``).

This is the **strangler shim**: at import time it injects this app's session factory (so
``aegis.governance`` opens its own short-lived sessions against this deployment's engine)
plus an RLS ``set_tenant_scope`` binder, then re-exports the public surface so every
existing call site (``app.api.routes`` admin handlers, the ``app.core.llm`` governance
hook, tests) keeps working unchanged.

``set_tenant_scope`` is re-exported as a module-level name and the injected binder
late-binds to it, so the existing H1 test seam (``monkeypatch.setattr(app.data.governance,
"set_tenant_scope", spy)``) still observes every governed write's tenant scope.
"""

from __future__ import annotations

from aegis.governance import configure_governance
from aegis.governance.enforcement import (
    CrossTenantBudgetError,
    DuplicateTenantError,
    DuplicateUserError,
    LastPlatformAdminError,
    UserCapAboveTenantCapError,
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
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.session import get_sessionmaker, set_tenant_scope

__all__ = [
    "CrossTenantBudgetError",
    "DuplicateTenantError",
    "DuplicateUserError",
    "LastPlatformAdminError",
    "UserCapAboveTenantCapError",
    "create_tenant",
    "create_user",
    "effective_limits",
    "enforce_governance",
    "list_budgets",
    "list_tenants",
    "list_users",
    "record_usage",
    "set_tenant_scope",
    "update_user_role",
    "upsert_budget",
    "usage_rollup",
    "user_tenant_id",
]


async def _scope(session: AsyncSession, tenant_id: int | None) -> None:
    """Late-binding RLS scope binder injected into ``aegis.governance``.

    It resolves ``set_tenant_scope`` from *this module's* globals on every call, so a
    test that monkeypatches ``app.data.governance.set_tenant_scope`` still intercepts the
    scope applied inside the governed data-layer calls (the H1 seam).
    """
    await set_tenant_scope(session, tenant_id)


# Wire the injected session factory + RLS binder once, at import time — every governed
# read/write in ``aegis.governance`` then runs against this deployment's engine with the
# per-request Postgres RLS scope applied.
configure_governance(
    session_factory=lambda: get_sessionmaker()(),
    set_tenant_scope=_scope,
)
