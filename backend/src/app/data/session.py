"""Async engine, session factory and bootstrap for the Postgres store.

The engine and session factory are created lazily and cached so that merely
importing this module never opens a connection or requires the ``asyncpg`` driver
to be installed — important because the unit tests run against an in-memory
SQLite database (via :func:`configure_engine`) with no Postgres present.

Verified against: SQLAlchemy 2.0.x asyncio API (``create_async_engine`` +
``async_sessionmaker``), August 2026.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from aegis.governance.rls import bootstrap_rls as _aegis_bootstrap_rls

# ``set_tenant_scope`` now lives in ``aegis.governance.rls`` (the RLS seam); re-export it
# under its historical name so ``app.data.governance`` / the orchestrator are unchanged.
from aegis.governance.rls import set_tenant_scope  # noqa: F401 - re-exported for importers
from aegis.governance.schema import (
    SchemaDriftError,  # noqa: F401 - re-exported so a host can catch it by name
    reconcile_additive_columns,
)
from sqlalchemy import text
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
    """Return the process-wide async engine, creating it on first use."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(to_asyncpg_dsn(get_settings().postgres_dsn))
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide async session factory, creating it on first use."""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _sessionmaker


def configure_engine(engine: AsyncEngine) -> None:
    """Install a pre-built engine (used by tests to bind an in-memory SQLite DB).

    Args:
        engine: An ``AsyncEngine`` to use for all subsequent sessions.
    """
    global _engine, _sessionmaker
    _engine = engine
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

    Delegates to :func:`aegis.governance.rls.bootstrap_rls` (the RLS policy on
    ``users`` / ``usage_ledger`` / ``approvals``, failing closed on an unset GUC).
    Postgres-only and idempotent; a no-op on other dialects.

    Args:
        engine: Engine to configure; defaults to the process-wide engine.
    """
    await _aegis_bootstrap_rls(engine or get_engine())


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

    Then the tenant Row-Level Security policies are installed (a no-op elsewhere).

    Args:
        engine: Engine to bootstrap; defaults to the process-wide engine.

    Raises:
        SchemaDriftError: If a live table is missing a column that cannot be added
            additively. This is deliberately fatal — see
            :func:`aegis.governance.schema.reconcile_additive_columns`.
    """
    engine = engine or get_engine()
    # Import the memory + governance models so their tables register on the aegis data
    # metadata before create_all. They live in ``aegis.memory.stores`` /
    # ``aegis.governance.models`` (re-exported by the ``app.memory.stores`` /
    # ``app.data.models`` shims) and register on ``aegis.data.AegisBase`` — a separate
    # metadata from the platform's ``app.data`` Base — so both must be created.
    import aegis.governance.models  # noqa: F401,PLC0415 - registration side-effect only
    import aegis.ops.models  # noqa: F401,PLC0415 - registration side-effect only
    from aegis.data import AegisBase  # noqa: PLC0415 - local to avoid an import-time dep

    import app.memory.stores  # noqa: F401,PLC0415 - registration side-effect only

    metadatas = (Base.metadata, AegisBase.metadata)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(AegisBase.metadata.create_all)
        await reconcile_additive_columns(conn, metadatas)
        await _align_timestamp_columns(conn, metadatas)
    await bootstrap_rls(engine)
