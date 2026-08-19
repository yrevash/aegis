"""The model-call concurrency limiter — one bound, held across the whole worker pool.

Five users asking four-agent questions is twenty simultaneous calls into one provider
fleet, and nothing in this gateway used to bound that number. This module is the bound.

Why it is not an ``asyncio.Semaphore``
--------------------------------------

Because there is more than one process. The API serves requests, ``python -m
app.jobs.worker`` runs activities, and both call :func:`aegis.gateway.complete` against
the same deployments. A semaphore in each of them is **N times the limit it appears to
be**, and that is strictly worse than having no limiter at all: a number that reads as a
fleet-wide guarantee and holds only per-process is a control that lies, and it lies in
the direction of a rate-limit failure during a demo.

So the shared implementation keeps its state where every process can see it —
:class:`RedisSlotLimiter`, a lease set in Redis — and the process-local implementation
(:class:`LocalSlotLimiter`) **says so in its** :attr:`~SlotLimiter.scope`. A host that
installs the local one in a multi-process deployment is making a claim this module will
not make on its behalf: ``scope`` is reported on the platform surface, and it reads
``process``, not ``fleet``.

How a leased slot survives a process that dies holding one
----------------------------------------------------------

A ``DECR``-on-release counter leaks a slot forever when the holder is SIGKILLed, and a
limiter that ratchets itself down to zero over a week of restarts is a worse outage than
the one it prevents. Each holder therefore takes a **lease**: a member in a sorted set,
scored with the Redis server's own clock, which the acquiring script drops once it is
older than ``lease_seconds``. Nothing has to notice the death and nothing has to be
repaired by hand.

The lease length is **derived, not chosen**: the gateway bounds every provider call with
its own ``timeout_seconds`` (:func:`aegis.gateway.llm._bounded_acompletion`), so a slot
that has been held for materially longer than one call timeout is held by something that
is no longer running. :func:`lease_seconds_for` writes that derivation down.

The clock is the *server's* (``TIME`` inside the script), never the caller's, so two boxes
whose clocks disagree do not disagree about whose lease has expired.

What happens when the shared store cannot be reached
----------------------------------------------------

The call proceeds, and the limiter says out loud that it did. Failing closed would turn a
Redis blip into a total model outage, which is a bigger failure than the one being
prevented; failing open *quietly* would leave the platform reporting a bound it was not
holding. So a store failure is logged at ERROR, counted in
:meth:`RedisSlotLimiter.status` as ``degraded``, and the ``scope`` that surface reports
becomes ``fleet (degraded)`` until a slot is successfully leased again.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

__all__ = [
    "LocalSlotLimiter",
    "NoSlotLimiter",
    "RedisSlotLimiter",
    "SlotLimiter",
    "SlotUnavailableError",
    "lease_seconds_for",
]

#: How long a lease outlives the call timeout it was derived from. A provider call is
#: already bounded by ``GatewayConfig.timeout_seconds``; the margin covers the retry the
#: gateway may make inside one logical call plus the time to write the ledger row, so an
#: ordinary slow call is never mistaken for a dead holder.
_LEASE_MARGIN = 2.0

#: How long a waiter sleeps between attempts. Short enough that a freed slot is picked up
#: without a human noticing, long enough that fifty waiters do not turn Redis into the
#: bottleneck they were queued to avoid. Jittered per attempt so a burst that arrived
#: together does not retry together forever.
_POLL_SECONDS = 0.025


def lease_seconds_for(hold_seconds: float) -> float:
    """Return the lease length implied by the longest a slot can be held.

    **Give this the worst-case hold, not the per-attempt timeout.**
    :func:`aegis.gateway.llm.max_call_hold_seconds` computes it: the slot wraps the outer
    backstop, which gives the primary deployment *and each fallback in the chain* its own
    timeout budget, so one logical call can hold a slot for three call timeouts on the
    shipped chains. A lease derived from one timeout is reaped mid-call, and the freed
    slot is handed to a second caller — the exact failure this function's derivation
    exists to prevent, reintroduced by feeding it the wrong number.

    Args:
        hold_seconds: The longest one call can hold the slot, from
            :func:`~aegis.gateway.llm.max_call_hold_seconds`.

    Returns:
        How long a slot may be held before the acquiring script treats its holder as
        dead. Derived rather than configured: a second number here could be set shorter
        than the call it is meant to outlast, which would hand the same slot to two
        callers and make the limit not a limit.
    """
    return max(hold_seconds, 1.0) * _LEASE_MARGIN


class SlotUnavailableError(RuntimeError):
    """No slot came free within the caller's wait budget.

    A refusal with a reason rather than an unbounded wait: a request that queues forever
    behind a saturated fleet is indistinguishable, to the person who made it, from one
    that was lost.

    Attributes:
        reason: One sentence naming the limit and how long the caller waited.
        limit: The fleet-wide limit in force.
        waited_seconds: How long the caller waited before giving up.
    """

    def __init__(self, reason: str, *, limit: int, waited_seconds: float) -> None:
        """Build the error with the facts a caller can render."""
        super().__init__(reason)
        self.reason = reason
        self.limit = limit
        self.waited_seconds = waited_seconds


@runtime_checkable
class SlotLimiter(Protocol):
    """The seam the gateway holds a slot through.

    Attributes:
        scope: What the limit actually bounds — ``"fleet"``, ``"process"`` or
            ``"unlimited"``. Reported verbatim on the platform surface, so an
            implementation that cannot hold a fleet-wide bound must not claim one.
    """

    scope: str

    def slot(self) -> Any:  # noqa: ANN401 - an async context manager
        """Return an async context manager held for the duration of one provider call."""
        ...

    def status(self) -> dict[str, Any]:
        """Return the limiter's live counters, for the platform surface."""
        ...


