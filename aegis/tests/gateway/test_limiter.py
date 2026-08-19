"""The model-call limiter: is the bound actually shared, and is it honest when it is not.

The load-bearing claim of :mod:`aegis.gateway.limiter` is a single sentence — *the limit
holds across every process in the pool* — and there is exactly one way to test it that
cannot fool itself: hold the slots from **another interpreter**. A second limiter object
inside this one would demonstrate that two instances share a Redis key; it would not
demonstrate that a process boundary is irrelevant, which is the whole claim. So
:func:`test_the_bound_holds_across_a_real_process_boundary` starts a real subprocess.

The other three cover the failure modes the design has to survive: a holder that dies
without releasing, a shared store that cannot be reached, and the wiring itself — a
limiter nothing calls is the most expensive kind of no-op.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

import aegis.gateway.llm as llm_mod
from aegis.core.models import ModelRole
from aegis.gateway.limiter import (
    LocalSlotLimiter,
    RedisSlotLimiter,
    SlotUnavailableError,
    lease_seconds_for,
)
from aegis.gateway.llm import complete, max_call_hold_seconds

from .test_llm import FakeLiteLLM, _make_response

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

#: What the subprocess does: take ``limit`` leases against the same key this test uses,
#: say so on stdout, then block until it is killed. It imports only the limiter, so a
#: failure here is a failure of the limiter and not of anything it is wired into.
_HOLDER = """
import asyncio, sys
from aegis.gateway.limiter import RedisSlotLimiter

async def main(url, key, limit):
    limiter = RedisSlotLimiter.from_url(
        url, limit=limit, lease_seconds=30.0, wait_seconds=1.0, key=key
    )
    async with limiter.slot(), limiter.slot():
        print("held", flush=True)
        await asyncio.sleep(300)

