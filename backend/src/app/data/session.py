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
    bind_scope_for_session,  # noqa: F401 - re-exported for importers
    bootstrap_login_functions,
    configure_rls,
    grant_serving_role,
    install_scope_auditor,
    report_rls_enforcement,
    rls_fail_closed,  # noqa: F401 - re-exported for importers
    set_tenant_scope,  # noqa: F401 - re-exported for importers
)
from aegis.governance.rls import bootstrap_rls as _aegis_bootstrap_rls
from aegis.governance.schema import (
    SchemaDriftError,
    reconcile_additive_columns,
    reconcile_enum_values,
)
from sqlalchemy import make_url, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.exc import TimeoutError as SATimeoutError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool

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


class PoolExhaustedError(SATimeoutError):
    """A connection pool ran out, and this says which one and what to do about it.

    Subclasses ``sqlalchemy.exc.TimeoutError`` (itself a ``SQLAlchemyError``), so every
    existing ``except SQLAlchemyError`` handler keeps catching it — this replaces a
    diagnostic, it does not add a new failure mode.

    The one it replaces read ``QueuePool limit of size 5 overflow 10 reached, connection
    timed out, timeout 30.00``, arrived thirty seconds late, and named neither the engine
    nor the setting that would fix it. On a demo, thirty silent seconds is
    indistinguishable from a hang, and a hang is diagnosed by restarting the process —
    which clears the evidence.
    """


def _pool_class(label: str, env_prefix: str) -> type[AsyncAdaptedQueuePool]:
    """Build a pool class that names itself when it runs out.

    A subclass rather than an event listener because there is no event for "the checkout
    timed out": SQLAlchemy raises from inside ``_do_get``, and that is the only place the
    pool's own status is in scope.

    Args:
        label: Which engine this pool belongs to, for the message.
        env_prefix: The settings prefix whose ``_POOL_SIZE`` / ``_MAX_OVERFLOW`` the
            operator would raise.

    Returns:
        An ``AsyncAdaptedQueuePool`` subclass. One per engine, because the label has to
        live somewhere and a class attribute is the only thing the pool machinery will
        carry for us.
    """

    class _NamedPool(AsyncAdaptedQueuePool):
        def _do_get(self) -> Any:  # noqa: ANN401 - _ConnectionRecord, kept loose
            try:
                return super()._do_get()
            except SATimeoutError as exc:
                message = (
                    f"The {label} Postgres pool is exhausted: {self.status()}. Every "
                    f"connection is checked out and none was returned within "
                    f"{self._timeout:.0f}s, so this request was refused rather than "
                    f"left to stall. Either raise {env_prefix}_POOL_SIZE / "
                    f"{env_prefix}_MAX_OVERFLOW (and re-check the total against the "
                    f"cluster's max_connections — app.data.session.pool_budget does "
                    f"that arithmetic), or find what is holding connections open: "
                    f"SELECT state, count(*), max(now() - state_change) FROM "
                    f"pg_stat_activity GROUP BY state;"
                )
                logger.critical("%s", message)
                raise PoolExhaustedError(message) from exc

    _NamedPool.__name__ = f"{label.capitalize()}Pool"
    return _NamedPool


#: The settings prefix an operator would raise, per engine. Explicit rather than derived
#: from the label: the console engine is built in another module, and a rule that read
#: ``"DB" if not admin`` sent its reader to a variable that does not exist.
_ENV_PREFIX: dict[str, str] = {
    "serving": "DB",
    "admin": "DB_ADMIN",
    "console": "AEGIS_DB_CONSOLE",
}