class NoSlotLimiter:
    """The default: no bound at all, and it says so.

    ``aegis.gateway`` is standalone and a library that silently rate-limited its
    embedder would be a surprise; every other injected hook in this package defaults to a
    documented no-op for the same reason. The honesty is in :attr:`scope`, which reads
    ``unlimited`` — a deployment reading that on its own platform surface knows exactly
    what it has.
    """

    scope = "unlimited"

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """Yield immediately — nothing is bounded."""
        yield

    def status(self) -> dict[str, Any]:
        """Return the (empty) counters of a limiter that limits nothing."""
        return {"scope": self.scope, "limit": None, "in_flight": None}


class LocalSlotLimiter:
    """A process-local bound, correct when the process is the whole deployment.

    Genuinely useful — a single-box demo runs the API and the worker in one process, and
    there this *is* the fleet — and genuinely narrower than it looks anywhere else. The
    distinction lives in :attr:`scope`, which is ``process``: this class never reports
    ``fleet``, so a two-process deployment that installed it cannot mistake what it has.
    """

    scope = "process"

    def __init__(self, limit: int) -> None:
        """Build the limiter.

        Args:
            limit: How many provider calls this process may have in flight.

        Raises:
            ValueError: If ``limit`` is below 1 — a limiter that admits nobody stalls
                every model call in the process with no diagnostic anywhere.
        """
        if limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit!r}")
        self._limit = limit
        self._semaphore = asyncio.Semaphore(limit)
        self._in_flight = 0
        self._waits = 0

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """Hold one of this process's slots for the duration of the block."""
        if self._semaphore.locked():
            self._waits += 1
        await self._semaphore.acquire()
        self._in_flight += 1
        try:
            yield
        finally:
            self._in_flight -= 1
            self._semaphore.release()

    def status(self) -> dict[str, Any]:
        """Return the live counters of the process-local bound."""
        return {
            "scope": self.scope,
            "limit": self._limit,
            "in_flight": self._in_flight,
            "waits": self._waits,
        }


@runtime_checkable
class RedisLike(Protocol):
    """The one Redis call this limiter needs, as ``redis.asyncio.Redis`` spells it."""

    async def eval(self, script: str, numkeys: int, *args: Any) -> Any:  # noqa: ANN401
        """Run ``script`` server-side against ``numkeys`` keys."""
        ...


#: Acquire one lease, atomically, against the *server's* clock.
#:
#: Three things happen in one round trip because they cannot be allowed to interleave
#: with another process's copy of themselves: expired leases are dropped, the survivors
#: are counted, and this caller is admitted only if the count leaves room. Split across
#: three client calls, two processes both read "three of four held" and both take the
#: fourth slot — which is the exact bug a limiter exists to prevent, reintroduced inside
#: the limiter.
_ACQUIRE_LUA = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local lease = tonumber(ARGV[2])
local token = ARGV[3]
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - lease)
local held = redis.call('ZCARD', key)
if held < limit then
  redis.call('ZADD', key, now, token)
  redis.call('PEXPIRE', key, math.ceil(lease * 2000))
  return 1
