"""Real-PostgreSQL fixtures for the whole ``aegis`` test suite. No SQLite, anywhere.

Why this file exists — the short version. Production is full of guards shaped like
``if bind.dialect.name != "postgresql": return``. On SQLite :func:`set_tenant_scope`
therefore did *nothing*, so every test that looked like it proved tenant isolation was
only ever exercising the app-level ``WHERE tenant_id`` filter. That is how ten tables
ended up with no RLS policy and nobody noticed, and why ``test_rls.py`` could assert DDL
*strings* against a hand-written fake engine and still look green. A test that cannot
fail is worse than no test, because it stops anyone writing the real one.

So every database-backed test in this package now runs on a real cluster, and — this is
the load-bearing part — over a ``LOGIN NOSUPERUSER NOBYPASSRLS`` role. PostgreSQL skips
row security **entirely** for a superuser (``FORCE ROW LEVEL SECURITY`` removes the
*owner's* exemption, not that one), so a suite run as ``postgres`` passes with every
policy dropped. :func:`_assert_unprivileged` is checked during provisioning, so a
privileged reader is a hard error rather than a green run.

**The speed strategy: one template database per session, cloned per test.**
Provisioning the schema costs ~1s (``create_all`` over three metadatas plus
:func:`~aegis.governance.rls.bootstrap_rls`); doing that per test would add minutes.
``CREATE DATABASE … TEMPLATE`` is a file-level copy — ~0.28s on the local cluster, ~0.11s
to drop — and it gives each test a genuinely independent database. A shared database
with a rolled-back transaction per test would be faster still, but it cannot express what
this suite actually tests: ``tests/ops/test_registry_durability.py`` opens four
*concurrent* sessions and relies on a real unique-constraint collision between them, and
the reconcile tests issue DDL. Correctness first; the clone is the cheapest thing that
keeps it.

**No cluster, no evidence.** When PostgreSQL cannot be provisioned the database tests
skip with a message naming exactly what went unverified, and ``AEGIS_REQUIRE_PG_TESTS=1``
turns that skip into a failure — a security test that silently skips reads as green,
which is its own failure mode. This mirrors ``backend/tests/integration/
test_tenant_isolation_live.py``, deliberately: one fixture design, not two.
"""

from __future__ import annotations

# Before any import: this venv holds two OpenMP runtimes (torch's, via Docling and via
# presidio-analyzer's device detector, and xgboost's/scikit-learn's), and one process
# holding both segfaults or deadlocks depending on load order — measured 2026-08-18, and
# it took this suite down at ~24%. ``OMP_NUM_THREADS=1`` is the only value that fixes it.
# It must be set before the first OpenMP library loads, which is why it is here rather
# than in a fixture. See ``backend/src/app/__init__.py`` for the full note.
import os  # noqa: E402

os.environ.setdefault("OMP_NUM_THREADS", "1")


import asyncio
import getpass
import os
import secrets
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
from sqlalchemy import make_url, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

# Import every module whose declarative models must be registered before ``create_all``.
# The template database has to carry the union of what all suites need, and it is built
# once, lazily, from ``AegisBase.metadata`` — so a table whose module nobody imported
# would simply not exist and the failure would surface as a confusing UndefinedTable in
# an unrelated test.
import aegis.governance.models  # noqa: F401 - registration side-effect only
import aegis.jobs.models  # noqa: F401 - registration side-effect only
import aegis.memory.stores  # noqa: F401 - registration side-effect only
import aegis.ops.models  # noqa: F401 - registration side-effect only

# Registers the run record's models and — through ``aegis.runs.__init__`` — the
# ``after_create`` hook that creates ``run_events``' monthly partitions. Without it the
# template would carry a partitioned table that rejects every write.
import aegis.runs.models  # noqa: F401 - registration side-effect only
import aegis.settings.models  # noqa: F401 - registration side-effect only
from aegis.data import AegisBase
from aegis.governance.rls import bootstrap_rls

# The ops suite's stand-in for the host-owned ``Approval`` ORM. Imported here (rather
# than left to ``tests/ops/conftest.py``) so the template schema is the same whichever
# subset of the suite is being run — see the module docstring on why the template is
# built from the registry rather than from whatever happened to be imported.
from .ops._approval import FakeApproval  # noqa: F401 - registration side-effect only

