"""Per-dependency health probes for honest infra reporting.

Each probe reports the real reachability of a backend so ``/readyz`` and the UI
never guess. Probes accept an injected client/connection (tests pass a fake); in
production the real driver is reached through :func:`aegis.core.lazy.require`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from aegis.core.lazy import require


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
    try:
        redis_client = client
        if redis_client is None:
            redis = require("aegis[redis]", "redis.asyncio")
            redis_client = redis.from_url(url)
        await redis_client.ping()
        return DependencyStatus(name="redis", status="up")
    except Exception as exc:  # noqa: BLE001 - a probe reports failure, never raises
        return DependencyStatus(name="redis", status="down", detail=str(exc))


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


async def probe_qdrant(url: str, *, client: Any | None = None) -> DependencyStatus:  # noqa: ANN401
    """Reach the Qdrant vector DB and report whether it answered.

    Qdrant is the ANN engine behind retrieval + memory recall; in full mode it is a
    hard dependency, exactly like Postgres/Redis. The probe lists the collections
    (the cheapest authenticated round-trip) and reports ``up`` only on a real answer —
    never a silent embedded fallback.

    Args:
        url: Qdrant server URL (e.g. 'http://localhost:6333').
        client: Optional injected Qdrant client for testing. If None, uses lazy loading.

    Returns:
        DependencyStatus with status "up" (node reachable) or "down" (unreachable/error).
    """
    try:
        qdrant_client = client
        if qdrant_client is None:
            qdrant = require("aegis[retrieval]", "qdrant_client")
            qdrant_client = qdrant.QdrantClient(url=url)
        qdrant_client.get_collections()
        return DependencyStatus(name="qdrant", status="up")
    except Exception as exc:  # noqa: BLE001 - a probe reports failure, never raises
        return DependencyStatus(name="qdrant", status="down", detail=str(exc))
