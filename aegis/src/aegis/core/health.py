"""Per-dependency health probes for honest infra reporting.

Each probe reports the real reachability of a backend so ``/readyz`` and the UI
never guess. Probes accept an injected client/connection (tests pass a fake); in
production the real driver is reached through :func:`aegis.core.lazy.require`.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Literal

from pydantic import BaseModel

from aegis.core.lazy import require

logger = logging.getLogger(__name__)


async def _aclose(client: Any) -> None:  # noqa: ANN401 - any driver client
    """Close a probe-owned client, sync or async, without ever raising.

    Drivers disagree on the spelling (``aclose`` on modern redis-py, ``close`` on
    chromadb and older redis), and some return an awaitable. Closing is
    best-effort: a probe must report reachability, never fail because teardown did.
    """
    if client is None:
        return
    for name in ("aclose", "close"):
        closer = getattr(client, name, None)
        if closer is None:
            continue
        try:
            result = closer()
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 - teardown must never mask the probe result
            logger.debug("health probe: closing %r failed", type(client), exc_info=True)
        return


class DependencyStatus(BaseModel):
    """The observed status of one backing dependency."""

    name: str
    status: Literal["up", "down"]
    detail: str | None = None


async def probe_redis(url: str, *, client: Any | None = None) -> DependencyStatus:  # noqa: ANN401
    """Ping Redis and report whether it answered.

    Args:
        url: Redis URL (e.g., 'redis://localhost:6379').
        client: Optional injected Redis client for testing. If None, uses lazy loading.

    Returns:
        DependencyStatus with status "up" or "down".
    """
    owned = None
    try:
        redis_client = client
        if redis_client is None:
            redis = require("aegis[redis]", "redis.asyncio")
            redis_client = owned = redis.from_url(url)
        await redis_client.ping()
        return DependencyStatus(name="redis", status="up")
    except Exception as exc:  # noqa: BLE001 - a probe reports failure, never raises
        return DependencyStatus(name="redis", status="down", detail=str(exc))
    finally:
        # Only close what this probe opened — an injected client belongs to the caller.
        # /readyz is polled, so a probe that leaks a connection per call exhausts the
        # pool it is supposed to be reporting on.
        await _aclose(owned)


async def probe_postgres(url: str, *, conn: Any | None = None) -> DependencyStatus:  # noqa: ANN401
    """Run ``SELECT 1`` against Postgres and report the result.

    Args:
        url: PostgreSQL connection string.
        conn: Optional injected connection object for testing. If None, uses lazy loading.

    Returns:
        DependencyStatus with status "up" or "down".
    """
    try:
        if conn is not None:
            await conn.execute("SELECT 1")
            return DependencyStatus(name="postgres", status="up")
        asyncpg = require("aegis[postgres]", "asyncpg")
        connection = await asyncpg.connect(url)
        try:
            await connection.execute("SELECT 1")
        finally:
            await connection.close()
        return DependencyStatus(name="postgres", status="up")
    except Exception as exc:  # noqa: BLE001 - a probe reports failure, never raises
        return DependencyStatus(name="postgres", status="down", detail=str(exc))


async def probe_vector_store(path: str, *, client: Any | None = None) -> DependencyStatus:  # noqa: ANN401
    """Open the embedded vector store on disk and report whether it answered.

    The vector store is the ANN engine behind retrieval + memory recall; in full mode it
    is a hard dependency, exactly like Postgres/Redis. Unlike those, it is **embedded**
    (Chroma's ``PersistentClient``), so there is no host to reach — the thing that can
    fail is the storage directory: missing, unwritable, or holding a database this build
    cannot open. The probe therefore opens the client at ``path`` and lists collections
    (the cheapest round-trip that actually touches the store) and reports ``up`` only on
    a real answer — never a silent in-RAM fallback.

    Args:
        path: Filesystem directory holding the embedded vector store.
        client: Optional injected client for testing. If None, uses lazy loading.

    Returns:
        DependencyStatus with status "up" (store usable) or "down" (unusable/error).
    """
    owned = None
    try:
        store_client = client
        if store_client is None:
            chromadb = require("aegis[retrieval]", "chromadb")
            store_client = owned = chromadb.PersistentClient(path=path)
        store_client.list_collections()
        return DependencyStatus(name="vector_store", status="up")
    except Exception as exc:  # noqa: BLE001 - a probe reports failure, never raises
        return DependencyStatus(name="vector_store", status="down", detail=str(exc))
    finally:
        # Only close what this probe opened — an injected client belongs to the caller.
        # /readyz is polled, so a probe that leaks a handle per call eventually exhausts
        # the file descriptors of the store it is supposed to be reporting on.
        await _aclose(owned)
