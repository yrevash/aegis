"""The Temporal client singleton.

One client per process, built lazily from :class:`app.config.Settings`, because a
Temporal client owns a gRPC connection and a long-lived worker pool: constructing one per
call would open a connection per call and quietly exhaust the server's limits under any
real load.

Lazily, and behind a lock, for a reason worth stating rather than discovering. Two
coroutines that both find the cache empty would both dial the server, and the loser's
connection would be dropped without ever being closed — a leak that shows up only as a
slow climb in the dev server's RSS. :func:`get_temporal_client` therefore double-checks
the cache after acquiring the lock.

**Connection failures are not caught here.** The platform is documented to start without
its databases (lite mode), and a worker that cannot reach Temporal must say so at the
place it happens rather than degrade into a substrate that accepts jobs and runs none.
The lifespan supervises the worker task and logs its death loudly; the API keeps serving.
That is the honest posture: the substrate is *down*, and it is visible, rather than
present-looking and inert.
"""

from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client

from app.config import get_settings

logger = logging.getLogger(__name__)

__all__ = ["get_temporal_client", "reset_temporal_client", "set_temporal_client"]

_client: Client | None = None
_lock: asyncio.Lock | None = None


def _client_lock() -> asyncio.Lock:
    """Return the process-wide connect lock, creating it on first use.

    Built lazily rather than at import because an ``asyncio.Lock`` created at import time
    on Python's older loop semantics binds to whatever loop is current then — and this
    module is imported long before the API's loop exists.

    Returns:
        The lock guarding client construction.
    """
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


async def get_temporal_client() -> Client:
    """Return the process-wide Temporal client, connecting on first use.

    Returns:
        A connected :class:`temporalio.client.Client` on ``TEMPORAL_ADDRESS`` /
        ``TEMPORAL_NAMESPACE``.

    Raises:
        RuntimeError: Propagated from the SDK when the server is unreachable. Not caught
            — see the module docstring.
    """
    global _client
    if _client is not None:
        return _client
    async with _client_lock():
        if _client is None:
            settings = get_settings()
            _client = await Client.connect(
                settings.temporal_address, namespace=settings.temporal_namespace
            )
            logger.info(
                "Temporal client connected: %s (namespace %s)",
                settings.temporal_address,
                settings.temporal_namespace,
            )
    return _client


def set_temporal_client(client: Client | None) -> None:
    """Install a pre-built client as the process singleton.

    The seam that lets the durability tests drive the *shipped* worker bootstrap: a
    :class:`temporalio.testing.WorkflowEnvironment` hands out a client pointed at an
    ephemeral server on a random port, and installing it here means
    :func:`app.jobs.worker.run_workers` runs unmodified rather than being reimplemented
    in the test.

    Args:
        client: The client to cache, or ``None`` to clear it.
    """
    global _client
    _client = client


def reset_temporal_client() -> None:
    """Drop the cached client.

    Verified against ``temporalio`` 1.31: neither :class:`temporalio.client.Client` nor
    its ``ServiceClient`` exposes a ``close``. The connection lives in the Rust core and
    is released when the last Python handle is dropped, so "reset" is genuinely all there
    is to do — and saying that here is better than shipping an ``await client.close()``
    that would raise ``AttributeError`` the first time a lifespan called it.
    """
    set_temporal_client(None)
