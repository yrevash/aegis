"""Async engine, session factory and bootstrap for the Postgres/pgvector store.

The engine and session factory are created lazily and cached so that merely
importing this module never opens a connection or requires the ``asyncpg`` driver
to be installed — important because the unit tests run against an in-memory
SQLite database (via :func:`configure_engine`) with no Postgres present.

Verified against: SQLAlchemy 2.0.x asyncio API (``create_async_engine`` +
``async_sessionmaker``), August 2026.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from aegis.governance.rls import bootstrap_rls as _aegis_bootstrap_rls

# ``set_tenant_scope`` now lives in ``aegis.governance.rls`` (the RLS seam); re-export it
# under its historical name so ``app.data.governance`` / the orchestrator are unchanged.
from aegis.governance.rls import set_tenant_scope  # noqa: F401 - re-exported for importers
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

from .models import Base

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


async def bootstrap(engine: AsyncEngine | None = None) -> None:
    """Create every table, enabling the ``vector`` extension first on Postgres.

    The extension must exist before ``create_all`` so the pgvector column type
    can be resolved. On non-Postgres dialects (the SQLite test database) the
    extension step is skipped. On PostgreSQL, tenant Row-Level Security policies are
    installed after table creation (a no-op elsewhere).

    Args:
        engine: Engine to bootstrap; defaults to the process-wide engine.
    """
    engine = engine or get_engine()
    # Import the memory + governance models so their tables register on the aegis data
    # metadata before create_all. They live in ``aegis.memory.stores`` /
    # ``aegis.governance.models`` (re-exported by the ``app.memory.stores`` /
    # ``app.data.models`` shims) and register on ``aegis.data.AegisBase`` — a separate
    # metadata from the platform's ``app.data`` Base — so both must be created.
    import aegis.governance.models  # noqa: F401,PLC0415 - registration side-effect only
    from aegis.data import AegisBase  # noqa: PLC0415 - local to avoid an import-time dep

    import app.memory.stores  # noqa: F401,PLC0415 - registration side-effect only

    async with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(AegisBase.metadata.create_all)
    await bootstrap_rls(engine)
