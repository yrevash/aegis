"""Live-database proof that tenant isolation is real, not merely declared.

This file exists because of two verified failures of the *previous* evidence:

1. ``aegis/tests/governance/test_rls.py`` asserts the **DDL strings** ``bootstrap_rls``
   emits against a hand-written fake engine. It has never opened a connection, so it
   cannot fail when a policy is absent — which is exactly why ten of the thirteen
   tenant-scoped tables went un-policied without anyone noticing.
2. A naive live test would still prove nothing. **PostgreSQL superusers bypass row
   security entirely** (``FORCE ROW LEVEL SECURITY`` removes the *owner* exemption, not
   the superuser one), and the application has been connecting as ``postgres``. An
   isolation test that reuses the app's DSN therefore passes while every policy is inert.

So the fixtures here are deliberately self-contained and ambient-config-free: they
create their own scratch database **and** their own ``NOSUPERUSER NOBYPASSRLS`` role,
run ``create_all`` + :func:`aegis.governance.rls.bootstrap_rls` as the owner, and then do
every isolation assertion over a connection made **as the unprivileged role**. Nothing
in ``backend/.env`` can make these tests vacuous, and :func:`_role_privileges` is checked
during provisioning so a privileged reader is an error rather than a green run.

What is proved, per registered tenant-scoped table (the list is read from
:data:`aegis.governance.rls._TENANT_SCOPED_TABLES`, so registering a new table extends
this sweep automatically, and forgetting one fails
:func:`test_bootstrap_protects_every_registered_table_in_the_live_schema`):

* both tenants' rows really are in the table (the non-vacuity check — read as the owner,
  who bypasses RLS);
* under tenant A's bound scope the unprivileged role sees **exactly** A's row;
* under tenant A's bound scope, an INSERT stamping tenant B is **rejected** by the
  database (the policy carries no explicit ``WITH CHECK``, so Postgres reuses ``USING``
  for writes) — with a positive control proving the same INSERT succeeds for A, so the
  rejection cannot be an artefact of a malformed statement;
* with **no** scope bound the rows stay visible, which is the deliberate fail-open
  documented on ``_TENANT_ISOLATION_PREDICATE`` — see
  :func:`test_an_unbound_scope_is_deliberately_fail_open_pending_security_definer_login`.

If no PostgreSQL is reachable the database tests skip with a message naming precisely
what was *not* verified; setting ``AEGIS_REQUIRE_PG_TESTS=1`` turns that skip into a
failure so CI can demand the evidence rather than accept a green-looking skip.

The last test needs no database: it drives the real retrieval pipeline twice for one
tenant and asserts the other tenant's distinctive phrase reaches neither the result, its
citations, nor the cache.
"""

from __future__ import annotations

import asyncio
import getpass
import hashlib
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

# Import every module whose declarative models must be registered before ``create_all``,
# exactly as ``app.data.session.bootstrap`` does — the scratch schema has to be the
# schema the application actually runs on, or the sweep would silently skip tables.
import aegis.governance.models  # noqa: F401 - registration side-effect only
import aegis.ops.models  # noqa: F401 - registration side-effect only
import pytest
from aegis.data import AegisBase
from aegis.governance.rls import (
    _POLICY_NAME,
    _TENANT_COLUMN,
    _TENANT_SCOPED_TABLES,
    bootstrap_rls,
    set_tenant_scope,
)
from aegis.retrieval.cache import SemanticCache
from aegis.retrieval.memory import (
    InMemoryKnowledgeBackend,
    InMemoryRedis,
    _default_offline_embed,
)
from aegis.retrieval.pipeline import RetrievalConfig, Retriever
from aegis.retrieval.types import RetrievalScope
from sqlalchemy import Integer, Table, make_url, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, async_sessionmaker, create_async_engine
from sqlalchemy.schema import sort_tables

import app.memory.stores  # noqa: F401 - registration side-effect only
from app.data.models import Base

#: Environment variable naming the **admin** DSN used only to create (and drop) the
#: scratch database and role. Deliberately *not* ``POSTGRES_DSN``: reusing the app's DSN
#: is how an isolation test ends up connecting as a superuser and proving nothing.
_ADMIN_DSN_ENV = "AEGIS_PG_TEST_ADMIN_DSN"

#: Set to ``1`` to turn "no PostgreSQL reachable" from a skip into a hard failure. CI
#: sets it, because a security test that silently skips is indistinguishable from one
#: that passes, and that is its own failure mode.
_REQUIRE_ENV = "AEGIS_REQUIRE_PG_TESTS"

