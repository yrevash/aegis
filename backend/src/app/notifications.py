"""The notification fan-out — one Redis subscription per process, many SSE streams.

This module is the *push* half of the alert subsystem. The durable half is
:class:`app.data.models.Notification` and :func:`app.data.notifications.emit`; nothing
here is a system of record and nothing here is allowed to become one. A frame that this
bus drops costs a connected user a live toast; the row is already committed, and their
next ``GET /v1/notifications`` shows it. That asymmetry is what lets every failure path
below degrade instead of raising.

Why Redis pub/sub and not an in-process queue
---------------------------------------------

The thing that emits the headline notification is not the thing that serves the stream.
``job.succeeded`` is written by a Temporal activity (:func:`app.jobs.activities.finish_ingest`)
and the SSE connection waiting for it is held by a request handler. In this deployment
both happen to live in one interpreter — the worker runs as an asyncio task inside the
API process (see ``app.main.lifespan``) — so an in-process broadcast would appear to
work perfectly, right up until somebody runs ``python -m app.jobs.worker`` separately or
starts ``uvicorn --workers 2``. At that point the alert would be written correctly,
published to nobody, and the bell would only light up on a page refresh: a bug that
looks like flakiness rather than like a missing transport.

So the transport is Redis ``PUBLISH``/``SUBSCRIBE`` on :data:`CHANNEL`, and Redis is
already a hard dependency of this platform (the semantic cache, the answer cache, the
gateway's fleet limiter and the guardrail cache all require it).

**One subscription per process, not one per stream.** Every SSE connection gets an
:class:`asyncio.Queue`; a single reader task holds the one Redis subscription and fans
each message out to those queues. The alternative — a Redis connection per open browser
tab — spends a pooled connection on an idle socket and is the standard way an SSE
feature takes a Redis instance down at a demo.

The failure posture, stated rather than assumed
------------------------------------------------

Redis being unreachable must not silently turn this into "notifications sometimes
arrive". Two rules:

* **The mode is a fact the process reports.** :attr:`NotificationBus.mode` is
  ``"redis"`` or ``"in-process"``, it is logged at WARNING the moment it degrades (not
  at DEBUG, and not once per message), and ``GET /v1/notifications/stream`` sends it as
  the stream's opening ``ready`` event. An operator watching a stream can see which
  transport is behind it without reading a log.
* **In-process is a real fallback, not a stub.** When Redis cannot be reached the bus
  keeps delivering to the queues in *this* interpreter, which is exactly right for the
  single-process deployment and is honestly insufficient for a multi-process one. It
  says so, in one line, naming the consequence.

A publish is never awaited by the thing being reported on: :func:`app.data.notifications.emit`
calls :meth:`NotificationBus.publish` inside its own ``try``, and this method swallows
its own transport errors on top of that. Two layers, because an alert about a finished
ingest that *failed the ingest* would be worse than no alert at all.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "CHANNEL",
    "NotificationBus",
    "get_bus",
    "reset_bus",
]

#: The Redis pub/sub channel every Aegis process publishes notifications on. A constant
#: rather than a setting: the deployment already isolates itself with a logical Redis
#: database (``REDIS_URL=redis://localhost:6379/1`` in ``.env.run1``), so a second knob
#: would only create a way for a publisher and a subscriber to be pointed at different
#: channels while both look configured.
CHANNEL = "aegis:notifications"

#: How many frames a single slow SSE consumer may fall behind before the bus starts
#: dropping *its* frames. Bounded on purpose: an abandoned browser tab whose TCP window
#: has closed would otherwise pin every notification ever published in this process's
#: heap. Dropping is safe here in a way it is not for the row — see the module docstring.
_QUEUE_DEPTH = 256


class NotificationBus:
    """Fan notification envelopes out to every SSE stream in this process.

    Not a singleton by construction — :func:`get_bus` holds the process-wide one — so a
    test can build an isolated bus and drive both ends of it without touching Redis.
    """

    def __init__(self, *, redis_url: str | None = None) -> None:
        """Build an unstarted bus.

        Args:
            redis_url: The Redis URL to subscribe on. ``None`` means "in-process only",
                which is what the unit tests use and what a lite deployment gets.
        """
        self._redis_url = redis_url
        self._queues: set[asyncio.Queue[str]] = set()
        self._redis: Any = None
        self._pubsub: Any = None
        self._reader: asyncio.Task[None] | None = None
        self._mode = "in-process"
        self._started = False
        self._lock = asyncio.Lock()

    @property
    def mode(self) -> str:
        """``"redis"`` while the cross-process transport is live, else ``"in-process"``.

        Read by ``GET /v1/notifications/stream``, which sends it on the opening frame so
        the answer to "is this stream cross-process?" is visible to whoever is watching
        the stream rather than only to whoever is reading the logs.
        """
        return self._mode

    @property
    def subscribers(self) -> int:
        """How many SSE streams this process is currently fanning out to."""
        return len(self._queues)

    async def start(self) -> None:
        """Connect and subscribe, or degrade to in-process delivery and say so.

        Idempotent and safe to call from anywhere — the API's lifespan calls it at boot,
        and :meth:`publish` / :meth:`subscribe` call it lazily so a worker process that
        never runs the FastAPI lifespan still gets the cross-process transport.

        Never raises. A Redis that is down at boot leaves the process delivering
        in-process, with one WARNING naming exactly what that costs.
        """
        async with self._lock:
            if self._started:
                return
            self._started = True
            if not self._redis_url:
                logger.info(
                    "Notification bus started in-process (no REDIS_URL configured). "
                    "Alerts are durable in Postgres either way; live frames reach only "
                    "the SSE streams held by THIS process."
                )
                return
            try:
                import redis.asyncio as redis  # noqa: PLC0415 - lazy: keeps unit tests infra-free

                self._redis = redis.from_url(self._redis_url, decode_responses=True)
                self._pubsub = self._redis.pubsub()
                await self._pubsub.subscribe(CHANNEL)
            except Exception:  # noqa: BLE001 - any transport failure degrades, none raises
                logger.warning(
                    "Notification bus could NOT reach Redis at %s, so it is running "
                    "in-process only: a notification written by another process (a "
                    "standalone Temporal worker, a second uvicorn worker) will NOT "
                    "reach an SSE stream served by this one. The rows are still durable "
                    "in Postgres, so a reader sees them on their next GET "
                    "/v1/notifications — they just will not arrive live. Channel: %s.",
                    self._redis_url,
                    CHANNEL,
                    exc_info=True,
                )
                await self._teardown_redis()
                return
            self._reader = asyncio.create_task(self._read_forever(), name="notification-bus")
            self._mode = "redis"
            logger.info(
                "Notification bus subscribed to Redis channel %r at %s; live frames "
                "cross process boundaries.",
                CHANNEL,
                self._redis_url,
            )

    async def stop(self) -> None:
        """Cancel the reader, close the Redis connection, and wake every subscriber."""
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader
            self._reader = None
        await self._teardown_redis()
        self._started = False
        self._mode = "in-process"

    async def _teardown_redis(self) -> None:
        """Close whatever half-built Redis objects exist, swallowing close errors."""
        for closer in (self._pubsub, self._redis):
            if closer is None:
                continue
            with contextlib.suppress(Exception):
                await closer.aclose()
        self._pubsub = None
        self._redis = None

    async def _read_forever(self) -> None:
        """Drain the Redis subscription into every local queue until cancelled.

        A failure here is the one that matters most, because it is invisible: the
        publisher keeps succeeding and the subscribers simply stop hearing anything. So
        the handler does not merely log — it flips :attr:`mode` back to ``in-process``,
        which changes what :meth:`publish` does (local delivery resumes) and what a new
        stream's opening frame says. Degrading loudly and *completely* is the point;
        half-degrading is how you get "notifications only sometimes arrive".
        """
        assert self._pubsub is not None  # noqa: S101 - only reachable from start()
        try:
            async for message in self._pubsub.listen():
                if message.get("type") != "message":
                    continue
                data = message.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8", "replace")
                if isinstance(data, str):
                    self._fan_out(data)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - see the docstring: degrade, never die silently
            logger.warning(
                "Notification bus lost its Redis subscription; falling back to "
                "in-process delivery. Cross-process alerts will not arrive live until "
                "this process is restarted with Redis reachable.",
                exc_info=True,
            )
            self._mode = "in-process"

    def _fan_out(self, payload: str) -> None:
        """Put one serialised envelope on every subscriber's queue, dropping if full.

        Synchronous and non-blocking by construction: ``put_nowait`` on a bounded queue.
        A slow consumer loses frames instead of stalling the reader that serves every
        other consumer — the one place in this subsystem where dropping data is correct,
        because the row it describes is already committed.
        """
        for queue in list(self._queues):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                logger.warning(
                    "An SSE notification stream is more than %d frames behind; dropping "
                    "this one for that consumer. It will still see the row on its next "
                    "GET /v1/notifications.",
                    _QUEUE_DEPTH,
                )

    async def publish(self, envelope: dict[str, Any]) -> None:
        """Broadcast one envelope. Never raises, whatever the transport does.

        Called only after the row is committed (see :func:`app.data.notifications.emit`),
        so there is nothing here whose failure could cost durability.

        In ``redis`` mode the envelope goes to Redis and comes back to this process
        through :meth:`_read_forever` — deliberately *not* also delivered locally, which
        would give every stream in the publishing process two copies of every frame.

        Args:
            envelope: ``{"tenant_id": ..., "user_id": ..., "row": {...}}``. The scoping
                fields ride alongside the wire row rather than inside it, because the
                row is the public :class:`app.api.routes_notifications.NotificationRow`
                contract and must not grow a ``tenant_id`` the frontend could key on.
        """
        await self.start()
        payload = json.dumps(envelope, separators=(",", ":"))
        if self._mode == "redis" and self._redis is not None:
            try:
                await self._redis.publish(CHANNEL, payload)
                return
            except Exception:  # noqa: BLE001 - a publish failure degrades this frame only
                logger.warning(
                    "Notification publish to Redis failed; delivering to this process's "
                    "streams only. The row is committed and will be read on next load.",
                    exc_info=True,
                )
        self._fan_out(payload)

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[str]]:
        """Yield a queue that receives every published envelope while held.

        **No replay.** The queue starts empty and carries only what is published while
        the caller holds it; the backlog is the frontend's separate
        ``GET /v1/notifications`` call. Mixing the two would mean deciding, in the
        stream, how far back "everything" goes — and getting it wrong in the direction
        that re-toasts a week of alerts on every reconnect.
        """
        await self.start()
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=_QUEUE_DEPTH)
        self._queues.add(queue)
        try:
            yield queue
        finally:
            self._queues.discard(queue)


_bus: NotificationBus | None = None


def get_bus() -> NotificationBus:
    """Return the process-wide bus, building it from settings on first use.

    Lazy for the same reason :func:`app.data.session.get_engine` is: importing this
    module must not open a socket, or the unit suite would need a Redis.
    """
    global _bus
    if _bus is None:
        from app.config import get_settings  # noqa: PLC0415 - lazy: no import-time settings read

        settings = get_settings()
        url = settings.redis_url if settings.stores_enabled else None
        _bus = NotificationBus(redis_url=url)
    return _bus


def reset_bus() -> None:
    """Drop the cached bus so the next access rebuilds it (test isolation).

    Does **not** stop it: a caller that wants the reader task gone awaits
    :meth:`NotificationBus.stop` first. Silently cancelling a task from a
    non-async helper is how a suite acquires a "Task was destroyed but it is pending".
    """
    global _bus
    _bus = None
