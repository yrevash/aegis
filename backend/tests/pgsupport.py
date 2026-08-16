"""Real-PostgreSQL scaffolding for the backend suite — the one place that owns it.

Why this module exists at all. Until 2026-08-16 the backend suite ran on SQLite, and
that made a whole class of test unfalsifiable. Production carries guards shaped like
``if bind.dialect.name != "postgresql": return`` — on SQLite
:func:`aegis.governance.rls.set_tenant_scope` therefore *silently did nothing*, so every
test that read like a proof of tenant isolation was only ever exercising the app-level
``WHERE tenant_id = :ctx`` filter. That is precisely how ten tenant-scoped tables ended
up with no Row-Level Security policy while the suite stayed green. SQLite is gone from
the backend tests; everything runs on a real cluster.

Two properties matter more than convenience, and both are enforced here rather than
documented and hoped for:

1. **The role that runs the assertions is not a superuser.** PostgreSQL skips row
   security *entirely* for ``SUPERUSER`` and for any role holding ``BYPASSRLS``;
   ``FORCE ROW LEVEL SECURITY`` removes only the table *owner's* exemption. A suite run
   as ``postgres`` passes with every policy dropped. :func:`create_scratch` therefore
   mints a ``LOGIN NOSUPERUSER NOBYPASSRLS`` role and :func:`assert_unprivileged`
   re-reads ``pg_roles`` over the very connection the tests will use, so a privileged
   reader is an error rather than a quietly green run.
2. **Nothing leaks.** Every scratch database and role is uuid-named and dropped in a
   ``finally``; :func:`drop_scratch` raises when either survives, so a leak is a failing
   run and not a slow accumulation of junk in the developer's cluster.

Nothing here reads application configuration. The admin DSN comes from
:data:`ADMIN_DSN_ENV` or defaults to the local cluster as the invoking OS user, so no
``.env`` file can influence what these tests prove. When no cluster can be provisioned
the suite skips with a message naming exactly what went unverified, and
``AEGIS_REQUIRE_PG_TESTS=1`` turns that skip into a failure so CI can demand the
evidence instead of accepting a green-looking skip.

Verified against: PostgreSQL 14+ (``DROP DATABASE ... WITH (FORCE)`` needs 13+),
SQLAlchemy 2.0.x asyncio API, asyncpg — August 2026.
"""

from __future__ import annotations

import getpass
import os
import secrets
import uuid
from dataclasses import dataclass
from typing import NoReturn

import pytest
from sqlalchemy import MetaData, Table, inspect, make_url, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

#: Environment variable naming the **admin** DSN used only to create (and drop) scratch
#: databases and roles. Deliberately *not* ``POSTGRES_DSN``: reusing the application's
#: DSN is how a suite ends up connecting as a superuser and proving nothing.
ADMIN_DSN_ENV = "AEGIS_PG_TEST_ADMIN_DSN"

#: Set to ``1`` to turn "no PostgreSQL reachable" from a skip into a hard failure. CI
#: sets it, because a security test that silently skips is indistinguishable from one
#: that passes, and that is its own failure mode.
REQUIRE_ENV = "AEGIS_REQUIRE_PG_TESTS"


def admin_dsn() -> str:
    """Return the DSN used to provision scratch databases and roles.

    Defaults to the local cluster as the invoking OS user (the usual Homebrew/``initdb``
    setup) rather than reading any application configuration: the whole point of this
    module is that ambient config cannot influence what the tests prove.

    Returns:
        A SQLAlchemy asyncpg DSN pointing at a maintenance database.
    """
    configured = os.environ.get(ADMIN_DSN_ENV)
    if configured:
        return configured
    return f"postgresql+asyncpg://{getpass.getuser()}@localhost:5432/postgres"


def skip_or_fail(*, unverified: str, reason: str) -> NoReturn:
    """Skip loudly — or fail — when a real database could not be provisioned.

    A skipped test reads as green in every report anyone actually looks at, so the
    message has to name what went *unverified* rather than only saying "postgres
    unavailable". Under ``AEGIS_REQUIRE_PG_TESTS=1`` the skip becomes a failure so a
    pipeline can demand the evidence.

    Args:
        unverified: Prose naming exactly which behaviour is now unproven.
        reason: The underlying cause, appended verbatim.

    Raises:
        Failed: Always, via ``pytest.fail``, when the require-flag is set.
        Skipped: Always, via ``pytest.skip``, otherwise.
    """
    message = (
        f"NOT VERIFIED against a live database: {unverified} No PostgreSQL could be "
        f"provisioned via {ADMIN_DSN_ENV} ({admin_dsn()!r}). Cause: {reason}. "
        f"Set {REQUIRE_ENV}=1 to make this a failure instead of a skip."
    )
    if os.environ.get(REQUIRE_ENV) == "1":
        pytest.fail(message)
    pytest.skip(message)