#: The two tenants every assertion below is written against. Values far outside the
#: ranges the rest of the suite seeds, so a stray row from elsewhere cannot be mistaken
#: for one of these.
_TENANT_A = 90101
_TENANT_B = 90202

#: Tenant A's distinctive phrase for the retrieval leg — a string that exists nowhere
#: else in this repository, so finding it in tenant B's result, citations or cache is
#: unambiguous proof of a leak rather than a coincidental substring.
_LEAK_PHRASE = "ZANZIBAR-PARROTFISH-7731"


def _admin_dsn() -> str:
    """Return the DSN used to provision the scratch database and role.

    Defaults to the local cluster as the invoking OS user (the usual Homebrew/`initdb`
    setup) rather than reading any application configuration: the whole point of this
    module is that ambient config cannot influence what it proves.

    Returns:
        A SQLAlchemy asyncpg DSN pointing at a maintenance database.
    """
    configured = os.environ.get(_ADMIN_DSN_ENV)
    if configured:
        return configured
    return f"postgresql+asyncpg://{getpass.getuser()}@localhost:5432/postgres"


def _skip_or_fail(reason: str) -> None:
    """Skip loudly — or fail — when the database evidence could not be gathered.

    A skipped security test reads as green, so the message names exactly what went
    unverified rather than saying "postgres unavailable". Under
    ``AEGIS_REQUIRE_PG_TESTS=1`` the skip becomes a failure so a pipeline can *demand*
    the evidence.

    Args:
        reason: The underlying cause, appended verbatim to the message.

    Raises:
        Failed: Always, via ``pytest.fail``, when the require-flag is set.
        Skipped: Always, via ``pytest.skip``, otherwise.
    """
    message = (
        "Tenant isolation was NOT verified against a live database: no PostgreSQL could "
        f"be provisioned via {_ADMIN_DSN_ENV} ({_admin_dsn()!r}). Unverified: that the "
        f"'{_POLICY_NAME}' policy exists on every table in _TENANT_SCOPED_TABLES, that a "
        "bound tenant scope hides another tenant's rows from a non-superuser role, and "
        f"that a write stamping another tenant is rejected. Cause: {reason}. "
        f"Set {_REQUIRE_ENV}=1 to make this a failure instead of a skip."
    )
    if os.environ.get(_REQUIRE_ENV) == "1":
        pytest.fail(message)
    pytest.skip(message)


# ─────────────────────────────────────────────────────────────────────────────
# Schema introspection — the sweep is driven from the registry + the metadata,
# never from a hand-maintained list, so a newly registered table is covered by
# construction and a missing one is a failure rather than a silent gap.
# ─────────────────────────────────────────────────────────────────────────────


def _all_tables() -> dict[str, Table]:
    """Return every table across both declarative metadatas, keyed by name.

    The host keeps two metadatas (``app.data.models.Base`` for platform tables such as
    ``approvals``, ``aegis.data.AegisBase`` for the governance/memory/ops tables) and
    creates both at bootstrap, so both are needed here to reproduce the live schema.

    Returns:
        A mapping of table name to :class:`sqlalchemy.Table`.
    """
    tables: dict[str, Table] = {}
    for metadata in (Base.metadata, AegisBase.metadata):
        for table in metadata.tables.values():
            tables[table.name] = table
    return tables


def _registered_tables() -> list[Table]:
    """Return the registered tenant-scoped tables this schema actually declares.

    Returns:
        One :class:`sqlalchemy.Table` per name in
        :data:`aegis.governance.rls._TENANT_SCOPED_TABLES` that the metadata knows,
        ordered as the registry declares them.
    """
    tables = _all_tables()
    return [tables[name] for name in _TENANT_SCOPED_TABLES if name in tables]


def _tables_to_seed() -> list[Table]:
    """Return the tables to populate, in foreign-key dependency order.

    The tenant-scoped tables plus the transitive closure of their foreign-key parents:
    ``memory_message`` cannot be inserted without a ``memory_session`` row, and four
    tables reference ``tenants``. Ordering comes from :func:`sqlalchemy.schema.sort_tables`
    rather than a hand-written list so a new relationship does not need a code change here.

    Returns:
        The tables to seed, parents first.
    """
    tables = _all_tables()
    wanted = {table.name for table in _registered_tables()}
    pending = list(wanted)
    while pending:
        for key in tables[pending.pop()].foreign_keys:
            parent = key.column.table.name
            if parent in tables and parent not in wanted:
                wanted.add(parent)
                pending.append(parent)
    return sort_tables([tables[name] for name in wanted])