#: Environment variable naming the **admin** DSN used only to create and drop the
#: template/scratch databases and the unprivileged role. Deliberately *not*
#: ``POSTGRES_DSN``: reusing the application's DSN is exactly how an isolation test ends
#: up connecting as a superuser and proving nothing.
ADMIN_DSN_ENV = "AEGIS_PG_TEST_ADMIN_DSN"

#: Set to ``1`` to turn "no PostgreSQL reachable" from a skip into a hard failure. CI
#: sets it, because a skipped security test is indistinguishable from a passing one.
REQUIRE_ENV = "AEGIS_REQUIRE_PG_TESTS"


def admin_dsn() -> str:
    """Return the DSN used to provision the template database and the scratch role.

    Defaults to the local cluster as the invoking OS user (the usual Homebrew/``initdb``
    setup) rather than reading any application configuration: the point of these fixtures
    is that ambient config cannot influence what they prove.

    Returns:
        A SQLAlchemy asyncpg DSN pointing at a maintenance database.
    """
    return os.environ.get(ADMIN_DSN_ENV) or (
        f"postgresql+asyncpg://{getpass.getuser()}@localhost:5432/postgres"
    )


def skip_or_fail(reason: str) -> None:
    """Skip loudly — or fail — when the database evidence could not be gathered.

    The message names what went unverified rather than saying "postgres unavailable",
    because the reader of a skipped run needs to know which guarantee is currently
    unevidenced.

    Args:
        reason: The underlying cause, appended verbatim to the message.

    Raises:
        Failed: Always, via ``pytest.fail``, when :data:`REQUIRE_ENV` is ``1``.
        Skipped: Always, via ``pytest.skip``, otherwise.
    """
    message = (
        "The aegis database suite was NOT run: no PostgreSQL could be provisioned via "
        f"{ADMIN_DSN_ENV} ({admin_dsn()!r}). Unverified: tenant scoping under real RLS, "
        "the governance/ops/memory persistence paths, and every catalog read-back that "
        f"proves a policy exists. Cause: {reason}. Set {REQUIRE_ENV}=1 to make this a "
        "failure instead of a skip."
    )
    if os.environ.get(REQUIRE_ENV) == "1":
        pytest.fail(message)
    pytest.skip(message)


@dataclass(frozen=True, slots=True)
class PostgresTemplate:
    """The session-wide template database and the unprivileged role that reads it.

    Attributes:
        admin_dsn: DSN of the provisioning role (``CREATE DATABASE``/``CREATE ROLE``).
        database: The template database name; cloned per test, dropped at session end.
        role: The ``NOSUPERUSER NOBYPASSRLS`` login role every test connects as.
        password: That role's generated password, needed to build the per-test DSN.
        protected: The tables :func:`bootstrap_rls` installed the tenant policy on.
    """

    admin_dsn: str
    database: str
    role: str
    password: str
    protected: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PostgresScratch:
    """One test's private clone of the template.

    Attributes:
        database: The scratch database name (dropped in the fixture's ``finally``).
        role: The unprivileged login role — cluster-wide, owned by the session fixture.
        owner_dsn: DSN of the provisioning role. It owns the tables and on a stock local
            cluster is a superuser, so it **bypasses RLS**. Use it only for DDL, seeding,
            and non-vacuity checks — never for an isolation assertion.
        app_dsn: DSN of the unprivileged role. Everything the tests do runs over this.
        protected: The tables carrying the tenant policy, copied from the template.
    """

    database: str
    role: str
    owner_dsn: str
    app_dsn: str
    protected: tuple[str, ...]


async def _assert_unprivileged(dsn: str, role: str) -> None:
    """Fail provisioning if the role the tests read as is exempt from row security.

    The single fact the whole suite rests on. ``SUPERUSER`` and ``BYPASSRLS`` each skip
    RLS outright, so a suite run under either passes with every policy dropped. Asserted
    rather than assumed, and asserted over the very DSN the tests use.

    Args:
        dsn: The DSN handed to the tests.
        role: The role name that DSN is expected to connect as.

    Raises:
        RuntimeError: If the connecting role is not ``role``, or is RLS-exempt.
    """
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as conn:
            name, superuser, bypass = (
                await conn.execute(
                    text(
                        "SELECT rolname, rolsuper, rolbypassrls "
                        "FROM pg_roles WHERE rolname = current_user"
                    )
                )
            ).one()
    finally:
        await engine.dispose()
    if str(name) != role or bool(superuser) or bool(bypass):
        raise RuntimeError(
            f"the aegis test role is {name!r} (expected {role!r}) with "
            f"superuser={bool(superuser)} bypassrls={bool(bypass)}; every tenant "
            "isolation assertion in this suite would pass vacuously"
        )