def pool_kwargs(dsn: str, *, label: str, size: int, overflow: int) -> dict[str, Any]:
    """Return the ``create_async_engine`` pool arguments for one engine.

    Public because there are **three** engines and only two of them are built here: the
    §7.9 SQL console builds its own, in :mod:`app.api.routes_db`, and building it with
    bare ``create_async_engine`` is what left it on SQLAlchemy's defaults
    (``pool_size=5, max_overflow=10, pool_timeout=30``) — fifteen unbudgeted connections
    and the thirty-second undiagnosed stall §9.4 exists to remove, on the one engine an
    operator reaches for when the platform is already misbehaving.

    Empty for a non-PostgreSQL URL: SQLite (in-process, in tests) has its own pool
    classes, and forcing a queue pool onto it is how a test suite acquires a mysterious
    "database is locked".

    Args:
        dsn: The URL the engine will be built from.
        label: Which engine this is — ``serving``, ``admin`` or ``console``. Names the
            pool in :class:`PoolExhaustedError` and selects the settings prefix.
        size: Steady-state connections held open.
        overflow: Extra connections allowed under burst, closed again afterwards.

    Returns:
        Keyword arguments for ``create_async_engine``.
    """
    if not dsn.startswith(("postgresql:", "postgresql+")):
        return {}
    settings = get_settings()
    return {
        "poolclass": _pool_class(label, _ENV_PREFIX.get(label, "DB")),
        "pool_size": size,
        "max_overflow": overflow,
        "pool_timeout": settings.db_pool_timeout_seconds,
        "pool_recycle": settings.db_pool_recycle_seconds,
        # A connection the server closed under us is otherwise discovered by the query
        # that fails on it — one round trip per checkout buys "the pool never hands out a
        # corpse", which is worth it for a platform that idles between demos.
        "pool_pre_ping": True,
    }


#: The name this helper had while only this module called it. Kept so the existing
#: pool tests import the same object the engines are built with.
_pool_kwargs = pool_kwargs