def _stable_int(tag: str) -> int:
    """Return a deterministic, positive 32-bit-safe integer derived from ``tag``.

    Deterministic because the suite must be re-runnable and order-independent; derived
    from a digest rather than ``hash()`` because ``PYTHONHASHSEED`` randomises the latter
    between runs, which would make a unique-constraint collision an intermittent failure.

    Args:
        tag: The row tag the value belongs to.

    Returns:
        An integer in ``[0, 2**24)``.
    """
    return int(hashlib.sha256(tag.encode()).hexdigest()[:6], 16)


def _synthetic_value(column, tag: str) -> object:  # noqa: ANN001 - sqlalchemy Column
    """Invent a legal value for a NOT NULL column that has no default.

    Values are a function of ``(tag, column)``, so two rows written under different tags
    differ in every generated column. That matters more than it looks: ``users.username``
    and ``prompt_versions(prompt_key, version)`` are unique, and a unique-index violation
    bypasses row security — it would raise *before* the policy did, turning a real RLS
    assertion into a confusing integrity error.

    Args:
        column: The column to invent a value for.
        tag: A short token identifying the row being built.

    Returns:
        A value of the column's Python type.

    Raises:
        AssertionError: If the column's type is one this generator has never seen. That
            is deliberate: a newly registered tenant-scoped table with an exotic column
            must fail loudly here rather than be quietly dropped from the sweep.
    """
    enums = getattr(column.type, "enums", None)
    if enums:
        return enums[0]
    try:
        python_type = column.type.python_type
    except NotImplementedError:
        # JSON and friends decline to name a Python type; an empty object is valid for
        # every JSON column in this schema.
        return {}
    if python_type is bool:
        return True
    if python_type is int:
        return _stable_int(f"{tag}:{column.name}")
    if python_type is float:
        return 1.0
    if python_type is datetime:
        # ``bootstrap`` converts timestamp columns to ``timestamptz``, and asyncpg
        # refuses a naive datetime for those.
        return datetime.now(UTC)
    if python_type is str:
        value = f"{tag}-{column.name}"
        length = getattr(column.type, "length", None)
        return value[:length] if length else value
    raise AssertionError(
        f"{column.table.name}.{column.name} has type {column.type!r}, which the live "
        "RLS sweep does not know how to populate. Extend _synthetic_value — do not "
        "drop the table from the sweep."
    )


def _row_values(
    table: Table, tenant_id: int, tag: str, seeded: dict[tuple[str, int], object]
) -> dict[str, object]:
    """Build one insertable row for ``table`` owned by ``tenant_id``.

    Columns are resolved in a fixed order of precedence: the tenant discriminator (the
    column the whole test is about), then foreign keys (resolved to the *same tenant's*
    parent row, so the row is internally consistent and the only thing under test is the
    policy), then autoincrement primary keys (left to the sequence), then anything the
    database can fill itself, and finally the generated values.

    Args:
        table: The table to build a row for.
        tenant_id: The owning tenant.
        tag: A short token distinguishing this row from every other row in the table.
        seeded: Primary keys already inserted, keyed by ``(table name, tenant)``.

    Returns:
        A column-name → value mapping ready for ``table.insert().values(**row)``.
    """
    row: dict[str, object] = {}
    for column in table.columns:
        if table.name == "tenants" and column.primary_key:
            # The tenants row *is* the tenant, so its id is not invented.
            row[column.name] = tenant_id
            continue
        if column.name == _TENANT_COLUMN:
            row[column.name] = tenant_id
            continue
        key = next(iter(column.foreign_keys), None)
        if key is not None:
            if column.nullable:
                continue  # an optional reference adds nothing to what is under test
            parent = key.column.table.name
            row[column.name] = (
                tenant_id if parent == "tenants" else seeded[(parent, tenant_id)]
            )
            continue
        if column.primary_key and isinstance(column.type, Integer):
            continue  # serial
        if column.nullable or column.default is not None or column.server_default is not None:
            continue
        row[column.name] = _synthetic_value(column, tag)
    return row


# ─────────────────────────────────────────────────────────────────────────────
# Provisioning — a scratch database and a genuinely unprivileged role, both
# created and destroyed by this module.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Scratch:
    """Everything a test needs to talk to the scratch database.

    Attributes:
        database: The scratch database name (dropped on teardown).
        role: The unprivileged login role (dropped on teardown).
        owner_dsn: DSN of the provisioning role — owns the tables, and on a typical local
            cluster is a superuser, so it **bypasses RLS**. Used only for seeding and for
            the non-vacuity check that the rows exist at all.
        app_dsn: DSN of the ``NOSUPERUSER NOBYPASSRLS`` role. Every isolation assertion
            runs over this one.
        protected: The tables :func:`bootstrap_rls` reported installing the policy on.
    """

    database: str
    role: str
    owner_dsn: str
    app_dsn: str
    protected: tuple[str, ...]


