"""Postgres Row-Level Security — per-tenant isolation for the governed tables.

Two pieces, both **Postgres-only** (a clean no-op on SQLite, so the unit tests run
without a database):

- :func:`set_tenant_scope` — bind ``app.tenant_id`` for a session's connection so the
  bootstrapped RLS policies engage for the request.
- :func:`bootstrap_rls` — enable **and force** RLS, then install the ``tenant_isolation``
  policy on the tenant-scoped tables (``users`` / ``usage_ledger`` / ``approvals``). The
  policy restricts every request that binds a tenant scope; a request that binds none
  (the login lookup, the platform-admin listings) is not restricted — the reasoning and
  the follow-up that would close it are documented on
  :data:`_TENANT_ISOLATION_PREDICATE`.

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

    RLS is **Postgres-only**: this calls ``set_config('app.tenant_id', <id>, true)`` on
    PostgreSQL, writing the **empty string** when the request is unscoped; on SQLite (the
    test database) it does nothing, since RLS and session GUCs are Postgres-only. See the
    numbered comment in the body for why ``set_config`` rather than ``SET``/``RESET``, and
    note that the empty string is not inert: it makes the policy predicate's ``substring``
    yield NULL, which is the deliberate fail-open branch documented on
    :data:`_TENANT_ISOLATION_PREDICATE`.

    Args:
        session: The async session whose connection to pin.
        tenant_id: The tenant to scope to, or ``None`` to clear the scope.
    """
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # ``set_config(name, value, is_local)`` rather than ``SET``/``RESET``, for two
    # independent reasons:
    #
    # 1. CORRECTNESS. ``SET app.tenant_id = :tid`` is not executable: Postgres' ``SET``
    #    takes a literal, so sending it with a bind parameter over the extended query
    #    protocol raises ``PostgresSyntaxError: syntax error at or near "$1"``. Every
    #    tenant-scoped call path on Postgres therefore failed. It went unnoticed because
    #    the suite runs SQLite, which returns at the dialect check three lines above.
    #    ``set_config`` is a normal function call, so it parameterises correctly — and
    #    parameterising matters: the value must never be interpolated into SQL.
    #
    # 2. ISOLATION. ``is_local=true`` scopes the GUC to the current transaction, so it
    #    is discarded on commit/rollback. Session-level ``SET`` persists for the life of
    #    the *connection*, which a pool then hands to the next request — leaking one
    #    tenant's scope into another tenant's query.
    if tenant_id is None:
        await session.execute(text("SELECT set_config('app.tenant_id', '', true)"))
    else:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )


#: The row-visibility predicate installed as the ``tenant_isolation`` policy.
#:
#: ``substring(<guc> from '^[0-9]+$')`` yields the bound tenant id, or SQL NULL when
#: the GUC is unset, empty, or anything other than digits. That shape is chosen so the
#: expression can never raise: a bare ``''::int`` cast would error, and Postgres gives
#: no evaluation-order guarantee that would let an ``OR`` guard protect it.
#:
#: Semantics:
#:   * a **numeric** scope is bound → a row is visible only when ``tenant_id`` equals
#:     it. This is the isolation the policy exists for, and it now genuinely applies
#:     (see the FORCE note in :func:`bootstrap_rls`).
#:   * **no** numeric scope is bound (unset GUC, or the empty string that
#:     :func:`set_tenant_scope` writes for an unscoped/platform-admin request) → the
#:     policy does not restrict.
#:
#: That second branch is a deliberate, documented choice, not an oversight. The host's
#: authentication path reads ``users`` by username *before* any tenant is known, and
#: the platform-admin surfaces list across every tenant; under a fail-closed unset
#: branch, FORCE would make both return zero rows — login included. Closing it
#: requires the host to bind a scope on those paths, which is a change outside this
#: module. Note this is strictly *more* enforcement than before, not less: without
#: FORCE the policy was inert for the owning role in every case.
_TENANT_ISOLATION_PREDICATE = (
    "(substring(current_setting('app.tenant_id', true) from '^[0-9]+$') IS NULL "
    "OR tenant_id = substring(current_setting('app.tenant_id', true) "
    "from '^[0-9]+$')::int)"
)


async def bootstrap_rls(engine: AsyncEngine) -> None:
    """Enable + **force** Row-Level Security and install the per-tenant policy.

    Postgres-only and idempotent. Each tenant-scoped table gets:

    1. ``ENABLE ROW LEVEL SECURITY`` — turns policies on for non-owning roles.
    2. ``FORCE ROW LEVEL SECURITY`` — **the load-bearing statement**. Postgres exempts
       a table's *owner* from its own RLS policies unless FORCE is issued, and this
       application connects with the same role that ran ``create_all`` (see
       ``app.data.session.bootstrap``: one engine creates the tables and is then the
       engine every request uses). Without FORCE the ``tenant_isolation`` policy was
       therefore decorative — enabled, visible in ``pg_policies``, and enforced against
       nobody.
    3. The ``tenant_isolation`` policy itself
       (:data:`_TENANT_ISOLATION_PREDICATE`), matching a row's ``tenant_id`` against
       the ``app.tenant_id`` GUC bound per request by :func:`set_tenant_scope`.

    The policy is created without an explicit ``WITH CHECK``, so Postgres reuses the
    ``USING`` predicate for writes: under a bound tenant scope an INSERT/UPDATE that
    would stamp a *different* tenant is rejected by the database, not merely hidden.

    A no-op on other dialects (SQLite has neither RLS nor session GUCs).

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
            # Without this the owner — i.e. the application's own role — bypasses
            # every policy below.
            await conn.execute(
                text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            )
            await conn.execute(text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
            await conn.execute(
                text(
                    f"CREATE POLICY tenant_isolation ON {table} USING "
                    f"{_TENANT_ISOLATION_PREDICATE}"
                )
            )
