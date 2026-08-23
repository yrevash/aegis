"""The durable Postgres checkpoint store, usable from *both* halves of this app.

Why this module exists at all — the bug it fixes
------------------------------------------------
``langgraph-checkpoint-postgres`` ships two savers and neither one, on its own, can
serve this codebase:

- ``PostgresSaver`` implements only the **sync** protocol. Its ``aget_tuple`` /
  ``aput`` / ``alist`` are the inherited ``BaseCheckpointSaver`` stubs, which
  ``raise NotImplementedError``. The orchestrator drives every run with
  ``graph.astream(...)``, and ``AsyncPregelLoop.__aenter__`` calls
  ``await checkpointer.aget_tuple(...)`` as its very first act — so selecting
  ``AGENT_CHECKPOINTER=postgres`` used to blow up on the first token of the first run.
- ``AsyncPostgresSaver`` implements the async protocol, but its **sync** entry points
  deliberately ``raise asyncio.InvalidStateError`` when called from the saver's own
  event loop. ``aegis.agent.orchestrator`` calls the sync ``graph.get_state(config)``
  from inside ``async def`` bodies in three places (read the final state, decide
  whether a parked run is resumable), so that saver breaks the resume path instead.

:class:`HybridPostgresSaver` is the small thing that was missing: the sync
``PostgresSaver`` (so every sync ``get_state`` / ``get_state_history`` call works
unchanged) with the async half implemented by handing the same sync call to a worker
thread. The blocking psycopg work therefore never runs on the event loop, and both
call styles hit one Postgres-backed store.

Concurrency
-----------
``PostgresSaver`` guards every cursor with a single ``threading.Lock``, so checkpoint
operations serialise no matter how many connections are available. We still open a
``ConnectionPool`` rather than one bare connection: a pool re-opens a connection the
server dropped, which is what a long-lived singleton in a demo box actually needs.

Verified against langgraph 1.2.x / langgraph-checkpoint-postgres 3.1.x, August 2026.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    DeltaChannelHistory,
)
from langgraph.checkpoint.postgres import PostgresSaver

logger = logging.getLogger(__name__)

__all__ = ["HybridPostgresSaver", "build_postgres_checkpointer", "close_checkpointer"]


class HybridPostgresSaver(PostgresSaver):
    """A ``PostgresSaver`` whose async protocol is the sync one, off the event loop.

    Every override is the same statement: run the inherited **sync** method in a
    worker thread via :func:`asyncio.to_thread`. Nothing about how a checkpoint is
    written or read changes — this only makes the async half of
    ``BaseCheckpointSaver`` exist, which is the half LangGraph's async Pregel loop
    calls and the stock ``PostgresSaver`` leaves raising ``NotImplementedError``.
    """

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        """Fetch one checkpoint tuple without blocking the event loop."""
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,  # noqa: A002 - LangGraph's parameter name
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        """Stream checkpoints for a thread, newest first.

        The whole page is materialised inside the worker thread rather than yielding
        row-by-row across the thread boundary: the sync iterator holds an open cursor
        (and the saver's lock) for as long as it lives, and suspending that inside an
        ``async for`` would pin both across arbitrary awaits.
        """
        rows = await asyncio.to_thread(
            lambda: list(self.list(config, filter=filter, before=before, limit=limit))
        )
        for row in rows:
            yield row

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Persist one checkpoint without blocking the event loop."""
        return await asyncio.to_thread(
            self.put, config, checkpoint, metadata, new_versions
        )

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Persist one task's pending writes without blocking the event loop."""
        await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        """Delete every checkpoint for a thread without blocking the event loop."""
        await asyncio.to_thread(self.delete_thread, thread_id)

    async def acopy_thread(self, source_thread_id: str, target_thread_id: str) -> None:
        """Fork a thread's checkpoints without blocking the event loop."""
        await asyncio.to_thread(self.copy_thread, source_thread_id, target_thread_id)

    async def adelete_for_runs(self, run_ids: Sequence[str]) -> None:
        """Delete checkpoints for the given runs without blocking the event loop."""
        await asyncio.to_thread(self.delete_for_runs, run_ids)

    async def aprune(
        self, thread_ids: Sequence[str], *, strategy: str = "keep_latest"
    ) -> None:
        """Prune a thread's checkpoint history without blocking the event loop."""
        await asyncio.to_thread(self.prune, thread_ids, strategy=strategy)

    async def aget_delta_channel_history(
        self, *, config: RunnableConfig, channels: Sequence[str]
    ) -> Mapping[str, DeltaChannelHistory]:
        """Read delta-channel history without blocking the event loop."""
        return await asyncio.to_thread(
            lambda: self.get_delta_channel_history(config=config, channels=channels)
        )


# The durable saver is a process-wide singleton: its pool stays open for the app's
# lifetime so every compiled graph shares one checkpoint store.
_saver: HybridPostgresSaver | None = None
_pool: Any = None


#: The DML the serving role needs on LangGraph's checkpoint tables. It never owns
#: them, so the owner connection grants these after the migration — same shape as
#: ``aegis.governance.rls.grant_serving_role`` does for the app's own tables.
_SERVING_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"

#: A plain SQL identifier. A role name that does not match is refused rather than
#: interpolated into DDL (mirrors ``aegis.governance.rls._SAFE_ROLE_NAME``).
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _migrate_and_grant(admin_dsn: str, serving_role: str | None) -> None:
    """Create the checkpoint tables on the **owner** connection and grant the app role.

    This repo has no Alembic: ``app.data.session.bootstrap`` is the schema owner and it
    runs its DDL on the separate owner/admin engine (``POSTGRES_ADMIN_DSN``) because the
    serving role deliberately owns nothing. LangGraph's checkpoint tables get the same
    treatment — ``setup()`` is LangGraph's own idempotent migrator, so it plays exactly
    the role ``create_all`` + the additive reconciler play for the app's tables, and it
    runs here on the owner connection followed by an explicit grant.

    Without the grant, a fresh box gets ``permission denied for table checkpoints`` on
    the first checkpoint write — mid-run, as a 500 — rather than at boot.

    Args:
        admin_dsn: The owner/DDL DSN.
        serving_role: The role the request path connects as, or ``None`` when there is
            no owner/serving split (that role owns the tables and needs no grant).
    """
    from psycopg import connect

    with connect(admin_dsn, autocommit=True) as conn:
        HybridPostgresSaver(conn).setup()
        if serving_role and _SAFE_IDENTIFIER.match(serving_role):
            schema = conn.execute("SELECT current_schema()").fetchone()[0]
            if _SAFE_IDENTIFIER.match(str(schema)):
                for table in (
                    "checkpoints",
                    "checkpoint_blobs",
                    "checkpoint_writes",
                    "checkpoint_migrations",
                ):
                    conn.execute(
                        f'GRANT {_SERVING_TABLE_PRIVILEGES} ON "{schema}"."{table}" '
                        f'TO "{serving_role}"'
                    )
        elif serving_role:
            logger.error(
                "Refusing to grant checkpoint tables to role %r: not a plain SQL "
                "identifier. Grant it by hand.",
                serving_role,
            )


def build_postgres_checkpointer(
    dsn: str, *, admin_dsn: str | None = None, serving_role: str | None = None
) -> HybridPostgresSaver:
    """Build (once) the durable saver bound to ``dsn``, schema created.

    Migrates the checkpoint tables via :func:`_migrate_and_grant` (on ``admin_dsn``
    when it is a different connection, else on ``dsn`` itself), then opens a psycopg
    ``ConnectionPool`` for the serving path in ``autocommit`` mode with
    ``prepare_threshold=0`` — LangGraph's documented requirement, because the saver
    issues the same statements against pooled connections and a server-side prepared
    statement is scoped to whichever connection first saw it.

    Args:
        dsn: The serving libpq DSN, normally ``settings.postgres_dsn``.
        admin_dsn: The owner/DDL DSN (``settings.admin_dsn``). Defaults to ``dsn``.
        serving_role: The role in ``dsn``, granted DML on the tables the owner created.
            ``None`` when owner and serving are the same role.

    Returns:
        The process-wide :class:`HybridPostgresSaver`.
    """
    global _saver, _pool
    if _saver is not None:
        return _saver

    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    _migrate_and_grant(admin_dsn or dsn, serving_role)

    pool = ConnectionPool(
        conninfo=dsn,
        min_size=1,
        max_size=4,
        # ``open=True`` connects eagerly so a bad DSN fails at boot, where it is
        # readable, rather than on the first checkpoint write mid-run.
        open=True,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
    )
    saver = HybridPostgresSaver(pool)
    _pool = pool
    _saver = saver
    logger.info("Durable agent checkpointer ready (Postgres, checkpoint tables ensured)")
    return saver


def close_checkpointer() -> None:
    """Close the durable saver's pool and drop the singleton (shutdown / tests)."""
    global _saver, _pool
    if _pool is not None:
        try:
            _pool.close()
        except Exception:  # noqa: BLE001 - shutdown must not raise
            logger.warning("Checkpointer pool did not close cleanly", exc_info=True)
    _pool = None
    _saver = None