async def _role_privileges(scratch: _Scratch) -> tuple[str, bool, bool]:
    """Return ``(role name, is superuser, has BYPASSRLS)`` for the reading role.

    The single fact this whole file rests on. Both flags exempt a role from row security,
    so if either is true every assertion below would pass while proving nothing.

    It deliberately goes through :func:`_app_engine`, the one constructor every isolation
    assertion uses, rather than through ``app_dsn`` directly: if that constructor is ever
    changed to hand back a privileged connection, this check is on the same wire and
    catches it.

    Args:
        scratch: The provisioned scratch handle.

    Returns:
        The connecting role's name and its two RLS-exemption flags.
    """
    engine = _app_engine(scratch)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT rolname, rolsuper, rolbypassrls "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            )
            name, superuser, bypass = result.one()
            return str(name), bool(superuser), bool(bypass)
    finally:
        await engine.dispose()


async def _drop_scratch(admin_dsn: str, database: str, role: str) -> None:
    """Drop the scratch database and role, reporting anything left behind.

    Ordered database-then-role because a role cannot be dropped while it still holds
    privileges recorded against objects in an existing database. Failures are collected
    rather than raised immediately so the second drop is always attempted — a leaked
    scratch database per run is not acceptable, and a leaked one that hides behind an
    earlier exception is worse.

    Args:
        admin_dsn: The provisioning DSN.
        database: The scratch database to drop.
        role: The scratch role to drop.

    Raises:
        RuntimeError: If either object could not be dropped, naming what leaked.
    """
    engine = create_async_engine(admin_dsn, isolation_level="AUTOCOMMIT")
    leaked: list[str] = []
    try:
        async with engine.connect() as conn:
            for statement, what in (
                (f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)', f"database {database}"),
                (f'DROP ROLE IF EXISTS "{role}"', f"role {role}"),
            ):
                try:
                    await conn.execute(text(statement))
                except SQLAlchemyError as exc:
                    leaked.append(f"{what}: {exc}")
    finally:
        await engine.dispose()
    if leaked:
        raise RuntimeError("scratch cleanup left objects behind — " + "; ".join(leaked))