end
return 0
"""

#: Release this holder's lease, and only this holder's. ``ZREM`` by token rather than
#: "drop the oldest" so a caller that overran its lease and had it reaped cannot, on
#: finishing, delete a slot somebody else is legitimately holding.
_RELEASE_LUA = """
redis.call('ZREM', KEYS[1], ARGV[1])
return 1
"""


class RedisSlotLimiter:
    """The fleet-wide bound: leases in Redis, shared by every process that calls.

    The state is in Redis and not in this object, which is the whole point — two
    processes, two instances of this class, one limit. See the module docstring for the
    lease mechanics and for what happens when Redis cannot be reached.
    """

    scope = "fleet"

    def __init__(
        self,
        client: RedisLike | None = None,
        *,
        limit: int,
        lease_seconds: float,
        wait_seconds: float = 60.0,
        key: str = "aegis:gateway:slots",
        client_factory: Callable[[], RedisLike] | None = None,
    ) -> None:
        """Build the limiter over an async Redis client, or a factory for one.

        Args:
            client: The async Redis client (injected, so a test drives a real server
                rather than a fake whose ``EVAL`` semantics we would be inventing).
                Mutually exclusive with ``client_factory``.
            limit: How many provider calls the whole fleet may have in flight.
            lease_seconds: How long a slot may be held before its holder is presumed
                dead. Pass :func:`lease_seconds_for` of the gateway's call timeout.
            wait_seconds: How long a caller queues for a slot before
                :class:`SlotUnavailableError`.
            key: The sorted-set key the leases live in. One key per fleet; a second
                deployment sharing the Redis instance must pass its own.
            client_factory: Builds a client on demand. Preferred when the limiter is
                constructed at import time, because a Redis connection belongs to the
                event loop that opened its socket: a process that runs more than one loop
                — a test suite, a CLI calling ``asyncio.run`` twice, a worker restarted
                in-process — would otherwise await the first loop's transport from the
                second and get a failure that looks like Redis being down. The client is
                therefore cached **per loop** rather than per limiter.

        Raises:
            ValueError: If ``limit`` is below 1, or if neither a client nor a factory is
                given — a limiter with no way to reach its store would report ``fleet``
                and hold nothing.
        """
        if limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit!r}")
        if client is None and client_factory is None:
            raise ValueError(
                "RedisSlotLimiter needs a client or a client_factory; without one it "
                "would report scope='fleet' while holding no bound at all"
            )
        self._client = client
        self._client_factory = client_factory
        self._clients_by_loop: dict[int, RedisLike] = {}
        self._limit = limit
        self._lease = lease_seconds
        self._wait = wait_seconds
        self._key = key
        self._in_flight = 0
        self._waits = 0
        self._degraded = 0
        self._healthy = True

    @property
    def reported_scope(self) -> str:
        """The scope as the platform surface should render it, degradation included."""
        return self.scope if self._healthy else f"{self.scope} (degraded)"

    def _resolve_client(self) -> RedisLike:
        """Return the client for the *running* loop, building one if this loop has none.

        See ``client_factory`` on :meth:`__init__` for why the cache is keyed on the loop
        and not simply held as one attribute.
        """
        if self._client is not None:
            return self._client
        loop_key = id(asyncio.get_running_loop())
        client = self._clients_by_loop.get(loop_key)
        if client is None:
            client = self._client_factory()  # type: ignore[misc] - checked in __init__
            self._clients_by_loop[loop_key] = client
        return client

    async def _try_acquire(self, token: str) -> bool | None:
        """Ask Redis for a lease.

        Returns:
            ``True`` when the lease was granted, ``False`` when the fleet is full, and
            ``None`` when Redis could not answer at all — the third case is *not* folded
            into "full", because a full fleet must make the caller wait and an
            unreachable store must not.
        """
        try:
            granted = await self._resolve_client().eval(
                _ACQUIRE_LUA, 1, self._key, self._limit, self._lease, token
            )
        except Exception:  # noqa: BLE001 - any client/transport error means "no answer"
            self._degraded += 1
            if self._healthy:
                # Once per transition, not once per call: a Redis outage under load
                # would otherwise write the log that hides every other line in it.
                logger.error(
                    "The model-call limiter cannot reach its shared store, so the "
                    "fleet-wide bound of %d is NOT being held. Calls proceed and the "
                    "platform surface reports scope=%r until a lease succeeds again.",
                    self._limit,
                    f"{self.scope} (degraded)",
                    exc_info=True,
                )
            self._healthy = False
            return None
        if not self._healthy:
            logger.info("The model-call limiter reached its shared store again.")
        self._healthy = True
        return bool(granted)

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """Hold one fleet-wide slot for the duration of the block.

        Raises:
            SlotUnavailableError: If no slot came free within ``wait_seconds``.
        """
        token = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        started = loop.time()
        held = False
        waited = False
        while True:
            outcome = await self._try_acquire(token)
            if outcome is None:  # store unreachable: proceed, counted and logged
                break
            if outcome:
                held = True
                break
            if not waited:
                self._waits += 1
                waited = True
            elapsed = loop.time() - started
            if elapsed >= self._wait:
                raise SlotUnavailableError(
                    f"no model-call slot came free within {elapsed:.1f}s; the fleet "
                    f"limit is {self._limit} concurrent provider calls",
                    limit=self._limit,
                    waited_seconds=elapsed,
                )
            await asyncio.sleep(_POLL_SECONDS * (1.0 + random.random()))  # noqa: S311
        self._in_flight += 1
        try:
            yield
        finally:
            self._in_flight -= 1
            if held:
                try:
                    await self._resolve_client().eval(_RELEASE_LUA, 1, self._key, token)
                except Exception:  # noqa: BLE001 - the lease expires on its own
                    # Deliberately not fatal and deliberately not retried: an unreleased
                    # lease is reaped by the next acquire once it ages past the lease
                    # window, which is the same path a killed process takes. Raising
                    # here would turn a Redis blip into a failed model call whose
                    # answer is already in hand.
                    logger.warning(
                        "Could not release a model-call slot; it will expire in %.0fs.",
                        self._lease,
                        exc_info=True,
                    )

    def status(self) -> dict[str, Any]:
        """Return the live counters, including how often the bound was not held."""
        return {
            "scope": self.reported_scope,
            "limit": self._limit,
            "in_flight": self._in_flight,
            "waits": self._waits,
            "degraded": self._degraded,
        }

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        limit: int,
        lease_seconds: float,
        wait_seconds: float = 60.0,
        key: str = "aegis:gateway:slots",
    ) -> RedisSlotLimiter:
        """Build a fleet limiter over a ``redis://`` URL (lazy import).

        Args:
            url: The Redis connection URL.
            limit: The fleet-wide concurrent-call limit.
            lease_seconds: See :meth:`__init__`.
            wait_seconds: See :meth:`__init__`.
            key: See :meth:`__init__`.

        Returns:
            The limiter, over an async ``redis.asyncio`` client.
        """
        def _connect() -> RedisLike:
            import redis.asyncio as redis  # noqa: PLC0415 - lazy: keeps unit tests infra-free

            return redis.from_url(url, decode_responses=True)

        return cls(
            limit=limit,
            lease_seconds=lease_seconds,
            wait_seconds=wait_seconds,
            key=key,
            client_factory=_connect,
        )


def _env_int(name: str, default: int) -> int:
    """Read a positive integer from the environment, ignoring an unusable value."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s=%r is not an integer; using %d.", name, raw, default)
        return default
    return value if value >= 1 else default


