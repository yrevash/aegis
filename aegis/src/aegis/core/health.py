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


async def probe_pgvector(url: str, *, conn: Any | None = None) -> DependencyStatus:  # noqa: ANN401
    """Check that the ``vector`` extension is installed in Postgres.

    Args:
        url: PostgreSQL connection string.
        conn: Optional injected connection object for testing. If None, uses lazy loading.

    Returns:
        DependencyStatus with status "up" (extension present) or "down" (missing or error).
    """
    query = "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
    try:
        if conn is not None:
            row = await conn.fetchrow(query)
            present = row is not None
        else:
            asyncpg = require("aegis[postgres]", "asyncpg")
            connection = await asyncpg.connect(url)
            try:
                present = await connection.fetchrow(query) is not None
            finally:
                await connection.close()
        return (
            DependencyStatus(name="pgvector", status="up", detail="extension present")
            if present
            else DependencyStatus(name="pgvector", status="down", detail="extension missing")
        )
    except Exception as exc:  # noqa: BLE001 - a probe reports failure, never raises
        return DependencyStatus(name="pgvector", status="down", detail=str(exc))