async def _provision(admin_dsn: str) -> _Scratch:
    """Create the scratch database, the unprivileged role, the schema and the rows.

    The order is the security-relevant part: the schema is created and
    :func:`bootstrap_rls` runs **as the owner**, the DML grants are issued explicitly
    (the scratch role owns nothing, so it starts with no access at all), and only then is
    the unprivileged DSN handed to the tests. The rows are seeded over the owner
    connection, which bypasses RLS — seeding through the policy would make the fixture
    depend on the very thing under test.

    Args:
        admin_dsn: DSN of a role allowed to ``CREATE ROLE`` and ``CREATE DATABASE``.

    Returns:
        The provisioned :class:`_Scratch`.

    Raises:
        RuntimeError: If the freshly created role turns out to be exempt from RLS.
    """
    suffix = uuid.uuid4().hex[:12]
    database = f"aegis_rls_{suffix}"
    role = f"aegis_rls_role_{suffix}"
    # Hex only, so inlining it in the (non-parameterisable) CREATE ROLE utility
    # statement cannot terminate the literal.
    password = secrets.token_hex(16)

    url = make_url(admin_dsn)
    owner_dsn = url.set(database=database).render_as_string(hide_password=False)
    app_dsn = url.set(
        database=database, username=role, password=password
    ).render_as_string(hide_password=False)

    admin = create_async_engine(admin_dsn, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(
                text(
                    f'CREATE ROLE "{role}" LOGIN PASSWORD \'{password}\' '
                    "NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE INHERIT"
                )
            )
            await conn.execute(text(f'CREATE DATABASE "{database}"'))
    finally:
        await admin.dispose()

    try:
        owner = create_async_engine(owner_dsn)
        try:
            async with owner.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                await conn.run_sync(AegisBase.metadata.create_all)
            protected = tuple(await bootstrap_rls(owner))
            async with owner.begin() as conn:
                for statement in (
                    f'GRANT USAGE ON SCHEMA public TO "{role}"',
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                    f'IN SCHEMA public TO "{role}"',
                    f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{role}"',
                ):
                    await conn.execute(text(statement))
                await _seed(conn)
        finally:
            await owner.dispose()

        scratch = _Scratch(
            database=database,
            role=role,
            owner_dsn=owner_dsn,
            app_dsn=app_dsn,
            protected=protected,
        )
        name, superuser, bypass = await _role_privileges(scratch)
        if superuser or bypass:
            raise RuntimeError(
                f"the scratch reader role {name!r} is superuser={superuser} "
                f"bypassrls={bypass}; every isolation assertion would pass vacuously"
            )
    except Exception:
        # Never leak a scratch database or role because setup failed half-way.
        await _drop_scratch(admin_dsn, database, role)
        raise

    return scratch


async def _seed(conn: AsyncConnection) -> dict[tuple[str, int], object]:
    """Insert exactly one row per tenant into every seeded table.

    Args:
        conn: An owner connection (superuser on a stock local cluster, so RLS does not
            filter these writes — deliberately, see :func:`_provision`).

    Returns:
        The inserted primary keys, keyed by ``(table name, tenant)``, so a child row can
        reference its own tenant's parent.
    """
    seeded: dict[tuple[str, int], object] = {}
    for table in _tables_to_seed():
        for tenant_id in (_TENANT_A, _TENANT_B):
            row = _row_values(table, tenant_id, f"seed-t{tenant_id}", seeded)
            result = await conn.execute(table.insert().values(**row))
            key = result.inserted_primary_key
            seeded[(table.name, tenant_id)] = key[0] if key else None
    return seeded


@pytest.fixture(scope="module")
def scratch() -> _Scratch:
    """Provide a scratch database + unprivileged role for the module, then destroy them.

    Module-scoped because provisioning a database per test would dominate the runtime,
    and synchronous (driving ``asyncio.run`` itself) so each async test still creates its
    engines inside its own event loop — no fixture-scoped loop to keep in step.

    Yields:
        The :class:`_Scratch` handle. Skips (or fails, under ``AEGIS_REQUIRE_PG_TESTS``)
        when no PostgreSQL can be provisioned.
    """
    admin_dsn = _admin_dsn()
    try:
        provisioned = asyncio.run(_provision(admin_dsn))
    except (OSError, SQLAlchemyError) as exc:
        _skip_or_fail(f"{type(exc).__name__}: {exc}")
        raise  # unreachable: _skip_or_fail always raises
    try:
        yield provisioned
    finally:
        asyncio.run(_drop_scratch(admin_dsn, provisioned.database, provisioned.role))


def _app_engine(scratch: _Scratch):  # noqa: ANN202 - AsyncEngine
    """Return an engine connecting as the unprivileged role.

    Args:
        scratch: The provisioned scratch handle.

    Returns:
        A fresh :class:`~sqlalchemy.ext.asyncio.AsyncEngine`; callers dispose it.
    """
    return create_async_engine(scratch.app_dsn)


# ─────────────────────────────────────────────────────────────────────────────
# The evidence.
# ─────────────────────────────────────────────────────────────────────────────


async def test_the_reading_role_is_neither_superuser_nor_bypassrls(scratch: _Scratch):
    """Everything else in this file is vacuous if this is not true.

    A superuser — or any role with ``BYPASSRLS`` — skips row security outright, so an
    isolation suite run as one passes with every policy dropped. This is the guard that
    makes the rest of the file mean something, and it is asserted rather than assumed.
    """
    name, superuser, bypass = await _role_privileges(scratch)
    assert name == scratch.role, (
        f"the isolation assertions are running as {name!r}, not as the unprivileged "
        f"scratch role {scratch.role!r}"
    )
    assert superuser is False, f"{name} is a superuser and bypasses every RLS policy"
    assert bypass is False, f"{name} has BYPASSRLS and bypasses every RLS policy"


async def test_bootstrap_protects_every_registered_table_in_the_live_schema(scratch: _Scratch):
    """``bootstrap_rls`` must have installed an enabled, FORCEd policy on all thirteen.

    Read back from ``pg_class``/``pg_policy`` on the real database rather than from the
    statements the function claims to have emitted. FORCE is checked separately from
    ENABLE because ENABLE alone exempts the table owner, which is the shape that made
    the previous policies decorative.
    """
    expected = {table.name for table in _registered_tables()}
    assert expected == set(_TENANT_SCOPED_TABLES), (
        "a registered tenant-scoped table is missing from the host schema, so the sweep "
        f"below would silently not cover it: {set(_TENANT_SCOPED_TABLES) - expected}"
    )
    assert set(scratch.protected) == expected

    engine = create_async_engine(scratch.owner_dsn)
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                        "EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid "
                        "        AND p.polname = :policy) "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'p')"
                    ),
                    {"policy": _POLICY_NAME},
                )
            ).all()
    finally:
        await engine.dispose()

    state = {name: (enabled, forced, policy) for name, enabled, forced, policy in rows}
    for name in sorted(expected):
        assert state[name] == (True, True, True), (
            f"{name} is (enabled, forced, has_policy)={state[name]} — it is NOT isolated "
            "by the database"
        )