async def _drop(dsn: str, *, databases: tuple[str, ...] = (), roles: tuple[str, ...] = ()) -> None:
    """Drop scratch databases and roles, reporting anything left behind.

    Databases first: a role cannot be dropped while privileges are still recorded
    against objects in a database that exists. Failures are collected rather than raised
    at the first one, so every drop is attempted — a leaked scratch database is bad, and
    one hidden behind an earlier exception is worse.

    Args:
        dsn: The provisioning DSN.
        databases: Database names to drop.
        roles: Role names to drop.

    Raises:
        RuntimeError: If anything could not be dropped, naming what leaked.
    """
    engine = create_async_engine(dsn, isolation_level="AUTOCOMMIT")
    leaked: list[str] = []
    try:
        async with engine.connect() as conn:
            for database in databases:
                try:
                    await conn.execute(
                        text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
                    )
                except SQLAlchemyError as exc:
                    leaked.append(f"database {database}: {exc}")
            for role in roles:
                try:
                    await conn.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
                except SQLAlchemyError as exc:
                    leaked.append(f"role {role}: {exc}")
    finally:
        await engine.dispose()
    if leaked:
        raise RuntimeError("scratch cleanup left objects behind — " + "; ".join(leaked))


async def _provision_template(dsn: str) -> PostgresTemplate:
    """Create the unprivileged role and the template database the whole session clones.

    The order is the security-relevant part: ``create_all`` and :func:`bootstrap_rls` run
    as the **owner**, the DML grants are then issued explicitly (the scratch role owns
    nothing, so it starts with no access at all), and only then is the unprivileged DSN
    handed to a test. Grants live in the template's own catalog, so every clone inherits
    them without a second round of DDL.

    Args:
        dsn: DSN of a role allowed to ``CREATE ROLE`` and ``CREATE DATABASE``.

    Returns:
        The provisioned :class:`PostgresTemplate`.
    """
    suffix = uuid.uuid4().hex[:12]
    database = f"aegis_tmpl_{suffix}"
    role = f"aegis_test_{suffix}"
    # Hex only, so inlining it in the (non-parameterisable) CREATE ROLE utility statement
    # cannot terminate the literal.
    password = secrets.token_hex(16)
    owner_dsn = make_url(dsn).set(database=database).render_as_string(hide_password=False)

    admin = create_async_engine(dsn, isolation_level="AUTOCOMMIT")
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
                await conn.run_sync(AegisBase.metadata.create_all)
            protected = tuple(await bootstrap_rls(owner))
            async with owner.begin() as conn:
                for statement in (
                    f'GRANT USAGE ON SCHEMA public TO "{role}"',
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                    f'IN SCHEMA public TO "{role}"',
                    # ``UPDATE`` is one privilege more than production's serving role
                    # gets (``grant_serving_role`` issues ``USAGE, SELECT``), and it is
                    # here for one reason: the governance seeding helper inserts parent
                    # rows with explicit ids and then ``setval``s the identity sequence
                    # past them, which needs UPDATE. Nothing in ``aegis`` calls
                    # ``setval``, and the privilege has no bearing on row security — the
                    # NOSUPERUSER/NOBYPASSRLS attributes are what make this role an
                    # honest stand-in for the serving one.
                    f'GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public '
                    f'TO "{role}"',
                ):
                    await conn.execute(text(statement))
        finally:
            # Disposed before the first clone: ``CREATE DATABASE … TEMPLATE`` refuses to
            # run while any other session is connected to the template.
            await owner.dispose()
    except Exception:
        # Never leak a template database or a role because setup failed half-way.
        await _drop(dsn, databases=(database,), roles=(role,))
        raise

    return PostgresTemplate(
        admin_dsn=dsn,
        database=database,
        role=role,
        password=password,
        protected=protected,
    )


