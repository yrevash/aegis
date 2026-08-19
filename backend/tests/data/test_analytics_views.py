"""The Superset pipeline, over real PostgreSQL: the views narrow, and ownership is why.

Superset does not connect through Aegis. It opens its **own** pooled Postgres connection
as its **own** role, carrying none of Aegis's request context — so "does a tenant's
row-level-security policy still apply to a query Superset runs?" is decided entirely by
two things, and both are exercised here against a real cluster:

0. **the view's own predicate must fail closed.** Aegis's global ``tenant_isolation``
   policy is null-tolerant by design — an unset ``app.tenant_id`` does not restrict —
   which is right for Aegis, whose request path always binds it, and wrong for a
   connection Aegis did not open. ``DB_CONNECTION_MUTATOR`` is exactly the kind of hook
   that stops firing quietly, and a layer that silently becomes a no-op is worse than no
   layer, because the runbook still claims it. So the views carry their own
   *non*-null-tolerant predicate and an unmutated connection sees **zero** rows;
1. the role Superset connects as must be ``NOSUPERUSER NOBYPASSRLS`` — PostgreSQL skips
   row security *entirely* for a superuser or a ``BYPASSRLS`` role;
2. **the view must be owned by that role.** A view executes its query with the
   privileges of its *owner*. A view owned by the table owner and read by Superset
   reaches the base table as the owner — and where that owner can bypass RLS, the view
   is a hole straight through the policy while looking exactly like a safe projection.
   Same shape as the partition bug this project already paid for: *a parent's policy
   does not protect what is reached by another name.*

Claims 0 and 2 are the ones worth a test, and each has its mutation proof beside it:
:func:`test_an_unmutated_connection_sees_nothing_where_the_old_predicate_leaked` runs the
fail-closed predicate and Aegis's null-tolerant one side by side with the GUC unset — one
returns nothing, the other returns both tenants — and
:func:`test_the_same_view_left_owned_by_the_table_owner_leaks` does the same for the
ownership transfer.

Everything here runs as a **separate role from the application's serving role**, minted
per test module, exactly as a real deployment would: Superset's grants are not Aegis's.
"""

from __future__ import annotations

import secrets
import uuid

import pytest
import pytest_asyncio
from aegis.analytics.provision import (
    ALL_TENANTS_GUC,
    ANALYTICS_VIEWS,
    SOURCE_TABLES,
    provisioning_statements,
    revocation_statements,
)
from aegis.analytics.rls import analytics_connect_options
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.asyncio