async def seed(session: AsyncSession, *rows: object) -> None:
    """Persist ORM ``rows`` parent-first, flushing once per foreign-key level.

    Why this exists rather than a plain ``session.add_all(...)``. None of the governance
    or memory models declares a SQLAlchemy ``relationship()`` — they carry raw
    ``ForeignKey`` columns only (``UsageLedger.user_id`` → ``users.id``,
    ``MemoryMessage.session_id`` → ``memory_session.id``, and so on). SQLAlchemy's unit
    of work derives its inter-mapper flush order from *relationships*, so with none
    declared it falls back to sorting mappers by name. That orders the flush
    ``Tenant`` → ``UsageLedger`` → ``User`` and ``MemoryMessage`` → ``MemorySession``:
    in both cases the child INSERT is emitted **before** the parent it references.

    On SQLite that was invisible, because SQLite does not enforce foreign keys unless
    ``PRAGMA foreign_keys=ON`` is set per connection — which this suite never did. Every
    such seed therefore wrote an orphaned row and passed. PostgreSQL enforces the
    constraint, so the same seed raises ``ForeignKeyViolationError``. The rows the tests
    describe were always meant to be related; only the write order was wrong.

    The fix keeps every constraint exactly as production declares it. Rows are grouped by
    their table and the groups are flushed in ``MetaData.sorted_tables`` order, which
    *is* the topological sort over the real ``ForeignKey`` graph — so a parent is always
    in the database before the child that points at it. A cycle between tables (which
    ``sorted_tables`` cannot order) would surface as the same violation rather than being
    hidden; the schema has none.

    The caller still owns the transaction: this flushes but never commits, so a seed can
    be composed with further writes and rolled back as one unit.

    Args:
        session: The open session to write through.
        *rows: ORM instances in any order; dependency order is derived, not assumed.

    Raises:
        TypeError: If an argument is not a mapped ORM instance, which would otherwise
            fail much later as a confusing flush error.
    """
    by_table: dict[Table, list[object]] = {}
    for row in rows:
        state = inspect(row, raiseerr=False)
        if state is None or not hasattr(state, "mapper"):
            raise TypeError(
                f"seed() takes mapped ORM instances; got {type(row).__name__!r}"
            )
        by_table.setdefault(state.mapper.local_table, []).append(row)

    if not by_table:
        return

    # This suite spans *two* declarative registries — ``app.data.models.Base``
    # (``approvals``, ``chunks``) and ``aegis.governance.models.AegisBase`` (``tenants``,
    # ``users``, ``usage_ledger``, ...) — and a single seed legitimately mixes them. A
    # ``ForeignKey`` can only resolve within one MetaData, so ordering *within* each
    # registry must be topological while ordering *between* registries is free. Ranking
    # by (registry first seen, position in that registry's sorted_tables) gives exactly
    # that, and never raises on a table its neighbour's MetaData has never heard of.
    registries: list[MetaData] = []
    for table in by_table:
        if table.metadata not in registries:
            registries.append(table.metadata)
    position = {
        table: (rank, index)
        for rank, metadata in enumerate(registries)
        for index, table in enumerate(metadata.sorted_tables)
    }

    for table in sorted(by_table, key=lambda t: position[t]):
        session.add_all(by_table[table])
        await session.flush()


@dataclass(frozen=True, slots=True)
class Scratch:
    """A throwaway database plus a genuinely unprivileged login role.

    Attributes:
        database: The scratch database name (dropped on teardown).
        role: The ``NOSUPERUSER NOBYPASSRLS`` login role (dropped on teardown).
        owner_dsn: DSN of the provisioning role. It owns the tables and, on a stock
            local cluster, is a superuser — so it **bypasses RLS**. Use it for DDL,
            seeding and non-vacuity checks, never for an isolation assertion.
        app_dsn: DSN of the unprivileged role. Every request-path session and every
            isolation assertion runs over this one.
    """

    database: str
    role: str
    owner_dsn: str
    app_dsn: str


