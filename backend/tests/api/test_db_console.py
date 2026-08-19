"""The database console, proven against a real cluster (§7.9).

This is the highest-risk surface in the product: everything else in Phase 7 exposes a
*control*, and this exposes the *data layer*. So the tests here are not a coverage exercise
— each one is a mutation somebody could plausibly introduce, run against a real PostgreSQL
over a real ``NOSUPERUSER NOBYPASSRLS`` role, with a non-vacuity check beside it wherever a
passing assertion could otherwise mean "there was nothing to find".

The four that carry the surface:

* **a write slips through** — every write form, including one attempted after the role
  turns its own ``default_transaction_read_only`` off, because §7.9 finding 4 showed that
  setting is not the boundary. The privilege is.
* **a query escapes its tenant** — a browse bound to one tenant, with the other tenant's
  rows proven present over the owner connection, so an empty result cannot pass for
  isolation.
* **an unbound scope returns everything** — the control case. The same connection, the
  same table: a naive ``SELECT`` returns every tenant's rows (because Aegis's own RLS
  predicate is fail-*open* on an unset scope, by design) while the console's generated
  statement returns none. If those two ever agree, the console's predicate has stopped
  working and this test is the only thing that would say so.
* **the read-only claim is asserted rather than measured** — the runner is pointed at the
  application's serving connection, which holds ``INSERT``/``UPDATE``/``DELETE``, and must
  refuse to run anything at all.
"""

from __future__ import annotations

import asyncio
import secrets
import uuid
from dataclasses import dataclass

import pytest
import pytest_asyncio
from aegis.dbadmin import (
    INSPECTIONS,
    ReadOnlyRunner,
    UnsafeRoleError,
    binding_for,
    browse_query,
    provisioning_statements,
    table_named,
)
from aegis.retrieval.types import ALL_TENANTS
from sqlalchemy import make_url, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

from app.api import routes_db


@dataclass(frozen=True, slots=True)
class ConsoleRole:
    """The provisioned read-only role and the DSN that reaches it."""

    role: str
    dsn: str


async def _provision(owner_dsn: str, role: str, password: str) -> None:
    """Run the shipped provisioning DDL — the same statements an operator runs.

    Deliberately :func:`aegis.dbadmin.provisioning_statements` rather than a test-local
    imitation: the grants under test have to be the real ones, or this whole module proves
    something no deployment has.
    """
    owner = create_async_engine(owner_dsn)
    try:
        async with owner.begin() as conn:
            for statement in provisioning_statements(role, password=password):
                await conn.execute(text(statement))
    finally:
        await owner.dispose()


async def _deprovision(owner_dsn: str, admin_dsn: str, role: str) -> None:
    """Drop the role, and everything recorded against it, leaving no cluster litter."""
    owner = create_async_engine(owner_dsn, isolation_level="AUTOCOMMIT")
    try:
        async with owner.connect() as conn:
            await conn.execute(text(f'DROP OWNED BY "{role}"'))
    finally:
        await owner.dispose()
    admin = create_async_engine(admin_dsn, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
    finally:
        await admin.dispose()


@pytest.fixture(scope="module")
def console_role(postgres_database):
    """Provision the console's read-only role once for this module.

    Synchronous, driving ``asyncio.run`` itself, for the reason ``postgres_database`` is:
    each async test then builds its engines inside its **own** event loop, and no pooled
    asyncpg connection is ever handed across loops.
    """
    from tests import pgsupport

    role = f"aegis_console_{uuid.uuid4().hex[:10]}"
    password = secrets.token_hex(16)
    owner_dsn = postgres_database.scratch.owner_dsn
    asyncio.run(_provision(owner_dsn, role, password))
    dsn = (
        make_url(owner_dsn)
        .set(username=role, password=password)
        .render_as_string(hide_password=False)
    )
    try:
        yield ConsoleRole(role=role, dsn=dsn)
    finally:
        asyncio.run(_deprovision(owner_dsn, pgsupport.admin_dsn(), role))


@pytest_asyncio.fixture
async def console_engine(console_role, db):
    """An engine connected as the console's read-only role, built in this test's loop."""
    engine = create_async_engine(console_role.dsn)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def two_tenants(db):
    """Seed two tenants with ledger rows each, over the **owner** connection.

    The owner bypasses RLS, which is exactly what a seed needs and exactly what an
    isolation assertion must never use.
    """
    from app.data.session import get_admin_engine

    engine = get_admin_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenants (id, name, status) "
                "VALUES (901, 'alpha', 'ACTIVE'), (902, 'beta', 'ACTIVE')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO users (id, username, role, tenant_id, password_hash, is_active) "
                "VALUES (9011, 'alpha-user', 'CLIENT', 901, 'HASH-ALPHA', true), "
                "       (9021, 'beta-user', 'CLIENT', 902, 'HASH-BETA', true)"
            )
        )
        for tenant in (901, 902):
            for _ in range(4):
                await conn.execute(
                    text(
                        "INSERT INTO usage_ledger "
                        "(tenant_id, model, prompt_tokens, completion_tokens, cost_usd) "
                        "VALUES (:t, 'gpt-test', 10, 5, 0.01)"
                    ),
                    {"t": tenant},
                )
    return (901, 902)