async def _clone(template: PostgresTemplate) -> PostgresScratch:
    """Clone the template into a fresh, uuid-named database for one test.

    Args:
        template: The session-wide template.

    Returns:
        The per-test :class:`PostgresScratch` handle.
    """
    database = f"aegis_test_{uuid.uuid4().hex[:12]}"
    url = make_url(template.admin_dsn)
    admin = create_async_engine(template.admin_dsn, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(
                text(f'CREATE DATABASE "{database}" TEMPLATE "{template.database}"')
            )
    finally:
        await admin.dispose()
    return PostgresScratch(
        database=database,
        role=template.role,
        owner_dsn=url.set(database=database).render_as_string(hide_password=False),
        app_dsn=url.set(
            database=database, username=template.role, password=template.password
        ).render_as_string(hide_password=False),
        protected=template.protected,
    )


@pytest.fixture(scope="session")
def pg_template() -> Iterator[PostgresTemplate]:
    """Provision the session's template database and unprivileged role, then destroy them.

    Synchronous, driving :func:`asyncio.run` itself, so each async test still builds its
    engines inside its own function-scoped event loop — there is no session-scoped loop to
    keep in step with pytest-asyncio.

    Yields:
        The :class:`PostgresTemplate`. Skips (or fails, under
        :data:`REQUIRE_ENV`) when no PostgreSQL can be provisioned.
    """
    dsn = admin_dsn()
    try:
        template = asyncio.run(_provision_template(dsn))
    except (OSError, SQLAlchemyError) as exc:
        skip_or_fail(f"{type(exc).__name__}: {exc}")
        raise  # unreachable: skip_or_fail always raises
    try:
        asyncio.run(
            _assert_unprivileged(
                make_url(template.admin_dsn)
                .set(
                    database=template.database,
                    username=template.role,
                    password=template.password,
                )
                .render_as_string(hide_password=False),
                template.role,
            )
        )
        yield template
    finally:
        asyncio.run(
            _drop(dsn, databases=(template.database,), roles=(template.role,))
        )


@pytest.fixture
def pg_scratch(pg_template: PostgresTemplate) -> Iterator[PostgresScratch]:
    """Give one test its own clone of the template, and drop it afterwards.

    Args:
        pg_template: The session-wide template fixture.

    Yields:
        The per-test :class:`PostgresScratch` handle.
    """
    scratch = asyncio.run(_clone(pg_template))
    try:
        yield scratch
    finally:
        asyncio.run(_drop(pg_template.admin_dsn, databases=(scratch.database,)))


@pytest_asyncio.fixture
async def pg_engine(pg_scratch: PostgresScratch) -> AsyncEngine:
    """An engine connected as the **unprivileged** role — the one tests must use.

    Every RLS policy the template carries is enforced against this connection, which is
    the entire reason the suite moved off SQLite.

    Args:
        pg_scratch: This test's scratch database.

    Yields:
        A disposed-on-teardown :class:`~sqlalchemy.ext.asyncio.AsyncEngine`.
    """
    engine = create_async_engine(pg_scratch.app_dsn)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def pg_owner_engine(pg_scratch: PostgresScratch) -> AsyncEngine:
    """An engine connected as the table **owner** — for DDL and non-vacuity reads only.

    On a stock local cluster this role is a superuser, so it bypasses every policy. That
    is exactly what a catalog read-back or a "both tenants' rows really are there" check
    needs, and exactly what an isolation assertion must never use.

    Args:
        pg_scratch: This test's scratch database.

    Yields:
        A disposed-on-teardown :class:`~sqlalchemy.ext.asyncio.AsyncEngine`.
    """
    engine = create_async_engine(pg_scratch.owner_dsn)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def pg_sessionmaker(pg_engine: AsyncEngine) -> async_sessionmaker:
    """A sessionmaker over the unprivileged engine — the base every suite's ``db`` uses.

    Args:
        pg_engine: The unprivileged engine for this test.

    Returns:
        An ``async_sessionmaker`` with ``expire_on_commit=False``, matching how the
        application's own session factory is configured.
    """
    return async_sessionmaker(pg_engine, expire_on_commit=False)