async def test_no_live_table_carries_a_tenant_column_without_being_registered(
    scratch: _Scratch,
):
    """A tenant-scoped table nobody registered is the exact hole this phase found.

    ``bootstrap_rls`` only *logs* that gap (deliberately — see ``_plan_rls``: it refuses
    to conjure a policy for a table nobody declared), and a log line in a boot sequence is
    not a control. This asserts it against the live catalog instead, so the next time a
    model grows a ``tenant_id`` column the suite says so before a tenant reads another
    tenant's rows.
    """
    engine = create_async_engine(scratch.owner_dsn)
    try:
        async with engine.connect() as conn:
            live = (
                await conn.execute(
                    text(
                        "SELECT c.relname FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "JOIN pg_attribute a ON a.attrelid = c.oid "
                        "WHERE n.nspname = current_schema() "
                        "  AND c.relkind IN ('r', 'p') "
                        "  AND a.attname = :tenant_column "
                        "  AND a.attnum > 0 AND NOT a.attisdropped"
                    ),
                    {"tenant_column": _TENANT_COLUMN},
                )
            ).scalars().all()
    finally:
        await engine.dispose()

    unregistered = set(live) - set(_TENANT_SCOPED_TABLES)
    assert not unregistered, (
        f"live table(s) {sorted(unregistered)} carry a '{_TENANT_COLUMN}' column but are "
        "not in aegis.governance.rls._TENANT_SCOPED_TABLES, so they got no policy and a "
        "scoped request can read every tenant's rows there. Fix: add each name to that "
        "tuple."
    )


async def test_the_scratch_database_really_holds_both_tenants_rows(scratch: _Scratch):
    """The non-vacuity check for the sweep: two rows exist in every swept table.

    "Tenant A sees only its own row" is trivially true when tenant B's row was never
    written. Read over the owner connection, which bypasses RLS, so this counts what is
    physically there rather than what a policy allows.
    """
    engine = create_async_engine(scratch.owner_dsn)
    try:
        async with engine.connect() as conn:
            for table in _registered_tables():
                rows = (
                    await conn.execute(
                        text(
                            f'SELECT {_TENANT_COLUMN} FROM "{table.name}" '
                            f"ORDER BY {_TENANT_COLUMN}"
                        )
                    )
                ).scalars().all()
                assert rows == [_TENANT_A, _TENANT_B], f"{table.name}: seeded {rows}"
    finally:
        await engine.dispose()


async def test_a_bound_tenant_scope_reads_only_its_own_row_in_every_table(scratch: _Scratch):
    """The sweep: for every registered table, tenant A's scope sees A's row and no other.

    Driven from :data:`aegis.governance.rls._TENANT_SCOPED_TABLES` so a newly registered
    table is covered without touching this test, and asserted for **both** tenants so a
    policy that happened to hard-code one of them would still fail.

    ``set_tenant_scope`` writes a transaction-local GUC, so the bind and the read share
    one transaction (the session opens it on first execute and it is rolled back after).
    """
    engine = _app_engine(scratch)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        for table in _registered_tables():
            for tenant_id in (_TENANT_A, _TENANT_B):
                async with maker() as session:
                    await set_tenant_scope(session, tenant_id)
                    visible = (
                        await session.execute(
                            text(f'SELECT {_TENANT_COLUMN} FROM "{table.name}"')
                        )
                    ).scalars().all()
                    await session.rollback()
                assert visible == [tenant_id], (
                    f"{table.name}: scoped to tenant {tenant_id} but saw {visible} — "
                    "the tenant_isolation policy is missing or not enforced"
                )
    finally:
        await engine.dispose()


async def test_a_bound_scope_cannot_write_a_row_stamped_for_another_tenant(scratch: _Scratch):
    """Writes are constrained by the same predicate — with a positive control.

    The policy is created without an explicit ``WITH CHECK``, so PostgreSQL reuses
    ``USING`` for writes: under tenant A's scope an INSERT stamping tenant B must be
    rejected by the database, not merely hidden afterwards.

    Each table is checked twice in one transaction. The **positive control comes first**:
    the identical INSERT stamped for tenant A has to succeed. Without it, a mistyped
    statement would raise for its own reasons and the rejection assertion would pass
    while proving nothing — the same trap that made the DDL-string tests worthless.
    Both writes are rolled back, so the sweep above is order-independent of this one.
    """
    parents = await _parents(scratch)
    engine = _app_engine(scratch)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        for table in _registered_tables():
            async with maker() as session:
                await set_tenant_scope(session, _TENANT_A)
                own = _row_values(table, _TENANT_A, "own-a", parents)
                await session.execute(table.insert().values(**own))

                other = _row_values(table, _TENANT_B, "cross-b", parents)
                with pytest.raises(SQLAlchemyError) as caught:
                    await session.execute(table.insert().values(**other))
                assert "row-level security" in str(caught.value), (
                    f"{table.name}: writing a tenant-{_TENANT_B} row while scoped to "
                    f"tenant {_TENANT_A} failed for the wrong reason: {caught.value}"
                )
                await session.rollback()
    finally:
        await engine.dispose()