@pytest_asyncio.fixture
async def console_on(console_role, db):
    """Switch the console on for one test, pointed at the read-only role."""
    from app.config import get_settings

    settings = get_settings()
    restore = (settings.db_console_enabled, settings.db_console_dsn)
    settings.db_console_enabled = True
    settings.db_console_dsn = console_role.dsn
    routes_db.reset_console_engine()
    try:
        yield
    finally:
        settings.db_console_enabled, settings.db_console_dsn = restore
        engine = routes_db._engine
        routes_db.reset_console_engine()
        if engine is not None:
            await engine.dispose()


# ─────────────────────────────────────────────────────────────────────────────
# 1. A write cannot slip through
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO tenants (name, status) VALUES ('forged', 'ACTIVE')",
        "UPDATE users SET username = 'forged' WHERE id = 9011",
        "DELETE FROM usage_ledger",
        "TRUNCATE TABLE usage_ledger",
        "CREATE TABLE console_should_not_be_able_to (i int)",
        "DROP TABLE usage_ledger",
        "ALTER TABLE usage_ledger DISABLE ROW LEVEL SECURITY",
    ],
    ids=["insert", "update", "delete", "truncate", "create", "drop", "disable-rls"],
)
async def test_the_console_role_cannot_write(console_engine, two_tenants, statement):
    """Every write form is refused by the *privilege*, over the console's real connection.

    The mutation this catches is the one that matters most on this page: somebody widens
    the role's grants, or points ``AEGIS_DB_CONSOLE_DSN`` at ``aegis_app``, and every read
    keeps working while the page silently becomes a write surface.
    """
    async with console_engine.connect() as conn:
        with pytest.raises(SQLAlchemyError):
            await conn.execute(text(statement))
        await conn.rollback()


async def test_a_write_is_still_refused_after_the_role_disables_its_own_read_only_setting(
    console_engine, two_tenants
):
    """§7.9 finding 4, re-verified: the setting is a guard rail, the grant is the boundary.

    ``default_transaction_read_only`` is user-settable. If the console's read-only claim
    rested on it, this sequence would write a row.
    """
    async with console_engine.connect() as conn:
        await conn.execute(text("SET default_transaction_read_only = off"))
        with pytest.raises(SQLAlchemyError):
            await conn.execute(
                text("INSERT INTO tenants (name, status) VALUES ('forged', 'ACTIVE')")
            )
        await conn.rollback()
    # Non-vacuity: the same statement succeeds on the owner connection, so the refusal
    # above is about the role and not about the statement being malformed.
    from app.data.session import get_admin_engine

    async with get_admin_engine().begin() as conn:
        await conn.execute(
            text("INSERT INTO tenants (name, status) VALUES ('owner-can', 'ACTIVE')")
        )