asyncio.run(main(sys.argv[1], sys.argv[2], int(sys.argv[3])))
"""


async def _redis_or_skip():
    """Return a live async Redis client, or skip naming what went unverified."""
    redis = pytest.importorskip("redis.asyncio")
    client = redis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception as exc:  # noqa: BLE001 - any failure means "no server here"
        await client.aclose()
        pytest.skip(
            f"No Redis at {REDIS_URL} ({exc!r}); the fleet-wide model-call bound is "
            "UNVERIFIED by this run."
        )
    return client


@pytest.fixture
async def redis_client():
    """A live Redis client, closed afterwards."""
    client = await _redis_or_skip()
    yield client
    await client.aclose()


@pytest.fixture
def slot_key():
    """A key unique to this test, so a parallel run cannot borrow its leases."""
    return f"aegis:test:slots:{uuid.uuid4().hex}"


async def test_the_bound_holds_across_a_real_process_boundary(redis_client, slot_key):
    """Two slots held by ANOTHER process leave none for this one, and a semaphore would.

    The mutation this is proving against is the tempting implementation: an
    ``asyncio.Semaphore`` per process. The second half of the test runs exactly that
    against the same scenario and shows it hands out a third slot — so the first half's
    assertion is not passing for some incidental reason.
    """
    src = str(Path(__file__).resolve().parents[2] / "src")
    env = {**os.environ, "PYTHONPATH": src}
    child = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-c", _HOLDER, REDIS_URL, slot_key, "2"],
        stdout=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert child.stdout is not None
        ready = await asyncio.to_thread(child.stdout.readline)
        assert ready.strip() == "held", f"holder process never took its slots: {ready!r}"

        # This interpreter shares nothing with that one except Redis.
        limiter = RedisSlotLimiter(
            redis_client, limit=2, lease_seconds=30.0, wait_seconds=0.3, key=slot_key
        )
        with pytest.raises(SlotUnavailableError) as refused:
            async with limiter.slot():
                pass
        assert refused.value.limit == 2

        # The mutation: a per-process semaphore, same limit, same moment. It admits us —
        # which is the bug, and which is what makes the assertion above meaningful.
        local = LocalSlotLimiter(2)
        async with local.slot():
            assert local.status()["in_flight"] == 1

        # ...and the refusal above was the fleet being full rather than the limiter
        # being broken: raise the limit to three and the same key, still holding the
        # child's two leases, admits this process for the third.
        roomier = RedisSlotLimiter(
            redis_client, limit=3, lease_seconds=30.0, wait_seconds=1.0, key=slot_key
        )
        async with roomier.slot():
            pass
    finally:
        child.kill()
        child.wait(timeout=10)


async def test_a_holder_that_dies_does_not_leak_its_slot_forever(redis_client, slot_key):
    """An unreleased lease is reaped once it ages out, without anyone repairing it.

    A ``DECR``-on-release counter cannot do this: a SIGKILLed holder ratchets the limit
    down by one permanently, and a week of restarts leaves a fleet that admits nobody.
    """
    lease = 0.4
    limiter = RedisSlotLimiter(
        redis_client, limit=1, lease_seconds=lease, wait_seconds=0.1, key=slot_key
    )
    # Enter the slot and never leave it — the shape of a process killed mid-call.
    holder = limiter.slot()
    await holder.__aenter__()

    with pytest.raises(SlotUnavailableError):
        async with limiter.slot():
            pass

    await asyncio.sleep(lease + 0.1)
    async with limiter.slot():  # the dead holder's lease has been reaped
        pass


async def test_an_unreachable_store_is_counted_and_named_not_silently_survived(slot_key):
    """A limiter that cannot reach Redis proceeds, and stops calling itself fleet-wide."""

    class _DeadRedis:
        async def eval(self, *_args, **_kwargs):
            raise ConnectionError("redis is down")

    limiter = RedisSlotLimiter(
        _DeadRedis(), limit=1, lease_seconds=5.0, wait_seconds=0.1, key=slot_key
    )
    async with limiter.slot():
        pass
    status = limiter.status()
    assert status["degraded"] == 1
    # The bound is not being held, and the surface says so rather than reporting "fleet".
    assert status["scope"] == "fleet (degraded)"


async def test_the_gateway_actually_holds_the_slot_around_a_provider_call(monkeypatch):
    """Two concurrent ``complete`` calls do not overlap under a limit of one.

    The limiter is wired into :func:`aegis.gateway.llm._attempt`, and this is the test
    that would fail if that wiring were removed — everything else here tests a class
    nothing calls.
    """
    overlap = {"now": 0, "peak": 0}

    class _SlowFake(FakeLiteLLM):
        async def acompletion(self, **kwargs):
            overlap["now"] += 1
            overlap["peak"] = max(overlap["peak"], overlap["now"])
            try:
                await asyncio.sleep(0.05)
                return _make_response(content="ok")
            finally:
                overlap["now"] -= 1

    monkeypatch.setitem(sys.modules, "litellm", _SlowFake())
    monkeypatch.setattr(llm_mod, "_limiter", LocalSlotLimiter(1))
    messages = [{"role": "user", "content": "hi"}]
    await asyncio.gather(
        complete(ModelRole.CHEAP, messages),
        complete(ModelRole.CHEAP, messages),
        complete(ModelRole.CHEAP, messages),
    )
    assert overlap["peak"] == 1, "the gateway let a second call through the limiter"


def test_the_lease_outlasts_the_call_it_is_derived_from():
    """The lease is longer than one call timeout, so a slow call is not reaped mid-flight."""
    assert lease_seconds_for(60.0) > 60.0
    # A zero/absurd timeout must still produce a usable lease rather than 0.
    assert lease_seconds_for(0.0) >= 2.0



async def test_a_slow_call_is_not_reaped_out_from_under_its_own_holder(
    redis_client, slot_key, monkeypatch
):
    """A limit of one admits one, even when the call outlives a per-attempt timeout.

    **The defect this pins.** The lease used to be ``lease_seconds_for(timeout_seconds)``
    — two *per-attempt* timeouts. But the slot in :func:`aegis.gateway.llm._attempt`
    wraps the **outer** backstop, which gives the primary deployment and each fallback in
    the chain its own timeout budget: ``timeout × (len(fallbacks) + 1)``, i.e. three call
    timeouts for ``GENERATION``. Any call slower than two timeouts therefore had its
    lease reaped while it was still in flight, and the fleet admitted an extra caller —
    the limit silently becoming ``N + 1`` on exactly the slow calls it exists for.

    Mutation-proven: pass ``lease_seconds_for(TIMEOUT)`` instead of
    ``lease_seconds_for(max_call_hold_seconds(TIMEOUT))`` below and ``peak`` reads 2.
    Deliberately a ``RedisSlotLimiter``, because the lease is the mechanism under test
    and :class:`LocalSlotLimiter` has none.
    """
    timeout = 1.0
    # Longer than one lease under the old derivation (2.0s), shorter than the outer
    # backstop (3.0s) — the exact window in which a slot was handed out twice.
    call_seconds = 2.5
    overlap = {"now": 0, "peak": 0}

    class _SlowFake(FakeLiteLLM):
        async def acompletion(self, **kwargs):
            overlap["now"] += 1
            overlap["peak"] = max(overlap["peak"], overlap["now"])
            try:
                await asyncio.sleep(call_seconds)
                return _make_response(content="ok")
            finally:
                overlap["now"] -= 1

    class _Config:
        base_url = "http://unused"
        api_key = "x"
        ssl_verify = False
        max_output_tokens = 64
        timeout_seconds = timeout
        budget_fail_open = False

    limiter = RedisSlotLimiter(
        redis_client,
        limit=1,
        lease_seconds=lease_seconds_for(max_call_hold_seconds(timeout)),
        wait_seconds=30.0,
        key=slot_key,
    )
    monkeypatch.setitem(sys.modules, "litellm", _SlowFake())
    monkeypatch.setattr(llm_mod, "_limiter", limiter)
    monkeypatch.setattr(llm_mod, "_config", _Config())

    messages = [{"role": "user", "content": "hi"}]
    await asyncio.gather(
        complete(ModelRole.GENERATION, messages),
        complete(ModelRole.GENERATION, messages),
    )
    assert overlap["peak"] == 1, (
        "a slow call's lease expired while it was still holding the slot, so the fleet "
        "limit of 1 admitted 2"
    )


def test_the_lease_is_derived_from_the_widest_hold_not_one_attempt():
    """``max_call_hold_seconds`` covers every shape of call the gateway makes."""
    # Two fallbacks on the shipped GENERATION chain -> three call timeouts.
    assert max_call_hold_seconds(60.0) == 180.0
    # ...and the lease covers even that.
    assert lease_seconds_for(max_call_hold_seconds(60.0)) > 180.0
    # ``embed``/``transcribe`` hold theirs for ``timeout + 5``, which dominates when the
    # timeout is small; a lease derived only from the chain would be too short there.
    assert max_call_hold_seconds(1.0) >= 6.0