def get_engine() -> AsyncEngine:
    """Return the process-wide **serving** engine, creating it on first use.

    Built from ``POSTGRES_DSN``, which must name a role with neither ``SUPERUSER`` nor
    ``BYPASSRLS`` so the tenant RLS policies actually apply to it. Every request-path
    session comes from here; DDL does not (see :func:`get_admin_engine`).

    Two things are installed on it here and nowhere else:

    * **The pool is sized** (§9.4) — see :attr:`app.config.Settings.db_pool_size` for the
      arithmetic, and :class:`PoolExhaustedError` for what exhaustion now says.
    * **The scope auditor** (§9.5) — :func:`aegis.governance.rls.install_scope_auditor`,
      which names every statement that reads a tenant-scoped table with no scope bound.
      Only on the serving engine: the owner engine's DDL and grants are not tenant reads,
      and a diagnostic that fires at every boot is one nobody reads.
    """
    global _engine
    if _engine is None:
        settings = get_settings()
        dsn = to_asyncpg_dsn(settings.postgres_dsn)
        configure_rls(fail_closed=settings.rls_fail_closed)
        _engine = create_async_engine(
            dsn,
            **pool_kwargs(
                dsn,
                label="serving",
                size=settings.db_pool_size,
                overflow=settings.db_max_overflow,
            ),
        )
        if settings.rls_scope_audit:
            install_scope_auditor(_engine, strict=settings.rls_scope_audit_strict)
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
        settings = get_settings()
        dsn = to_asyncpg_dsn(settings.admin_dsn)
        _admin_engine = create_async_engine(
            dsn,
            **pool_kwargs(
                dsn,
                label="admin",
                size=settings.db_admin_pool_size,
                overflow=settings.db_admin_max_overflow,
            ),
        )
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
    # The scope auditor goes on here too, not only in :func:`get_engine` — and that is
    # the whole reason it can produce an enumeration. The suites bind their scratch
    # database through this function and never call ``get_engine``, so an auditor
    # installed only there would watch an engine no test uses and report a clean run
    # over a codebase full of unscoped readers.
    settings = get_settings()
    if settings.rls_scope_audit:
        install_scope_auditor(engine, strict=settings.rls_scope_audit_strict)


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

    **Which flavour of the predicate gets installed is decided here**, immediately before
    the DDL, and that is not tidiness. ``configure_rls`` was called in :func:`get_engine`
    only, while ``bootstrap`` runs on the *owner* engine and startup reaches it before
    anything builds the serving one — so the policy was installed from the module default
    and ``RLS_FAIL_CLOSED=true`` silently installed the fail-**open** predicate. The
    setting appeared to be honoured (the boot log even said which flavour it was
    installing) while the control it names was off: the worst possible shape for a
    security posture. Setting it here means the installed policy always matches the
    configured posture, whatever order the engines happen to be built in.

    Args:
        engine: Engine to configure; defaults to the process-wide **owner/DDL** engine,
            since ``ALTER TABLE``/``CREATE POLICY`` require ownership of the tables and
            the serving role deliberately owns nothing.
    """
    configure_rls(fail_closed=get_settings().rls_fail_closed)
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


async def pool_budget(engine: AsyncEngine | None = None) -> tuple[int, int] | None:
    """Check that the configured pools fit inside the cluster's ``max_connections``.

    The arithmetic in :attr:`app.config.Settings.db_pool_size` is written down once; this
    is the part that checks it against the database actually in front of us, because the
    number it is checked against is a property of the *cluster*, not of this repository.
    A deployment that lowered ``max_connections``, or added a second Aegis process, is
    exactly the case a comment cannot catch.

    Logged, never raised. Over-subscription is not a fault at boot — it is a fault only
    when every pool fills at once, and refusing to start would turn a capacity warning
    into an outage.

    Args:
        engine: A connection to the cluster to ask. Defaults to the serving engine.
            ``bootstrap`` passes its own, deliberately: reaching for the process-wide
            serving engine from inside a call that was handed an engine is how a check
            ends up connecting somewhere the caller never asked about — and on a suite
            that binds a scratch database per test, somewhere that no longer exists.

    Returns:
        ``(ceiling, requested)`` — the cluster's ``max_connections`` less its superuser
        reservation, and the maximum this process can hold open. ``None`` when the
        engine is not PostgreSQL or the cluster could not be asked.
    """
    engine = engine or get_engine()
    if engine.dialect.name != "postgresql":
        return None
    settings = get_settings()
    # All THREE engines, not two. The console's pool was omitted here while the comment
    # on ``Settings.db_pool_size`` counted it, so the one function whose job is this
    # arithmetic was the one place it was wrong — and it was wrong in the reassuring
    # direction. Counted whether or not the console is switched on: a budget that changes
    # when a feature flag flips is a budget nobody can check against a cluster.
    requested = (
        settings.db_pool_size
        + settings.db_max_overflow
        + settings.db_admin_pool_size
        + settings.db_admin_max_overflow
        + settings.db_console_pool_size
        + settings.db_console_max_overflow
    )
    try:
        async with engine.connect() as conn:
            limit = int(
                (await conn.execute(text("SHOW max_connections"))).scalar_one()
            )
            reserved = int(
                (
                    await conn.execute(text("SHOW superuser_reserved_connections"))
                ).scalar_one()
            )
    except (SQLAlchemyError, OSError, ValueError):
        logger.warning(
            "Could not read max_connections, so the %d connections this process may "
            "hold open are UNCHECKED against the cluster's ceiling.",
            requested,
            exc_info=True,
        )
        return None
    ceiling = limit - reserved
    if requested >= ceiling:
        logger.error(
            "Postgres pool budget does not fit: this process may hold %d connections "
            "(serving %d+%d, admin %d+%d, console %d+%d) against a cluster ceiling of "
            "%d (max_connections %d less %d reserved). Lower DB_POOL_SIZE / "
            "DB_MAX_OVERFLOW, or raise max_connections. Nothing else may connect to "
            "this cluster — psql, Superset and LightRAG included.",
            requested,
            settings.db_pool_size,
            settings.db_max_overflow,
            settings.db_admin_pool_size,
            settings.db_admin_max_overflow,
            settings.db_console_pool_size,
            settings.db_console_max_overflow,
            ceiling,
            limit,
            reserved,
        )
    else:
        logger.info(
            "Postgres pool budget: up to %d connections (serving %d+%d, admin %d+%d, "
            "console %d+%d) of a %d ceiling; %d left for everything else.",
            requested,
            settings.db_pool_size,
            settings.db_max_overflow,
            settings.db_admin_pool_size,
            settings.db_admin_max_overflow,
            settings.db_console_pool_size,
            settings.db_console_max_overflow,
            ceiling,
            ceiling - requested,
        )
    return ceiling, requested


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


async def _recreate_legacy_chunks(
    conn: Any,  # noqa: ANN401 - AsyncConnection, kept loose (no import-time asyncpg dep)
) -> None:
    """Drop a pre-tenancy ``chunks`` table so ``create_all`` can rebuild it correctly.

    ``chunks`` was declared with ``doc_id VARCHAR(255)`` and no ``tenant_id`` at all. Both
    columns of the replacement are ``NOT NULL`` foreign keys, which
    :func:`aegis.governance.schema.reconcile_additive_columns` cannot install additively —
    correctly, because for a table holding rows there is no right value to back-fill and
    only a human can choose one. The reconciler would therefore raise ``SchemaDriftError``
    on every boot against a database bootstrapped before this change, and the platform
    would not start.

    An **empty** legacy table has no such dilemma: there is nothing to back-fill, and
    recreating it is the migration. So this drops it, and drops it only when it is
    provably empty — a table with rows in it raises instead, naming what it holds. That
    asymmetry is the whole point: this function must never be able to become a silent
    ``DELETE`` of a tenant's corpus, and "it looked stale" is not evidence.

    Recognition is by the *legacy* column rather than by the absence of a new one, so it
    cannot mistake a half-created table for a legacy one. Runs before ``create_all``,
    which then materialises the current definition.

    Args:
        conn: An open (transactional) async connection on the owner engine.

    Raises:
        SchemaDriftError: If the legacy table still holds rows.
    """
    if conn.dialect.name != "postgresql":
        return
    legacy = (
        await conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = 'chunks' AND column_name = 'doc_id'"
            )
        )
    ).first()
    if legacy is None:
        return
    rows = (await conn.execute(text("SELECT count(*) FROM chunks"))).scalar_one()
    if rows:
        msg = (
            f"chunks still has the pre-tenancy 'doc_id' column and holds {rows} row(s). "
            "The replacement declares tenant_id and document_id NOT NULL, and neither "
            "can be back-filled for rows whose owning document was never recorded (the "
            "old doc_id is a string that does not join to documents.id). Export or "
            "delete those rows and re-ingest; refusing to drop a populated table."
        )
        logger.critical("%s", msg)
        raise SchemaDriftError(msg)
    await conn.execute(text("DROP TABLE chunks"))
    logger.info("dropped the empty pre-tenancy chunks table; create_all will rebuild it")


async def _retenant_prompt_version_indexes(
    conn: Any,  # noqa: ANN401 - AsyncConnection, kept loose (no import-time asyncpg dep)
) -> None:
    """Re-key ``prompt_versions``' indexes on the tenant (§7.7), on a live database.

    ``ux_prompt_version`` was ``UNIQUE (prompt_key, version)`` — one number line for a
    prompt key shared by every tenant on the platform. ``create_all`` will not replace it
    on a database that already has the table, and
    :func:`aegis.governance.schema.reconcile_additive_columns` is additive-columns-only
    by design, so this is the dedicated step (the pattern
    :func:`_recreate_legacy_chunks` set) that brings an existing database to the current
    definition.

    Why it matters rather than being tidy-up: with Row-Level Security on
    ``prompt_versions``, a tenant-scoped session computing ``max(version)`` sees only its
    own rows, allocates the next number, and collides with a row it cannot see. The
    insert retries with the same number and fails again — so the *second* tenant to write
    a prompt version could never write one at all.

    The new unique index is created **before** the old one is dropped, so a database
    whose rows somehow violate the tenant-scoped shape fails here, loudly, with the
    stricter constraint still in place rather than dropped.

    Args:
        conn: An open (transactional) async connection on the owner engine.
    """
    if conn.dialect.name != "postgresql":
        return
    table = (
        await conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = 'prompt_versions'"
            )
        )
    ).first()
    if table is None:
        return  # create_all is about to build it with the current definition
    await conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_prompt_version_tenant "
            "ON prompt_versions (coalesce(tenant_id, 0), prompt_key, version)"
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_prompt_tenant_key_status "
            "ON prompt_versions (tenant_id, prompt_key, status)"
        )
    )
    for stale in ("ux_prompt_version", "ix_prompt_key_status"):
        dropped = (
            await conn.execute(
                text(
                    "SELECT 1 FROM pg_indexes WHERE schemaname = current_schema() "
                    "AND tablename = 'prompt_versions' AND indexname = :name"
                ),
                {"name": stale},
            )
        ).first()
        if dropped is None:
            continue
        await conn.execute(text(f"DROP INDEX {stale}"))
        logger.info(
            "schema: dropped %s on prompt_versions — superseded by the tenant-scoped "
            "index (§7.7)",
            stale,
        )


#: The one-off back-fill of ``usage_ledger.run_id``, run exactly once — on the boot
#: that adds the column — and never again. See :func:`_backfill_usage_ledger_run_id`.
_BACKFILL_LEDGER_RUN_ID = """
WITH unambiguous AS (
    SELECT trace_id,
           min(run_id)    AS run_id,
           min(tenant_id) AS tenant_id
      FROM runs
     WHERE trace_id IS NOT NULL
     GROUP BY trace_id
    HAVING count(*) = 1
)
UPDATE usage_ledger u
   SET run_id = m.run_id
  FROM unambiguous m
 WHERE u.run_id IS NULL
   AND u.trace_id = m.trace_id
   AND u.tenant_id IS NOT DISTINCT FROM m.tenant_id