async def test_multiple_commands_cannot_be_sent_in_one_statement(console_engine, db):
    """§7.9 finding 2, re-verified: *how* the query is sent is itself a control.

    The simple protocol would run all three commands and report only the first one's status
    tag. Every statement this package sends carries a bind parameter, which forces the
    extended protocol, which refuses this outright.
    """
    async with console_engine.connect() as conn:
        with pytest.raises(SQLAlchemyError, match="multiple commands"):
            await conn.execute(
                text("SELECT 1 WHERE :p; SET default_transaction_read_only = off"),
                {"p": True},
            )
        await conn.rollback()


async def test_the_runner_refuses_a_connection_that_can_write(db):
    """Point the console at the application's serving role and it must serve nothing.

    The serving role holds INSERT/UPDATE/DELETE. This is the check that turns "we
    configured the right DSN" into something the process verifies for itself on every
    request, over the very connection the queries would use.
    """
    from app.data.session import get_engine

    runner = ReadOnlyRunner(engine=get_engine())
    posture = await runner.posture()
    assert posture.writable_tables, "the serving role should hold write grants"
    assert not posture.is_safe
    with pytest.raises(UnsafeRoleError, match="not read-only"):
        await runner.schema(binding_for(ALL_TENANTS))


# ─────────────────────────────────────────────────────────────────────────────
# 2. A query cannot escape its tenant — including with nothing bound
# ─────────────────────────────────────────────────────────────────────────────


async def test_a_browse_bound_to_one_tenant_sees_only_that_tenant(
    console_engine, two_tenants
):
    """The isolation claim, with the non-vacuity check that makes it mean something."""
    alpha, beta = two_tenants
    runner = ReadOnlyRunner(engine=console_engine)
    tables = await runner.schema(binding_for(ALL_TENANTS))
    ledger = table_named(tables, "usage_ledger")

    scoped = await runner.run(
        browse_query(ledger, platform_wide=False, limit=100), binding_for(alpha), row_limit=100
    )
    tenant_column = list(scoped.columns).index("tenant_id")
    assert scoped.row_count > 0
    assert {row[tenant_column] for row in scoped.rows} == {alpha}

    # Non-vacuity: beta's rows exist and the platform-wide authority does see them, so the
    # assertion above is isolation and not an empty table.
    everything = await runner.run(
        browse_query(ledger, platform_wide=True, limit=100),
        binding_for(ALL_TENANTS),
        row_limit=100,
    )
    assert {alpha, beta} <= {row[tenant_column] for row in everything.rows}


async def test_an_unbound_scope_returns_nothing_where_rls_alone_returns_everything(
    console_engine, two_tenants
):
    """**The control case.** The trap this project has already hit twice, closed.

    Aegis's ``tenant_isolation`` predicate is ``substring(current_setting('app.tenant_id',
    true) …) IS NULL OR tenant_id = …`` — true when the GUC is unset, so it fails *open*.
    On the very same connection, with nothing bound:

    * a naive ``SELECT`` over ``usage_ledger`` returns **every tenant's rows**, which is
      what a database page built the obvious way would show; and
    * the console's generated statement returns **none**, because its predicate has no
      null-tolerant branch.

    Both halves are asserted. Dropping the first would let this test pass against a table
    that happened to be empty, which is the failure mode it exists to prevent.
    """
    runner = ReadOnlyRunner(engine=console_engine)
    tables = await runner.schema(binding_for(ALL_TENANTS))
    ledger = table_named(tables, "usage_ledger")
    query = browse_query(ledger, platform_wide=False, limit=100)

    async with console_engine.connect() as conn:
        # Nothing bound: no set_config, no GUC, exactly the state a forgotten binding
        # leaves a pooled connection in.
        rls_only = (
            await conn.execute(text("SELECT count(*) FROM usage_ledger"))
        ).scalar_one()
        guarded = len(list(await conn.execute(text(query.sql), query.params)))
        await conn.rollback()

    assert rls_only >= 8, "the RLS policy alone is fail-open, and this proves the rows exist"
    assert guarded == 0, (
        "the console's generated statement returned rows with no scope bound; its tenant "
        "predicate has become null-tolerant and the page now reads across every tenant"
    )


