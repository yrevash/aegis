"""Async engines, session factory and bootstrap for the Postgres store.

There are **two** engines, and the difference between them is a security boundary:

- :func:`get_engine` — the **serving** engine, built from ``POSTGRES_DSN``. Every
  request runs here, as a role that has neither ``SUPERUSER`` nor ``BYPASSRLS`` and so
  is genuinely subject to the ``tenant_isolation`` policies.
- :func:`get_admin_engine` — the **owner/DDL** engine, built from
  ``POSTGRES_ADMIN_DSN``. ``create_all``, the additive schema reconciler, the RLS
  bootstrap and the serving-role grants run here, and only here. Those steps must
  bypass RLS (they own the tables); putting them on a separate connection is what makes
  bypass a property of the *connection* rather than a rule request code is trusted to
  remember.

Why the split had to exist at all: Postgres skips row security **entirely** for a
superuser or a ``BYPASSRLS`` role, and ``FORCE ROW LEVEL SECURITY`` only removes the
table *owner's* exemption. Serving requests as ``postgres`` therefore left every policy
installed, visible in ``pg_policies``, and enforced against nobody.
:func:`verify_rls_enforcement` is the boot-time check that says so out loud when it
happens again.

With no ``POSTGRES_ADMIN_DSN`` set, the admin engine falls back to the serving DSN and a
single-DSN developer install behaves exactly as before — reported, never assumed (see
:func:`verify_rls_enforcement`).

The engines and session factory are created lazily and cached so that merely importing
this module never opens a connection or requires the ``asyncpg`` driver to be installed
— important because the unit tests run against an in-memory SQLite database (via
:func:`configure_engine`) with no Postgres present.

Verified against: SQLAlchemy 2.0.x asyncio API (``create_async_engine`` +
``async_sessionmaker``), August 2026.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

# ``set_tenant_scope`` now lives in ``aegis.governance.rls`` (the RLS seam); it is
# re-exported here under its historical name so ``app.data.governance`` / the
# orchestrator are unchanged, as is ``RlsBypassError`` so a host can catch it by name.
from aegis.governance.rls import (
    RlsBypassError,  # noqa: F401 - re-exported for importers
    RlsEnforcement,
    audit_rls_enforcement,
    grant_serving_role,
    report_rls_enforcement,
    set_tenant_scope,  # noqa: F401 - re-exported for importers
)
from aegis.governance.rls import bootstrap_rls as _aegis_bootstrap_rls
from aegis.governance.schema import (
    SchemaDriftError,  # noqa: F401 - re-exported so a host can catch it by name
    reconcile_additive_columns,
)
from sqlalchemy import make_url, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

from .models import Base

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_admin_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Agent checkpointer wiring — the durable checkpoint store shared by every
# compiled graph in this process. This is the seam that makes cross-worker
# parked-run resume REAL (finding #8): a run paused at the human gate checkpoints
# here, and a *fresh* graph (rebuilt on any worker) resumes it by ``thread_id``
# from this same store — no in-memory ``ParkedRun`` handle required.
# ─────────────────────────────────────────────────────────────────────────────
_agent_checkpointer: Any = None


def get_agent_checkpointer() -> Any:  # noqa: ANN401 - BaseCheckpointSaver, kept loose
    """Return the process-wide agent checkpointer (the durable checkpoint store).

    This is the **single** shared checkpoint store every compiled graph binds to, so a
    run paused at the human gate can be resumed by ``thread_id`` from a *different*
    compiled-graph instance than the one that parked it — the mechanism behind
    cross-worker parked-run resume (see :func:`app.agent.resume_parked_run`).

    - ``agent_checkpointer='postgres'`` → a durable ``PostgresSaver`` whose Postgres
      tables back the store, so the resume works across separate OS processes/workers.
      Built once and reused (the documented prod singleton).
    - Otherwise (``'memory'``, the lite/offline/test default) → a single **shared**
      :class:`~langgraph.checkpoint.memory.InMemorySaver`. Because it is a process-wide
      singleton (not a fresh saver per ``build_agent``), a graph rebuilt by a resumer
      shares the same in-RAM store and can rehydrate a parked run by ``thread_id`` —
      cross-worker resume within one process, exactly the path the tests exercise.

    Returns:
        A LangGraph checkpointer instance, built once then cached.
    """
    global _agent_checkpointer
    if _agent_checkpointer is None:
        _agent_checkpointer = _build_agent_checkpointer()
    return _agent_checkpointer


def _build_agent_checkpointer() -> Any:  # noqa: ANN401 - BaseCheckpointSaver
    """Build the configured checkpointer once (Postgres when enabled, else memory)."""
    kind = get_settings().agent_checkpointer.strip().lower()
    if kind in {"postgres", "postgresql", "pg"}:
        # Reuse the documented prod ``PostgresSaver`` singleton so the whole process
        # shares exactly one Postgres-backed checkpoint connection (lazy import keeps
        # the default memory path free of any Postgres dependency).
        from app.agent.graph import _build_postgres_checkpointer

        return _build_postgres_checkpointer()
    from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver()


def reset_agent_checkpointer() -> None:
    """Drop the cached checkpointer so the next access rebuilds it (test isolation)."""
    global _agent_checkpointer
    _agent_checkpointer = None


def to_asyncpg_dsn(dsn: str) -> str:
    """Rewrite a libpq DSN to use SQLAlchemy's async ``asyncpg`` driver.

    Args:
        dsn: A database URL, typically ``postgresql://...`` from settings.

    Returns:
        The same URL with an async driver prefix. Non-Postgres URLs (e.g. the
        SQLite URL used in tests) are returned unchanged.
    """
    if dsn.startswith("postgresql+"):
        return dsn
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    if dsn.startswith("postgres://"):
        return dsn.replace("postgres://", "postgresql+asyncpg://", 1)
    return dsn


def get_engine() -> AsyncEngine:
    """Return the process-wide **serving** engine, creating it on first use.

    Built from ``POSTGRES_DSN``, which must name a role with neither ``SUPERUSER`` nor
    ``BYPASSRLS`` so the tenant RLS policies actually apply to it. Every request-path
    session comes from here; DDL does not (see :func:`get_admin_engine`).
    """
    global _engine
    if _engine is None:
        _engine = create_async_engine(to_asyncpg_dsn(get_settings().postgres_dsn))
    return _engine


def get_admin_engine() -> AsyncEngine:
    """Return the process-wide **owner/DDL** engine, creating it on first use.

    Built from ``POSTGRES_ADMIN_DSN`` when set, and from ``POSTGRES_DSN`` when it is
    not — see :attr:`app.config.Settings.admin_dsn` for why that fallback is safe to
    keep and why it is never silent. This is the only engine that should run
    ``create_all``, :func:`aegis.governance.schema.reconcile_additive_columns`,
    :func:`bootstrap_rls` or :func:`aegis.governance.rls.grant_serving_role`: all four
    need privileges the serving role must not have.

    Returns:
        The owner engine. It is a *separate* engine (and connection pool) from
        :func:`get_engine` whenever the two DSNs differ, which is the point.
    """
    global _admin_engine
    if _admin_engine is None:
        _admin_engine = create_async_engine(to_asyncpg_dsn(get_settings().admin_dsn))
    return _admin_engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide async session factory, creating it on first use."""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _sessionmaker