"""


async def _backfill_usage_ledger_run_id(
    conn: Any,  # noqa: ANN401 - AsyncConnection, kept loose (no import-time asyncpg dep)
) -> None:
    """Attribute historic ledger rows to a run, but only where that is a fact.

    Called by :func:`bootstrap` **only on the boot that actually adds
    ``usage_ledger.run_id``** (``reconcile_additive_columns`` reports what it added), so
    this is a one-off migration step and not a full-table ``UPDATE`` on every restart.

    The rule, and the three cases it deliberately leaves alone:

    * A ledger row whose ``trace_id`` matches **exactly one** ``runs`` row, of the same
      tenant, is that run's spend. Set it.
    * A ``trace_id`` matching **no** run — 173 of tenant 1's 1932 rows on ``taif_run1``,
      8.95% — belongs to no run: a job, an ingest pass, the chat endpoint, a platform
      probe. It stays ``NULL``, which reads as *not attributable* and is reported as its
      own bucket by ``analytics_spend_daily`` rather than being folded into anything.
    * A ``trace_id`` matching **more than one** run stays ``NULL`` too. Picking one would
      be a guess, and a guess written into a spend column is indistinguishable from a
      measurement afterwards.

    Historic rows therefore end up *partly* attributed, and that is the honest outcome:
    the ledger did not record the run at the time, and no back-fill can invent what was
    never observed. Only rows written after this migration carry attribution recorded at
    the chokepoint (:func:`aegis.gateway.llm._record_usage`).

    Args:
        conn: An open (transactional) async connection on the **owner** engine — it must
            see every tenant's rows, which is exactly what the owner connection does and
            a serving connection must not.
    """
    if conn.dialect.name != "postgresql":
        return
    result = await conn.execute(text(_BACKFILL_LEDGER_RUN_ID))
    matched = result.rowcount if result.rowcount is not None else -1
    remaining = (
        await conn.execute(text("SELECT count(*) FROM usage_ledger WHERE run_id IS NULL"))
    ).scalar_one()
    logger.info(
        "schema: back-filled usage_ledger.run_id on %s row(s) whose trace matched "
        "exactly one run; %s row(s) remain NULL and are reported as unattributed "
        "spend, never as zero.",
        matched,
        remaining,
    )


async def bootstrap(engine: AsyncEngine | None = None) -> None:
    """Create every table (relational + JSON embeddings-of-record).

    Embeddings persist as JSON (``jsonb`` on PostgreSQL, ``JSON`` on SQLite) — vector
    ANN search runs in the embedded vector store, so no pgvector extension (and no
    vector server) is required.

    ``create_all`` only ever *creates*; it never alters a table that already exists.
    With no Alembic in this project, this function is the schema owner, so the
    reconciliation steps a long-lived database needs run here, on PostgreSQL only:

    0a. :func:`aegis.governance.schema.reconcile_enum_values` — add any native-enum
       label the models declare and the live type lacks (``run_status.REJECTED`` was
       the first). On its own AUTOCOMMIT connection and before ``create_all``, because
       PostgreSQL will not let a label be *used* in the transaction that added it.
    0b. :func:`_retenant_prompt_version_indexes` — replace ``prompt_versions``' unique
       index on ``(prompt_key, version)`` with the tenant-scoped one (§7.7). Also before
       ``create_all``, which never touches an existing table's indexes.
    0. :func:`_recreate_legacy_chunks` — drop a ``chunks`` table left over from before
       the retrieval corpus was tenant-scoped, but only when it is provably empty. It is
       first because the other two cannot fix it: the replacement's ``tenant_id`` and
       ``document_id`` are ``NOT NULL`` foreign keys, which no additive ``ALTER`` can
       install onto an existing table.
    1. :func:`aegis.governance.schema.reconcile_additive_columns` — install any column
       the models declare that the live table lacks. This is what keeps the usage
       ledger writable after a column is added to
       :class:`aegis.governance.models.UsageLedger`; without it the ledger INSERT
       raises ``UndefinedColumn``, the gateway swallows it (usage recording is
       best-effort by design), the row is lost, and the USD budget caps computed by
       summing those rows quietly stop binding.
    1a. :func:`_backfill_usage_ledger_run_id` — **only** on the boot that step 1 adds
       ``usage_ledger.run_id``: attribute historic ledger rows to the run whose
       ``trace_id`` they uniquely match, and leave the rest ``NULL``. NULL is a fact
       ("this spend belongs to no run"), never a zero.
    1b. :func:`aegis.runs.partitions.ensure_run_event_partitions` — roll ``run_events``'
       monthly partitions forward to cover this month and next. The ``after_create`` hook
       seeds them only on the boot that creates the table, so without this a
       long-running database eventually has no partition for "now" — and a partitioned
       table with no covering partition rejects every write, which would take out the
       agent's durable run record and the ingest log together.
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
    import aegis.redteam.models  # noqa: F401,PLC0415 - registration side-effect only

    # Registers the run record's models **and** the ``after_create`` hook that gives
    # ``run_events`` its first monthly partitions (via ``aegis.runs.__init__``). A
    # partitioned table with no partitions rejects every write, so this import is not
    # merely about the table existing.
    import aegis.runs.models  # noqa: F401,PLC0415 - registration side-effect only
    import aegis.settings.models  # noqa: F401,PLC0415 - registration side-effect only
    import aegis.skills.models  # noqa: F401,PLC0415 - registration side-effect only
    from aegis.data import AegisBase  # noqa: PLC0415 - local to avoid an import-time dep
    from aegis.runs.partitions import (  # noqa: PLC0415 - local, same reason
        ensure_run_event_partitions,
    )

    import app.memory.stores  # noqa: F401,PLC0415 - registration side-effect only

    metadatas = (Base.metadata, AegisBase.metadata)
    # Step 0a, and it has to be here rather than inside the transaction below.
    # ``create_all`` emits ``CREATE TYPE`` once, on the boot that first creates a native
    # enum, and never again — so a member added to a Python enum (``RunStatus.REJECTED``)
    # exists in the models and not in any database built before it, and the first write
    # of that member fails with ``invalid input value for enum``. For ``run_status`` that
    # write is the run header, so the failure would land on the durable run record rather
    # than at startup, which is the shape of failure this whole bootstrap exists to
    # avoid. ``ALTER TYPE … ADD VALUE`` is additive, takes no table lock and cannot
    # invalidate a stored row; it runs on its OWN AUTOCOMMIT connection because
    # PostgreSQL forbids using a label in the transaction that added it (and, before 15,
    # forbids adding one inside a transaction block at all).
    async with engine.connect() as conn:
        await reconcile_enum_values(
            await conn.execution_options(isolation_level="AUTOCOMMIT"), metadatas
        )
    async with engine.begin() as conn:
        # Before create_all, not after: the current ``chunks`` definition cannot be
        # reconciled onto the pre-tenancy one, so the stale table has to go first.
        await _recreate_legacy_chunks(conn)
        # Also before create_all: an existing ``prompt_versions`` keeps whatever indexes
        # it was built with, and the one it was built with makes the platform's second
        # tenant unable to write a prompt version at all.
        await _retenant_prompt_version_indexes(conn)
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(AegisBase.metadata.create_all)
        # ``run_events`` is PARTITIONED BY RANGE (ts) and a partitioned table with no
        # partition covering "now" rejects **every** write. ``create_all``'s
        # ``after_create`` hook seeds the first two months, but only on the boot that
        # created the table: a database built in August and still serving in October has
        # nowhere to put a row, and the failure would land on the agent's durable run
        # record and the ingest log rather than at startup. Idempotent (CREATE TABLE IF
        # NOT EXISTS + drop-then-create policy DDL), a no-op off PostgreSQL, and it runs
        # on the owner connection this bootstrap already holds.
        await ensure_run_event_partitions(conn)
        added = await reconcile_additive_columns(conn, metadatas)
        if "usage_ledger.run_id" in added:
            # Gated on the column having just been created, so the back-fill is a
            # migration step that runs once rather than a full-table UPDATE on every
            # boot. See :func:`_backfill_usage_ledger_run_id` for what it refuses to
            # guess.
            await _backfill_usage_ledger_run_id(conn)
        await _align_timestamp_columns(conn, metadatas)
    serving_role = serving_role_name()
    if serving_role is not None:
        await grant_serving_role(engine, serving_role)
    await bootstrap_rls(engine)
    # §9.5. The one read that legitimately precedes a tenant. Installed on every boot,
    # not only under the fail-closed posture: a function that only exists when the flag
    # is already on cannot be tested by turning the flag on.
    await bootstrap_login_functions(engine, serving_role)
    # §9.4. The pool arithmetic, checked against the cluster it will actually run on —
    # asked over the engine this bootstrap was handed, not the process-wide one.
    await pool_budget(engine)