async def test_the_tenant_selector_narrows_the_same_read(console_engine, two_tenants):
    """The impersonation control: bind a tenant, re-run the read, watch rows disappear."""
    alpha, beta = two_tenants
    runner = ReadOnlyRunner(engine=console_engine)
    tables = await runner.schema(binding_for(ALL_TENANTS))
    ledger = table_named(tables, "usage_ledger")
    query = browse_query(ledger, platform_wide=False, limit=100)

    from_alpha = await runner.run(query, binding_for(alpha), row_limit=100)
    from_beta = await runner.run(query, binding_for(beta), row_limit=100)
    tenant_column = list(from_alpha.columns).index("tenant_id")
    assert {row[tenant_column] for row in from_alpha.rows} == {alpha}
    assert {row[tenant_column] for row in from_beta.rows} == {beta}


async def test_the_password_hash_is_withheld_from_the_catalog_itself(
    console_engine, two_tenants
):
    """§7.9 finding 5: the permission model *is* the browser's source of truth.

    ``users.password_hash`` is not withheld by a denylist in application code — it is
    withheld by a column-level grant, so ``information_schema`` stops listing it and there
    is nothing left to drift.
    """
    runner = ReadOnlyRunner(engine=console_engine)
    tables = await runner.schema(binding_for(ALL_TENANTS))
    users = table_named(tables, "users")
    assert "password_hash" not in {column.name for column in users.columns}
    assert "password_hash" in users.withheld_columns
    assert "username" in {column.name for column in users.columns}

    result = await runner.run(
        browse_query(users, platform_wide=True, limit=10),
        binding_for(ALL_TENANTS),
        row_limit=10,
    )
    assert "password_hash" not in result.columns
    async with console_engine.connect() as conn:
        with pytest.raises(SQLAlchemyError):
            await conn.execute(text("SELECT password_hash FROM users WHERE :p"), {"p": True})
        await conn.rollback()


# ─────────────────────────────────────────────────────────────────────────────
# 3. The closed set really runs against the real schema
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("inspection", INSPECTIONS, ids=[item.id for item in INSPECTIONS])
async def test_every_inspection_executes_against_the_live_schema(
    console_engine, two_tenants, inspection
):
    """An inspection that names a column the schema no longer has is a broken page.

    Running each one against the real database is the only way to know; a unit test over
    the catalogue would keep passing after a column was renamed underneath it.
    """
    runner = ReadOnlyRunner(engine=console_engine)
    binding = binding_for(ALL_TENANTS)
    tables = await runner.schema(binding)
    table = table_named(tables, inspection.source)
    result = await runner.run(inspection.build(table, limit=5), binding, row_limit=5)
    assert result.columns


# ─────────────────────────────────────────────────────────────────────────────
# 4. The HTTP surface: who may reach it, what it records, and what it refuses
# ─────────────────────────────────────────────────────────────────────────────


async def test_the_console_is_off_unless_a_deployment_turns_it_on(client, admin_headers):
    """Default off, and the refusal names the variable that turns it on."""
    routes_db.reset_console_engine()
    resp = await client.get("/database/overview", headers=admin_headers)
    assert resp.status_code == 503
    assert "AEGIS_DB_CONSOLE_ENABLED" in resp.json()["detail"]


@pytest.mark.parametrize("username", ["devops", "aiteam", "client"])
async def test_only_a_platform_admin_reaches_the_console(
    client, platform_principals, console_on, username
):
    """``require_platform_admin``, never ``require_admin`` — §7.16 row 4.

    Sent as a request a UI would never send: these portals carry no database section at
    all, so the only way to make this call is by hand.
    """
    from tests.conftest import login_as

    headers = await login_as(client, username)
    for method, path, body in (
        ("get", "/database/overview", None),
        ("post", "/database/browse", {"table": "usage_ledger"}),
        ("post", "/database/inspections/spend_by_tenant", {}),
    ):
        call = getattr(client, method)
        resp = await (
            call(path, headers=headers)
            if body is None
            else call(path, headers=headers, json=body)
        )
        assert resp.status_code == 403, f"{method.upper()} {path} admitted {username}"