def configure_engine(
    engine: AsyncEngine, *, admin_engine: AsyncEngine | None = None
) -> None:
    """Install a pre-built engine (used by tests to bind an in-memory SQLite DB).

    The admin/DDL engine defaults to the same object, because the caller that binds a
    single engine wants one database: on SQLite there are no roles and nothing to
    bypass, and a test that bound a serving engine only would otherwise have
    ``bootstrap`` quietly create its tables in a *different* database (the configured
    Postgres one) — the kind of split-brain that turns a green suite into a lie.

    Args:
        engine: An ``AsyncEngine`` to use for all subsequent sessions.
        admin_engine: The engine to run DDL on. Defaults to ``engine``; pass a distinct
            one only to exercise the owner/serving split itself.
    """
    global _engine, _admin_engine, _sessionmaker
    _engine = engine
    _admin_engine = admin_engine or engine
    _sessionmaker = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session (FastAPI dependency).

    Yields:
        An ``AsyncSession`` bound to the configured engine; it is closed (and any
        pending transaction rolled back) automatically when the request ends.
    """
    async with get_sessionmaker()() as session:
        yield session


async def bootstrap_rls(engine: AsyncEngine | None = None) -> None:
    """Enable Row-Level Security + a per-tenant policy on the tenant-scoped tables.

    Delegates to :func:`aegis.governance.rls.bootstrap_rls`, which installs the policy
    on every table registered in its ``_TENANT_SCOPED_TABLES`` — the governance tables,
    the six memory tables, the two LLM-Ops tables and this host's ``approvals`` inbox —
    and logs any live table with a ``tenant_id`` column that the registry has missed.
    Postgres-only and idempotent; a no-op on other dialects.

    Note the policy's unset-scope behaviour, because it is the opposite of what this
    docstring used to claim: an unset, empty or non-numeric ``app.tenant_id`` makes the
    predicate **stop restricting** — it fails *open*, deliberately, because the host
    reads ``users`` by username before any tenant is known and platform-admin surfaces
    span tenants. A row whose ``tenant_id`` column is NULL is the case that fails
    *closed* under a bound scope. See ``_TENANT_ISOLATION_PREDICATE`` in
    :mod:`aegis.governance.rls` for the full reasoning.

    Args:
        engine: Engine to configure; defaults to the process-wide **owner/DDL** engine,
            since ``ALTER TABLE``/``CREATE POLICY`` require ownership of the tables and
            the serving role deliberately owns nothing.
    """
    await _aegis_bootstrap_rls(engine or get_admin_engine())


def serving_role_name() -> str | None:
    """Return the role in ``POSTGRES_DSN``, or ``None`` when there is no split.

    ``None`` means "nothing to grant to": either the DSN carries no username (SQLite in
    tests), or the serving and owner DSNs name the same role, in which case that role
    owns the tables and already holds every privilege. It is *not* a healthy state —
    it is the state :func:`verify_rls_enforcement` reports on — but it is not a grant
    problem.

    Returns:
        The serving role's name when it differs from the owner's, else ``None``.
    """
    settings = get_settings()
    serving = make_url(settings.postgres_dsn).username
    owner = make_url(settings.admin_dsn).username
    if not serving or serving == owner:
        return None
    return serving


async def verify_rls_enforcement(*, fatal: bool | None = None) -> RlsEnforcement | None:
    """Check at boot that the serving role is actually subject to the RLS policies.

    Asks the database (:func:`aegis.governance.rls.audit_rls_enforcement`) whether
    ``current_user`` on the **serving** engine holds ``SUPERUSER`` or ``BYPASSRLS``. If
    it does, Postgres skips row security for it and all thirteen ``tenant_isolation``
    policies are decorative — the exact condition this platform shipped with, and the
    reason this check exists rather than a comment saying "remember not to".

    **Why it is fatal outside dev.** ``fatal`` defaults to "not a dev environment",
    mirroring :meth:`app.config.Settings.ensure_secure_secrets`: a developer pointed at
    a local database as ``postgres`` gets a loud ERROR and keeps working, while a real
    deployment refuses to boot rather than serve tenants with its isolation control
    silently off. The asymmetry is deliberate — a check that blocks the dev loop gets
    disabled, and a warning that a production deployment scrolls past protects nobody.

    Only *connection* failures are caught here, and only to distinguish them from the
    verdict: an unreachable database is a fact about the network, and the platform is
    documented to start without one (lite mode). The bypass verdict itself is never
    inside a ``try`` — the previous diagnostic in this area was wrapped in a broad
    ``except`` and could not fire, which is why it never reported the thing it existed
    to catch.

    Args:
        fatal: Override the environment-derived posture. ``True`` raises on a bypassing
            role, ``False`` only logs; ``None`` (the default) means "fatal unless
            ``APP_ENV`` is dev".

    Returns:
        The :class:`~aegis.governance.rls.RlsEnforcement` verdict, or ``None`` when the
        probe could not be run (non-Postgres dialect, or the database was unreachable).

    Raises:
        RlsBypassError: When the check is fatal and the serving role bypasses RLS.
    """
    settings = get_settings()
    engine = get_engine()
    if engine.dialect.name != "postgresql":
        return None
    try:
        enforcement = await audit_rls_enforcement(engine)
    except (SQLAlchemyError, OSError):
        # Unreachable/unauthenticated database. Deliberately narrow: this branch must
        # never be able to swallow the bypass verdict, which is produced *after* it.
        logger.warning(
            "Could not verify RLS enforcement: the serving database is unreachable. "
            "Tenant isolation is UNVERIFIED for this boot.",
            exc_info=True,
        )
        return None
    if serving_role_name() is None:
        logger.warning(
            "No owner/serving role split: POSTGRES_ADMIN_DSN is unset or names the same "
            "role as POSTGRES_DSN, so DDL and request serving share one connection (%s). "
            "The split is what keeps RLS bypass out of the request path — run "
            "scripts/db-roles.sh (or scripts\\db-roles.ps1); see "
            "docs/operations/runbook.md § Database roles.",
            enforcement.role or "unknown role",
        )
    if fatal is None:
        fatal = not settings.is_dev
    report_rls_enforcement(enforcement, fatal=fatal)
    return enforcement


async def _align_timestamp_columns(
    conn: Any,  # noqa: ANN401 - AsyncConnection, kept loose (no import-time asyncpg dep)
    metadatas: tuple[Any, ...],
) -> None:
    """Convert pre-existing naive timestamp columns to ``timestamptz`` (PostgreSQL only).

    ``create_all`` is ``CREATE TABLE IF NOT EXISTS``: it never alters a table that
    already exists. A database bootstrapped before timestamps became
    :class:`aegis.data.UtcDateTime` therefore still carries ``TIMESTAMP WITHOUT TIME
    ZONE`` columns, and every aware-UTC bind from the application keeps failing
    (``asyncpg.exceptions.DataError``) — the SLA sweeper's crash-per-cycle. With no
    Alembic in this project, ``bootstrap`` is the schema owner, so the conversion lives
    here: idempotent (it only touches columns still reported naive by
    ``information_schema``), and a no-op on SQLite and on an already-converted database.

    Existing values are reinterpreted with ``AT TIME ZONE 'UTC'`` — the meaning the
    application already assigned to a stored naive timestamp everywhere it read one.

    Args:
        conn: An open (transactional) async connection.
        metadatas: The metadata objects whose ``UtcDateTime`` columns to align.
    """
    if conn.dialect.name != "postgresql":
        return
    from aegis.data import UtcDateTime  # noqa: PLC0415 - local, mirrors bootstrap's style

    wanted = {
        (table.name, column.name)
        for metadata in metadatas
        for table in metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, UtcDateTime)
    }
    if not wanted:
        return
    result = await conn.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND data_type = 'timestamp without time zone'"
        )
    )
    naive = {(row[0], row[1]) for row in result}
    for table_name, column_name in sorted(wanted & naive):
        # Identifiers come from our own declarative metadata, never from user input.
        await conn.execute(
            text(
                f'ALTER TABLE "{table_name}" ALTER COLUMN "{column_name}" '
                f"TYPE timestamptz USING \"{column_name}\" AT TIME ZONE 'UTC'"
            )
        )
        logger.info(
            "converted %s.%s to timestamptz (naive → UTC-aware)", table_name, column_name
        )


async def bootstrap(engine: AsyncEngine | None = None) -> None:
    """Create every table (relational + JSON embeddings-of-record).

    Embeddings persist as JSON (``jsonb`` on PostgreSQL, ``JSON`` on SQLite) — vector
    ANN search runs in the embedded vector store, so no pgvector extension (and no
    vector server) is required.

    ``create_all`` only ever *creates*; it never alters a table that already exists.
    With no Alembic in this project, this function is the schema owner, so the two
    reconciliation steps a long-lived database needs run here, on PostgreSQL only:

    1. :func:`aegis.governance.schema.reconcile_additive_columns` — install any column
       the models declare that the live table lacks. This is what keeps the usage
       ledger writable after a column is added to
       :class:`aegis.governance.models.UsageLedger`; without it the ledger INSERT
       raises ``UndefinedColumn``, the gateway swallows it (usage recording is
       best-effort by design), the row is lost, and the USD budget caps computed by
       summing those rows quietly stop binding.
    2. :func:`_align_timestamp_columns` — convert any timestamp column left naive by
       an earlier bootstrap to ``timestamptz``.

    Then the serving role is granted DML on whatever ``create_all`` just made
    (:func:`aegis.governance.rls.grant_serving_role`), and the tenant Row-Level Security
    policies are installed (both no-ops elsewhere). The grant belongs here for the same
    reason step 1 does: a table added on this boot is a table the serving role has no
    privileges on, and that failure would otherwise surface as a ``permission denied``
    500 inside a request instead of at startup.

    **This runs on the owner/DDL engine**, not the serving one — every step above needs
    ownership, and the serving role deliberately has none of it.

    Args:
        engine: Engine to bootstrap; defaults to the process-wide **owner/DDL** engine
            (:func:`get_admin_engine`).

    Raises:
        SchemaDriftError: If a live table is missing a column that cannot be added
            additively. This is deliberately fatal — see
            :func:`aegis.governance.schema.reconcile_additive_columns`.
    """
    engine = engine or get_admin_engine()
    # Import the memory + governance models so their tables register on the aegis data
    # metadata before create_all. They live in ``aegis.memory.stores`` /
    # ``aegis.governance.models`` (re-exported by the ``app.memory.stores`` /
    # ``app.data.models`` shims) and register on ``aegis.data.AegisBase`` — a separate
    # metadata from the platform's ``app.data`` Base — so both must be created.
    import aegis.governance.models  # noqa: F401,PLC0415 - registration side-effect only
    import aegis.jobs.models  # noqa: F401,PLC0415 - registration side-effect only
    import aegis.ops.models  # noqa: F401,PLC0415 - registration side-effect only

    # Registers the run record's models **and** the ``after_create`` hook that gives
    # ``run_events`` its first monthly partitions (via ``aegis.runs.__init__``). A
    # partitioned table with no partitions rejects every write, so this import is not
    # merely about the table existing.
    import aegis.runs.models  # noqa: F401,PLC0415 - registration side-effect only
    import aegis.settings.models  # noqa: F401,PLC0415 - registration side-effect only
    from aegis.data import AegisBase  # noqa: PLC0415 - local to avoid an import-time dep

    import app.memory.stores  # noqa: F401,PLC0415 - registration side-effect only

    metadatas = (Base.metadata, AegisBase.metadata)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(AegisBase.metadata.create_all)
        await reconcile_additive_columns(conn, metadatas)
        await _align_timestamp_columns(conn, metadatas)
    serving_role = serving_role_name()
    if serving_role is not None:
        await grant_serving_role(engine, serving_role)
    await bootstrap_rls(engine)
