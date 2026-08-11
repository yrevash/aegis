"""Postgres Row-Level Security — per-tenant isolation for the governed tables.

Two pieces, both **Postgres-only** (a clean no-op on SQLite, so the unit tests run
without a database):

- :func:`set_tenant_scope` — bind ``app.tenant_id`` for a session's connection so the
  bootstrapped RLS policies engage for the request.
- :func:`bootstrap_rls` — enable RLS + install the ``tenant_isolation`` policy on the
  tenant-scoped tables (``users`` / ``usage_ledger`` / ``approvals``), failing **closed**
  when the GUC is unset.

App-level ``WHERE tenant_id = :ctx`` scoping (in :mod:`aegis.governance.enforcement`) is
the belt-and-suspenders layer over these DB-enforced policies — and the only layer on
SQLite.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

__all__ = ["bootstrap_rls", "set_tenant_scope"]

# Tenant-scoped tables that carry a ``tenant_id`` column and therefore get a
# Postgres Row-Level Security policy filtering on the ``app.tenant_id`` GUC. App-level
# ``WHERE tenant_id = :ctx`` scoping is the belt-and-suspenders layer over these
# DB-enforced policies. ``approvals`` lives host-side (agent HITL) but is governed by
# the same per-tenant policy, so it is bootstrapped here alongside the governance tables.
_RLS_TABLES = ("users", "usage_ledger", "approvals")


async def set_tenant_scope(session: AsyncSession, tenant_id: int | None) -> None:
    """Bind ``app.tenant_id`` for the session's connection so RLS policies apply.

    Applied inside the governed data-layer calls — the usage ledger, budget reads,
    user/usage listings, and the approvals inbox — so the bootstrapped per-tenant RLS
    policies engage on Postgres for every governed request. The app-level
    ``WHERE tenant_id = :ctx`` scoping remains the belt-and-suspenders layer over these
    DB-enforced policies (and the only layer on SQLite).

    RLS is **Postgres-only**: this emits ``SET app.tenant_id = '<id>'`` on PostgreSQL
    (a no-op ``RESET`` when the request is unscoped); on SQLite (the test database) it
    does nothing, since RLS and session GUCs are Postgres-only.

    Args:
        session: The async session whose connection to pin.
        tenant_id: The tenant to scope to, or ``None`` to clear the scope.
    """
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return
    if tenant_id is None:
        await session.execute(text("RESET app.tenant_id"))
    else:
        await session.execute(
            text("SET app.tenant_id = :tid"), {"tid": str(tenant_id)}
        )


async def bootstrap_rls(engine: AsyncEngine) -> None:
    """Enable Row-Level Security + a per-tenant policy on tenant-scoped tables.

    Postgres-only and idempotent: each table gets ``ENABLE ROW LEVEL SECURITY`` and a
    policy that admits a row only when its ``tenant_id`` matches the
    ``current_setting('app.tenant_id')`` GUC set per request by :func:`set_tenant_scope`
    (an unset/empty GUC admits nothing, failing closed). A no-op on other dialects.

    Args:
        engine: The async engine to configure.
    """
    if engine.dialect.name != "postgresql":
        return
    async with engine.begin() as conn:
        for table in _RLS_TABLES:
            await conn.execute(
                text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            )
            await conn.execute(text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
            await conn.execute(
                text(
                    f"CREATE POLICY tenant_isolation ON {table} USING "
                    "(tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
                    "::int)"
                )
            )
