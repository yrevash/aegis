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


# Tenant-scoped tables that carry a ``tenant_id`` column and therefore get a
# Postgres Row-Level Security policy filtering on the ``app.tenant_id`` GUC (§3.3,
# decision D3). App-level ``WHERE tenant_id = :ctx`` scoping is the belt-and-
# suspenders layer over these DB-enforced policies.
_RLS_TABLES = ("users", "usage_ledger", "approvals")


async def set_tenant_scope(session: AsyncSession, tenant_id: int | None) -> None:
    """Bind ``app.tenant_id`` for the session's connection so RLS policies apply.

    Applied inside the governed data-layer calls (H1) — the usage ledger, budget
    reads, user/usage listings, and the approvals inbox — so the bootstrapped
    per-tenant RLS policies engage on Postgres for every governed request. The
    app-level ``WHERE tenant_id = :ctx`` scoping remains the belt-and-suspenders
    layer over these DB-enforced policies (and the only layer on SQLite).

    RLS is **Postgres-only**: this emits ``SET app.tenant_id = '<id>'`` on PostgreSQL
    (a no-op ``RESET`` when the request is unscoped); on SQLite (the test database)
    it does nothing, since RLS and session GUCs are Postgres-only.

    Args:
        session: The async session whose connection to pin.
        tenant_id: The tenant to scope to, or ``None`` to clear the scope.
    """
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return
    if tenant_id is None:
        await session.execute(text("RESET app.tenant_id"))
    else:
        await session.execute(
            text("SET app.tenant_id = :tid"), {"tid": str(tenant_id)}
        )


async def bootstrap_rls(engine: AsyncEngine | None = None) -> None:
    """Enable Row-Level Security + a per-tenant policy on tenant-scoped tables.

    Postgres-only and idempotent: each table gets ``ENABLE ROW LEVEL SECURITY`` and a
    policy that admits a row only when its ``tenant_id`` matches the
    ``current_setting('app.tenant_id')`` GUC set per request by :func:`set_tenant_scope`
    (an unset/empty GUC admits nothing, failing closed). A no-op on other dialects.

    Args:
        engine: Engine to configure; defaults to the process-wide engine.
    """
    engine = engine or get_engine()
    if engine.dialect.name != "postgresql":
        return
    async with engine.begin() as conn:
        for table in _RLS_TABLES:
            await conn.execute(
                text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            )
            await conn.execute(text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
            await conn.execute(
                text(
                    f"CREATE POLICY tenant_isolation ON {table} USING "
                    "(tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
                    "::int)"
                )
            )


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
    # Import the memory models so their tables register on the aegis data metadata before
    # create_all. They now live in ``aegis.memory.stores`` (re-exported by the
    # ``app.memory.stores`` shim) and register on ``aegis.data.AegisBase`` — a separate
    # metadata from the platform's ``app.data`` Base — so both must be created.
    from aegis.data import AegisBase  # noqa: PLC0415 - local to avoid an import-time dep

    import app.memory.stores  # noqa: F401,PLC0415 - registration side-effect only

    async with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(AegisBase.metadata.create_all)
    await bootstrap_rls(engine)
