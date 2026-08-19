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

#: Seconds a reachability probe waits on the Qdrant node before calling it down.
#: ``/readyz`` is polled, so a probe that hangs is a probe that stops answering — and a
#: readiness endpoint that stops answering is indistinguishable from a dead process.
_PROBE_TIMEOUT_SECONDS = 5


async def _aclose(client: Any) -> None:  # noqa: ANN401 - any driver client
    """Close a probe-owned client, sync or async, without ever raising.

    Drivers disagree on the spelling (``aclose`` on modern redis-py, ``close`` on
    qdrant_client and older redis), and some return an awaitable. Closing is
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


async def probe_vector_store(url: str, *, client: Any | None = None) -> DependencyStatus:  # noqa: ANN401
    """Ask the Qdrant node for its collections and report whether it answered.

    The vector store is the ANN engine behind retrieval + memory recall; in full mode it
    is a hard dependency, exactly like Postgres/Redis — and since §9.1 it is a *node*,
    not an embedded index, because an embedded one is single-process and that was the
    ceiling under ``uvicorn --workers 2``. So this probe is now a real reachability check
    against a real host, like :func:`probe_postgres` and :func:`probe_neo4j`: it lists
    collections (the cheapest round-trip that actually touches the store) and reports
    ``up`` only on a genuine answer — never on a URL that merely parsed.

    Args:
        url: The Qdrant node URL (e.g. ``http://localhost:6333``).
        client: Optional injected client for testing. If None, uses lazy loading.

    Returns:
        DependencyStatus with status "up" (node answered) or "down" (unreachable/error).
    """
    owned = None
    try:
        store_client = client
        if store_client is None:
            qdrant_client = require("aegis[retrieval]", "qdrant_client")
            store_client = owned = qdrant_client.QdrantClient(
                url=url, timeout=_PROBE_TIMEOUT_SECONDS
            )
        store_client.get_collections()
        return DependencyStatus(name="vector_store", status="up")
    except Exception as exc:  # noqa: BLE001 - a probe reports failure, never raises
        return DependencyStatus(name="vector_store", status="down", detail=str(exc))
    finally:
        # Only close what this probe opened — an injected client belongs to the caller.
        # /readyz is polled, so a probe that leaks a handle per call eventually exhausts
        # the sockets of the store it is supposed to be reporting on.
        await _aclose(owned)


async def probe_neo4j(
    uri: str,
    *,
    user: str = "",
    password: str = "",
    driver: Any | None = None,  # noqa: ANN401 - any driver object
) -> DependencyStatus:
    """Ask the Neo4j driver to verify connectivity and report the answer.

    The fourth probe, and the one that was missing while the graph arm was already a
    hard part of hybrid retrieval: with Neo4j down, retrieval keeps working on the
    vector and BM25 arms and every dashboard stayed green, so the degradation was
    invisible to everything except the answer quality.

    ``verify_connectivity()`` is the driver's own round trip — it dials the server and
    runs the handshake — so a ``up`` here is a real answer from a real database, not a
    URI that parsed. Nothing is written and no query is run: this is a reachability
    probe, and a probe with side effects is a probe nobody dares run often.

    Args:
        uri: The bolt URI (e.g. ``bolt://localhost:7687``).
        user: The username, for the basic-auth tuple.
        password: The password.
        driver: An optional injected driver for testing. When ``None`` the real driver
            is built through :func:`aegis.core.lazy.require` and closed again here.

    Returns:
        DependencyStatus with status "up" or "down".
    """
    owned = None
    try:
        graph_driver = driver
        if graph_driver is None:
            neo4j = require("aegis[retrieval]", "neo4j")
            graph_driver = owned = neo4j.GraphDatabase.driver(
                uri, auth=(user, password)
            )
        result = graph_driver.verify_connectivity()
        if inspect.isawaitable(result):
            await result
        return DependencyStatus(name="neo4j", status="up")
    except Exception as exc:  # noqa: BLE001 - a probe reports failure, never raises
        return DependencyStatus(name="neo4j", status="down", detail=str(exc))
    finally:
        # Only close what this probe opened; the sync driver's ``close`` is picked up by
        # ``_aclose`` exactly like Qdrant's, and an injected driver belongs to its owner.
        await _aclose(owned)