async def test_the_overview_reports_the_connection_it_measured(
    client, admin_headers, console_on, two_tenants
):
    """The page's central claim is a measurement, and the measurement is on the wire."""
    resp = await client.get("/database/overview", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["posture"]["readOnly"] is True
    assert body["posture"]["writableTables"] == []
    assert body["posture"]["refusal"] is None
    assert body["freeFormSql"] is False
    assert "row-level security" in body["freeFormReason"]
    names = {table["name"] for table in body["tables"]}
    assert {"usage_ledger", "users", "tenants"} <= names
    users = next(table for table in body["tables"] if table["name"] == "users")
    assert users["withheldColumns"] == ["password_hash"]
    assert {inspection["id"] for inspection in body["inspections"]} == {
        item.id for item in INSPECTIONS
    }
    assert 901 in {tenant["id"] for tenant in body["tenants"]}


async def test_every_read_writes_an_audit_row_before_and_after_it(
    client, admin_headers, console_on, two_tenants, db
):
    """*Who looked at what* is the whole compliance story of this surface.

    Two rows, correlated by ``query_id``: the first written **before** the statement runs,
    so a read that never returns still leaves a trace; the second carrying what it actually
    did.
    """
    resp = await client.post(
        "/database/browse",
        headers=admin_headers,
        json={"table": "usage_ledger", "limit": 3, "tenantId": 901},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scope"] == "tenant 901"
    assert body["tenantFiltered"] is True

    from app.data.session import get_admin_engine

    async with get_admin_engine().connect() as conn:
        rows = list(
            await conn.execute(
                text(
                    "SELECT action, payload FROM audit_log "
                    "WHERE action LIKE 'db.query.%' ORDER BY id"
                )
            )
        )
    actions = [row[0] for row in rows]
    assert actions == ["db.query.execute", "db.query.result"]
    before, after = rows[0][1], rows[1][1]
    assert before["query_id"] == after["query_id"] == body["queryId"]
    assert before["scope"] == "tenant 901"
    assert before["via"] == "browser"
    assert before["tenant_filtered"] is True
    assert "current_setting" in before["sql"]
    assert after["verdict"] in {"completed", "truncated"}
    assert after["rows"] == body["rowCount"]


async def test_a_refusal_explains_itself(client, admin_headers, console_on, two_tenants):
    """Every refusal is a sentence the screen can render, not a status code."""
    unknown_table = await client.post(
        "/database/browse", headers=admin_headers, json={"table": "not_a_table"}
    )
    assert unknown_table.status_code == 400
    assert "no readable table" in unknown_table.json()["detail"]

    withheld = await client.post(
        "/database/browse",
        headers=admin_headers,
        json={"table": "users", "orderBy": "password_hash"},
    )
    assert withheld.status_code == 400
    assert "no readable column" in withheld.json()["detail"]

    unknown_inspection = await client.post(
        "/database/inspections/select_star", headers=admin_headers, json={}
    )
    assert unknown_inspection.status_code == 400
    assert "closed set" in unknown_inspection.json()["detail"]


async def test_the_console_is_rate_limited(client, admin_headers, console_on, two_tenants):
    """The one rate limit in ``backend/src``, on the page that most needs one."""
    routes_db._recent.clear()
    last = None
    for _ in range(routes_db.RATE_LIMIT_PER_MINUTE + 1):
        last = await client.post(
            "/database/inspections/spend_by_tenant", headers=admin_headers, json={"limit": 1}
        )
    assert last is not None
    assert last.status_code == 429
    assert "limit" in last.json()["detail"]
    routes_db._recent.clear()