def limiter_from_env(*, timeout_seconds: float) -> SlotLimiter:
    """Build the limiter a standalone ``aegis.gateway`` should use, from the environment.

    ``GATEWAY_MAX_CONCURRENT_CALLS`` sets the limit; ``REDIS_URL`` decides whether the
    bound can be fleet-wide. With no Redis URL the result is a
    :class:`LocalSlotLimiter`, whose ``scope`` is ``process`` — accurate for a
    one-process standalone user, and visibly not a fleet claim for anybody else.

    Args:
        timeout_seconds: The gateway's per-call timeout, which the lease is derived from.

    Returns:
        The configured limiter, or :class:`NoSlotLimiter` when no limit is set.
    """
    limit = _env_int("GATEWAY_MAX_CONCURRENT_CALLS", 0)
    if limit < 1:
        return NoSlotLimiter()
    url = os.getenv("REDIS_URL")
    # Local import: ``aegis.gateway.llm`` imports this module, so the dependency only
    # goes the other way at call time — and this is never called at import time.
    from aegis.gateway.llm import max_call_hold_seconds  # noqa: PLC0415

    lease = lease_seconds_for(max_call_hold_seconds(timeout_seconds))
    if url:
        return RedisSlotLimiter.from_url(url, limit=limit, lease_seconds=lease)
    return LocalSlotLimiter(limit)