async def _parents(scratch: _Scratch) -> dict[tuple[str, int], object]:
    """Return the seeded parent keys a child row needs, for **both** tenants.

    Only ``memory_message`` needs one (its ``session_id`` references ``memory_session``),
    and the cross-tenant write probe must build a row that is internally valid:
    foreign-key checks bypass row security, so a dangling reference would raise before
    the policy could — and the test would "pass" on the wrong error.

    Read over the owner connection precisely *because* it bypasses RLS: a scoped read
    could never see the other tenant's parent row, which is the whole point.

    Args:
        scratch: The provisioned scratch handle.

    Returns:
        ``{(parent table, tenant): primary key}`` for the parents this module seeds.
    """
    engine = create_async_engine(scratch.owner_dsn)
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(f"SELECT id, {_TENANT_COLUMN} FROM memory_session")
                )
            ).all()
    finally:
        await engine.dispose()
    return {("memory_session", tenant_id): key for key, tenant_id in rows}


async def test_an_unbound_scope_is_deliberately_fail_open_pending_security_definer_login(
    scratch: _Scratch,
):
    """With **no** tenant bound the policy does not restrict — on purpose, for now.

    This is not an oversight and it is not a bug report: ``_TENANT_ISOLATION_PREDICATE``
    documents that an unset (or empty) ``app.tenant_id`` leaves the policy permissive,
    because the host authenticates by reading ``users`` *before* any tenant is known and
    the platform-admin surfaces list across tenants. Under FORCE, a fail-closed unset
    branch would make login itself return zero rows.

    Both spellings of "unset" are pinned: never binding the GUC at all, and the empty
    string ``set_tenant_scope(session, None)`` writes for an unscoped request.

    **When someone closes that gap** — the ``SECURITY DEFINER`` login path in the phase
    plan — this test is what will go red, and that red is the signal to re-read the
    predicate's comment and delete this test rather than to "fix" the policy back.
    """
    engine = _app_engine(scratch)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            never_bound = (
                await session.execute(text(f"SELECT {_TENANT_COLUMN} FROM users"))
            ).scalars().all()
            await session.rollback()

        async with maker() as session:
            await set_tenant_scope(session, None)
            cleared = (
                await session.execute(text(f"SELECT {_TENANT_COLUMN} FROM users"))
            ).scalars().all()
            await session.rollback()
    finally:
        await engine.dispose()

    assert sorted(never_bound) == [_TENANT_A, _TENANT_B]
    assert sorted(cleared) == [_TENANT_A, _TENANT_B]


async def test_a_bound_scope_does_not_survive_its_transaction(scratch: _Scratch):
    """``set_tenant_scope`` is transaction-local, so a pooled connection cannot leak it.

    ``set_config(..., is_local => true)`` is the reason: a session-level ``SET`` would
    persist for the life of the *connection*, and the pool hands that connection to the
    next request — one tenant's scope silently filtering another tenant's query. Proved
    on the same physical connection: bind tenant A, roll back, then read the GUC again.
    """
    engine = _app_engine(scratch)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            await set_tenant_scope(session, _TENANT_A)
            bound = (
                await session.execute(text("SELECT current_setting('app.tenant_id', true)"))
            ).scalar_one()
            assert bound == str(_TENANT_A)
            await session.rollback()

            after = (
                await session.execute(text("SELECT current_setting('app.tenant_id', true)"))
            ).scalar_one()
            assert after in (None, ""), f"scope survived the transaction as {after!r}"
            leaked = (
                await session.execute(text(f"SELECT {_TENANT_COLUMN} FROM users"))
            ).scalars().all()
            await session.rollback()
    finally:
        await engine.dispose()
    assert sorted(leaked) == [_TENANT_A, _TENANT_B]


# ─────────────────────────────────────────────────────────────────────────────
# The retrieval leg — no database, but the same question: can tenant B reach a
# phrase that only tenant A ingested?
# ─────────────────────────────────────────────────────────────────────────────