async def _run(dsn: str, statements) -> None:
    """Run statements on ``dsn`` in autocommit, one at a time."""
    engine = create_async_engine(dsn, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            for statement in statements:
                await conn.execute(text(statement))
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def superset_role(postgres_database, db):
    """Provision the analytics role + views, and yield the DSN Superset would use.

    Depends on ``db`` so the schema exists and has been truncated for this test, and so
    the ``tenant_isolation`` policies are in force underneath.
    """
    owner_dsn = postgres_database.scratch.owner_dsn
    role = f"aegis_superset_{uuid.uuid4().hex[:10]}"
    password = secrets.token_hex(12)
    dsn = make_url(owner_dsn).set(username=role, password=password).render_as_string(
        hide_password=False
    )
    await _run(owner_dsn, provisioning_statements(role, password=password))
    try:
        yield dsn, role
    finally:
        await _run(owner_dsn, revocation_statements(role))
        await _run(owner_dsn, (f'DROP OWNED BY "{role}"', f'DROP ROLE IF EXISTS "{role}"'))


async def _seed_two_tenants_spend(owner_dsn: str) -> None:
    """One usage-ledger row for tenant 1 and one for tenant 2, written as the owner."""
    await _run(
        owner_dsn,
        (
            "INSERT INTO tenants (id, name, status) VALUES "
            "(1, 'A', 'ACTIVE'), (2, 'B', 'ACTIVE')",
            "INSERT INTO usage_ledger (tenant_id, model, prompt_tokens, "
            "completion_tokens, cost_usd) VALUES "
            "(1, 'model-a', 10, 5, 1.5), (2, 'model-b', 20, 7, 2.5)",
        ),
    )


async def _read_as_superset(dsn: str, sql: str, tenant: int | None, *, all_tenants: bool = False):
    """Read ``sql`` on one connection with the GUCs a mutated connection would carry.

    One connection for every statement on purpose: a GUC is session state, which is
    precisely why ``DB_CONNECTION_MUTATOR`` has to set it on the connection Superset
    opens rather than anywhere in Aegis.

    ``tenant=None, all_tenants=False`` is the important case — it is what an **unmutated**
    connection looks like, and the fail-closed predicate must return nothing for it.
    """
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as conn:
            if tenant is not None:
                await conn.execute(
                    text("SELECT set_config('app.tenant_id', :tid, false)"),
                    {"tid": str(tenant)},
                )
            if all_tenants:
                await conn.execute(
                    text(f"SELECT set_config('{ALL_TENANTS_GUC}', 'on', false)")
                )
            return (await conn.execute(text(sql))).scalars().all()
    finally:
        await engine.dispose()


async def test_the_role_superset_connects_as_cannot_bypass_row_security(superset_role):
    """The precondition for every other claim in this file."""
    dsn, role = superset_role
    rows = await _read_as_superset(
        dsn,
        "SELECT rolsuper::text || ',' || rolbypassrls::text FROM pg_roles "
        f"WHERE rolname = '{role}'",
        None,
    )
    assert rows == ["false,false"], rows


async def test_every_view_carries_the_tenant_column_the_rls_clause_filters_on(superset_role):
    """A view with no ``tenant_id`` cannot be made safe by any guest token, so the
    catalogue may not contain one."""
    dsn, _role = superset_role
    for view in ANALYTICS_VIEWS:
        columns = await _read_as_superset(
            dsn,
            "SELECT column_name FROM information_schema.columns "
            f"WHERE table_name = '{view.name}'",
            None,
        )
        assert "tenant_id" in columns, f"{view.name} has no tenant_id: {columns}"


async def test_a_view_owned_by_the_read_only_role_is_narrowed_by_postgres_rls(
    postgres_database, superset_role
):
    """Superset's own connection, its own GUC, its own answer — one tenant's rows.

    Both mechanisms are in force here at once: the base table's ``tenant_isolation``
    policy (reached as a role subject to it, because of the ownership transfer) and the
    view's own fail-closed predicate. This asserts the working case; the two tests below
    take each one away in turn.
    """
    dsn, _role = superset_role
    await _seed_two_tenants_spend(postgres_database.scratch.owner_dsn)
    rows = await _read_as_superset(dsn, "SELECT tenant_id FROM analytics_spend_daily", 1)
    assert rows == [1], f"the view returned {rows}; tenant 2's spend is visible"


async def test_the_same_view_left_owned_by_the_table_owner_leaks(
    postgres_database, superset_role
):
    """The mutation proof for ``ALTER VIEW … OWNER TO``.

    Byte-for-byte the same reader and the same bound tenant — and it returns both
    tenants, because it runs the base-table access as its owner. Delete that one line
    from the provisioning DDL and the six real views become this.
    """
    dsn, role = superset_role
    owner_dsn = postgres_database.scratch.owner_dsn
    await _seed_two_tenants_spend(owner_dsn)
    await _run(
        owner_dsn,
        (
            "CREATE OR REPLACE VIEW analytics_control_leak AS "
            "SELECT tenant_id FROM usage_ledger",
            f'GRANT SELECT ON TABLE analytics_control_leak TO "{role}"',
        ),
    )
    try:
        leaked = await _read_as_superset(dsn, "SELECT tenant_id FROM analytics_control_leak", 1)
        narrowed = await _read_as_superset(dsn, "SELECT tenant_id FROM analytics_spend_daily", 1)
    finally:
        await _run(owner_dsn, ("DROP VIEW IF EXISTS analytics_control_leak",))

    assert narrowed == [1], "the provisioned view must already be narrowed"
    assert sorted(leaked) == [1, 2], (
        "the control view was expected to leak; if it did not, the owner of these tables "
        "is itself subject to RLS on this cluster, and the ownership transfer is belt and "
        "braces rather than the load-bearing step"
    )


async def test_an_unmutated_connection_sees_nothing_where_the_old_predicate_leaked(
    postgres_database, superset_role
):
    """The mutation proof for the fail-closed predicate, and the reason it exists.

    ``control_null_tolerant`` below is Aegis's own ``tenant_isolation`` predicate,
    verbatim. It is correct for Aegis — the request path always binds the GUC, and the
    login lookup and every platform read happen outside a tenant scope — and it is a hole
    the moment a connection Aegis did not open reads through it, because *unset* means
    *unrestricted*.

    Same reader, same unset GUC, two predicates: the shipped view returns nothing and the
    null-tolerant one returns both tenants. That is the difference between a Superset
    whose ``DB_CONNECTION_MUTATOR`` quietly stopped firing showing an empty dashboard,
    and one showing every tenant's spend to whoever is looking.
    """
    dsn, role = superset_role
    owner_dsn = postgres_database.scratch.owner_dsn
    await _seed_two_tenants_spend(owner_dsn)
    await _run(
        owner_dsn,
        (
            "CREATE OR REPLACE VIEW control_null_tolerant AS SELECT tenant_id FROM "
            "usage_ledger u WHERE (substring(current_setting('app.tenant_id', true) "
            "from '^[0-9]+$') IS NULL OR u.tenant_id = "
            "substring(current_setting('app.tenant_id', true) from '^[0-9]+$')::int)",
            f'ALTER VIEW control_null_tolerant OWNER TO "{role}"',
        ),
    )
    try:
        shipped = await _read_as_superset(
            dsn, "SELECT tenant_id FROM analytics_spend_daily", None
        )
        tolerant = await _read_as_superset(
            dsn, "SELECT tenant_id FROM control_null_tolerant", None
        )
    finally:
        await _run(owner_dsn, ("DROP VIEW IF EXISTS control_null_tolerant",))

    assert shipped == [], (
        "an unmutated connection read rows out of a shipped view. The predicate is "
        f"null-tolerant again and the database layer is a no-op: {shipped}"
    )
    assert sorted(tolerant) == [1, 2], (
        "the null-tolerant control was expected to return both tenants; if it did not, "
        "this cluster is narrowing by some other means and the comparison proves nothing"
    )


async def test_a_platform_read_needs_the_sentinel_set_on_purpose(
    postgres_database, superset_role
):
    """Reading across tenants is an opt-out somebody chose, never one a session drifts into.

    The three states, in one test, because it is the boundary between them that matters:
    no GUCs at all is nothing; a tenant id is that tenant; and every tenant needs a GUC
    in its own name that no other code in Aegis writes — in particular it is *not* the
    empty ``app.tenant_id`` that :func:`set_tenant_scope` writes when it resets, which is
    the value a connection returns to rather than one anybody selected.
    """
    dsn, _role = superset_role
    await _seed_two_tenants_spend(postgres_database.scratch.owner_dsn)
    sql = "SELECT tenant_id FROM analytics_spend_daily"

    assert await _read_as_superset(dsn, sql, None) == []
    assert await _read_as_superset(dsn, sql, 1) == [1]
    assert sorted(await _read_as_superset(dsn, sql, None, all_tenants=True)) == [1, 2]

    # The reset value is not the sentinel.
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT set_config('app.tenant_id', '', false)"))
            reset = (await conn.execute(text(sql))).scalars().all()
    finally:
        await engine.dispose()
    assert reset == [], f"an empty app.tenant_id read rows: {reset}"


async def test_the_mutator_body_aegis_ships_produces_those_exact_states(
    postgres_database, superset_role
):
    """The connection options in the runbook are the ones this package computes.

    ``DB_CONNECTION_MUTATOR`` has to be written into ``superset_config.py`` by hand, so
    the risk is that the documented hook and the guest usernames Aegis mints drift apart.
    Driving the real options string through a real connection is what stops that being
    discovered on demo day.
    """
    dsn, _role = superset_role
    await _seed_two_tenants_spend(postgres_database.scratch.owner_dsn)
    sql = "SELECT tenant_id FROM analytics_spend_daily"

    async def read_with(options: str):
        engine = create_async_engine(dsn)
        try:
            async with engine.connect() as conn:
                for setting in options.split("-c ")[1:]:
                    name, value = setting.strip().split("=", 1)
                    await conn.execute(
                        text("SELECT set_config(:n, :v, false)"), {"n": name, "v": value}
                    )
                return (await conn.execute(text(sql))).scalars().all()
        finally:
            await engine.dispose()

    assert await read_with(analytics_connect_options("aegis-tenant-1")) == [1]
    assert sorted(await read_with(analytics_connect_options("aegis-platform"))) == [1, 2]
    # Superset's own service account, and a username format that drifted after an upgrade.
    assert await read_with(analytics_connect_options("admin")) == []
    assert await read_with(analytics_connect_options("aegis_tenant_1")) == []


async def test_the_role_gets_select_on_the_source_tables_and_nothing_else(superset_role):
    """A dashboard is a read, and it is a read of six tables.

    Checked on the **source tables**: the role owns the views (that is the previous
    test's whole point) and an owner holds every privilege on what it owns. What matters
    is its reach into Aegis's own tables — SELECT, and only where a board needs it. No
    ``chat_messages``, no ``memory_*``, no ``documents``: a business dashboard has no
    business reading conversation content.
    """
    dsn, role = superset_role
    granted = await _read_as_superset(
        dsn,
        "SELECT DISTINCT privilege_type FROM information_schema.table_privileges "
        f"WHERE grantee = '{role}' AND table_name IN "
        f"({', '.join(chr(39) + t + chr(39) for t in SOURCE_TABLES)})",
        None,
    )
    assert sorted(granted) == ["SELECT"], granted

    reachable = await _read_as_superset(
        dsn,
        "SELECT DISTINCT table_name FROM information_schema.table_privileges "
        f"WHERE grantee = '{role}' AND table_name IN "
        "('chat_messages', 'memory_message', 'documents', 'chunks')",
        None,
    )
    assert reachable == [], f"the analytics role can read {reachable}"


async def test_the_provisioning_is_idempotent(postgres_database, superset_role):
    """Re-run after every schema change, which is exactly when a non-idempotent script
    bites. The fixture applied it once; applying it again must be a no-op."""
    _dsn, role = superset_role
    await _run(
        postgres_database.scratch.owner_dsn, provisioning_statements(role, password=None)
    )