async def create_scratch(dsn: str, *, prefix: str) -> Scratch:
    """Create a uuid-named database and an unprivileged login role beside it.

    The role is created first and the database second so that a failure to create the
    role never leaves an orphaned database behind. Nothing is granted here: the scratch
    role owns nothing and starts with no access at all, and the caller decides what it
    gets (for the application schema that is
    :func:`aegis.governance.rls.grant_serving_role`, run from the owner connection —
    the same code production uses, so the grants under test are the real ones).

    Args:
        dsn: DSN of a role allowed to ``CREATE ROLE`` and ``CREATE DATABASE``.
        prefix: Short tag prepended to both names, so a leaked object says which suite
            made it.

    Returns:
        The provisioned :class:`Scratch`.
    """
    suffix = uuid.uuid4().hex[:12]
    database = f"{prefix}_{suffix}"
    role = f"{prefix}_role_{suffix}"
    # Hex only, so inlining it in the (non-parameterisable) CREATE ROLE utility
    # statement cannot terminate the literal.
    password = secrets.token_hex(16)

    url = make_url(dsn)
    scratch = Scratch(
        database=database,
        role=role,
        owner_dsn=url.set(database=database).render_as_string(hide_password=False),
        app_dsn=url.set(
            database=database, username=role, password=password
        ).render_as_string(hide_password=False),
    )

    admin = create_async_engine(dsn, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(
                text(
                    f'CREATE ROLE "{role}" LOGIN PASSWORD \'{password}\' '
                    "NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE INHERIT"
                )
            )
            try:
                await conn.execute(text(f'CREATE DATABASE "{database}"'))
            except SQLAlchemyError:
                await conn.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
                raise
    finally:
        await admin.dispose()
    return scratch


async def drop_scratch(dsn: str, scratch: Scratch) -> None:
    """Drop the scratch database and role, reporting anything left behind.

    Ordered database-then-role because a role cannot be dropped while privileges are
    still recorded against objects in an existing database. Failures are collected
    rather than raised immediately so the second drop is always attempted: one leaked
    scratch database per run is not acceptable, and a leaked one hiding behind an
    earlier exception is worse.

    Args:
        dsn: The provisioning DSN.
        scratch: The handle to destroy.

    Raises:
        RuntimeError: If either object could not be dropped, naming what leaked.
    """
    admin = create_async_engine(dsn, isolation_level="AUTOCOMMIT")
    leaked: list[str] = []
    try:
        async with admin.connect() as conn:
            for statement, what in (
                (
                    f'DROP DATABASE IF EXISTS "{scratch.database}" WITH (FORCE)',
                    f"database {scratch.database}",
                ),
                (f'DROP ROLE IF EXISTS "{scratch.role}"', f"role {scratch.role}"),
            ):
                try:
                    await conn.execute(text(statement))
                except SQLAlchemyError as exc:
                    leaked.append(f"{what}: {exc}")
    finally:
        await admin.dispose()
    if leaked:
        raise RuntimeError("scratch cleanup left objects behind — " + "; ".join(leaked))


async def role_privileges(engine: AsyncEngine) -> tuple[str, bool, bool]:
    """Return ``(role name, is superuser, has BYPASSRLS)`` for ``engine``'s login role.

    Both flags exempt a role from row security, so if either is true every isolation
    assertion made over that engine passes while proving nothing.

    Args:
        engine: The engine to interrogate — pass the *same* one the tests will use, so a
            constructor that quietly hands back a privileged connection is caught.

    Returns:
        The connecting role's name and its two RLS-exemption flags.
    """
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT rolname, rolsuper, rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user"
            )
        )
        name, superuser, bypass = result.one()
        return str(name), bool(superuser), bool(bypass)


async def assert_unprivileged(engine: AsyncEngine, *, expected_role: str) -> None:
    """Fail provisioning unless ``engine`` connects as the unprivileged scratch role.

    This is the single fact the whole Postgres migration rests on, so it is asserted at
    provisioning time rather than assumed. A suite whose serving engine turned out to be
    a superuser would report exactly the same green as one whose policies all work.

    Args:
        engine: The serving engine every request-path session will be built from.
        expected_role: The scratch role name that engine must be connecting as.

    Raises:
        RuntimeError: If the role is wrong, a superuser, or holds ``BYPASSRLS``.
    """
    name, superuser, bypass = await role_privileges(engine)
    if name != expected_role:
        raise RuntimeError(
            f"the suite's serving engine connects as {name!r}, not as the unprivileged "
            f"scratch role {expected_role!r}; RLS would not be exercised"
        )
    if superuser or bypass:
        raise RuntimeError(
            f"the scratch serving role {name!r} is superuser={superuser} "
            f"bypassrls={bypass}; every tenant-isolation assertion would pass vacuously"
        )