async def _no_llm(*args: object, **kwargs: object) -> str:
    """Fail the test if the pipeline reaches for a chat model.

    The retrieval test below runs with ``rerank_enabled=False`` precisely so no LLM is
    needed. Wiring a canned answer here instead would let a rerank silently run against a
    fabricated model and launder a leaked passage; raising means the test tells the truth
    about what did and did not execute.

    Raises:
        AssertionError: Always.
    """
    raise AssertionError(
        "the retrieval isolation test must not call an LLM — no completer is available"
    )


def _cache_blob(redis: InMemoryRedis) -> str:
    """Return every byte the cache has stored, as one searchable string.

    Reaching into the store's own dictionaries is the point: the leak this guards against
    is a cached *entry* readable under the wrong scope, so the assertion has to look at
    what was written, not at what a scoped read returns.

    Args:
        redis: The cache's backing store.

    Returns:
        The concatenation of every key, value and index member.
    """
    parts = list(redis._kv.keys()) + list(redis._kv.values())
    for key, members in redis._sets.items():
        parts.append(key)
        parts.extend(members)
    return "\n".join(parts)


async def test_retrieval_never_surfaces_another_tenants_phrase_across_two_runs():
    """Tenant B's retrieval must not reach tenant A's phrase — twice in a row.

    The second run is the one that matters: the first populates the semantic cache, and a
    cache keyed without the tenant would serve tenant A's passages to tenant B on the
    second identical question. That is the leak this phase was opened to close.

    What is real here, and what is not — stated plainly rather than implied:

    * **Real**: the shipped :class:`~aegis.retrieval.pipeline.Retriever`, its ingest path,
      wide recall over an embedded **Chroma** vector store (the genuine engine, in
      process), the BM25 keyword arm, RRF fusion, spotlighting, and the real
      :class:`~aegis.retrieval.cache.SemanticCache` with both tiers — over
      :class:`~aegis.retrieval.memory.InMemoryRedis`, the store the product's own lite
      mode ships, not a test double.
    * **Not covered here**: the LLM rerank stage (disabled — no model is available; the
      completer raises if anything calls it), the LightRAG/Neo4j production backend, and
      a Redis *server*. Cross-tenant isolation of the LightRAG arm is a Phase 4 concern
      (per-tenant instances) and is not claimed by this test.

    The final assertion is the positive control: tenant A *can* retrieve its own phrase
    with the same query, so tenant B's clean result is isolation rather than an empty
    index.
    """
    redis = InMemoryRedis()
    retriever = Retriever(
        backend=InMemoryKnowledgeBackend([], embed=_default_offline_embed),
        cache=SemanticCache(redis, ttl_seconds=300, similarity_threshold=0.985),
        complete=_no_llm,
        embed=_default_offline_embed,
        config=RetrievalConfig(rerank_enabled=False),
    )
    scope_a = RetrievalScope(tenant_id=_TENANT_A)
    scope_b = RetrievalScope(tenant_id=_TENANT_B)

    await retriever.ingest(
        [
            {
                "id": "acme-escalation",
                "text": (
                    "Acme escalation runbook. The reactor emergency shutdown code is "
                    f"{_LEAK_PHRASE}. Page the duty engineer before using it."
                ),
            }
        ],
        scope=scope_a,
    )
    await retriever.ingest(
        [
            {
                "id": "globex-escalation",
                "text": (
                    "Globex escalation runbook. The reactor emergency shutdown code is "
                    "held by the site controller and is never written down."
                ),
            }
        ],
        scope=scope_b,
    )

    query = "what is the reactor emergency shutdown code?"
    hits: list[bool] = []
    for run in (1, 2):
        result = await retriever.retrieve(query, scope=scope_b)
        hits.append(result.cache_hit)
        assert _LEAK_PHRASE not in result.model_dump_json(), (
            f"run {run}: tenant {_TENANT_A}'s phrase reached tenant {_TENANT_B}'s result"
        )
        assert all(_LEAK_PHRASE not in source.text for source in result.sources), (
            f"run {run}: the leak reached tenant {_TENANT_B}'s citations"
        )
        assert _LEAK_PHRASE not in _cache_blob(redis), (
            f"run {run}: the leak is readable in tenant {_TENANT_B}'s cache partition"
        )

    assert hits[1] is True, (
        "the second run did not come from the cache, so this test never exercised the "
        "cache-leak path it exists for"
    )
    assert _cache_blob(redis), (
        "nothing was ever written to the cache, so searching it for the leaked phrase "
        "proved nothing"
    )

    owner = await retriever.retrieve(query, scope=scope_a)
    assert _LEAK_PHRASE in owner.model_dump_json(), (
        "tenant A cannot retrieve its own document, so tenant B's clean result proves "
        "nothing about isolation"
    )
