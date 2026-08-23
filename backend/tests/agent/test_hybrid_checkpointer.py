"""The durable checkpointer must implement the half of the protocol LangGraph calls.

These pin the defect that made ``AGENT_CHECKPOINTER=postgres`` unusable, and the fix.

The shipped design selected ``langgraph.checkpoint.postgres.PostgresSaver`` — a
**sync-only** saver. This app drives every run with ``graph.astream(...)`` and
``AsyncPregelLoop.__aenter__`` calls ``await checkpointer.aget_tuple(...)`` as its very
first act, so the durable path raised ``NotImplementedError`` on the first token of the
first run. (The mirror-image trap is ``AsyncPostgresSaver``: its sync entry points
refuse to run on their own event loop, and ``aegis.agent.orchestrator`` calls the sync
``graph.get_state(config)`` from inside ``async def`` bodies.)

Nothing here opens a connection: the saver is constructed over a dummy conn object, and
each async method is checked to delegate to the sync one it wraps.
"""

from __future__ import annotations

import asyncio

import pytest
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver

from app.agent.checkpointer import HybridPostgresSaver

#: Every async method LangGraph's async Pregel loop may call on a checkpointer, and
#: which sync method the hybrid must route it to.
ASYNC_TO_SYNC = {
    "aget_tuple": "get_tuple",
    "alist": "list",
    "aput": "put",
    "aput_writes": "put_writes",
    "adelete_thread": "delete_thread",
    "acopy_thread": "copy_thread",
    "adelete_for_runs": "delete_for_runs",
    "aprune": "prune",
    "aget_delta_channel_history": "get_delta_channel_history",
}


def test_stock_postgres_saver_has_no_async_protocol():
    """The bug: every async method on ``PostgresSaver`` is the base's raising stub.

    If a future release of ``langgraph-checkpoint-postgres`` implements these, this
    test fails and :class:`HybridPostgresSaver` can be deleted — which is the point of
    asserting it rather than only asserting the workaround.
    """
    unimplemented = [
        name
        for name in ASYNC_TO_SYNC
        if getattr(PostgresSaver, name) is getattr(BaseCheckpointSaver, name, None)
    ]
    assert unimplemented == list(ASYNC_TO_SYNC), (
        "PostgresSaver now implements some async methods; re-check whether "
        "HybridPostgresSaver is still needed."
    )


def test_hybrid_overrides_every_async_method():
    """The fix: the hybrid defines all of them itself, so none can fall through."""
    for name in ASYNC_TO_SYNC:
        assert name in HybridPostgresSaver.__dict__, f"{name} is not overridden"


@pytest.mark.asyncio
async def test_async_methods_run_the_sync_ones_off_the_event_loop():
    """Each async method calls its sync twin, and does so in a worker thread.

    The thread matters as much as the delegation: psycopg is blocking, and a saver that
    ran it on the event loop would stall every other request in the process for the
    duration of each checkpoint write.
    """
    saver = HybridPostgresSaver.__new__(HybridPostgresSaver)
    loop_thread = asyncio.get_running_loop()
    seen: dict[str, str] = {}

    def record(name):
        def sync(*args, **kwargs):
            import threading

            seen[name] = threading.current_thread().name
            return [] if name == "list" else name

        return sync

    for sync_name in ASYNC_TO_SYNC.values():
        object.__setattr__(saver, sync_name, record(sync_name))

    assert await saver.aget_tuple({}) == "get_tuple"
    assert [row async for row in saver.alist(None)] == []
    assert await saver.aput({}, {}, {}, {}) == "put"
    await saver.aput_writes({}, [], "task")
    await saver.adelete_thread("t")
    await saver.acopy_thread("a", "b")
    await saver.adelete_for_runs(["r"])
    await saver.aprune(["t"])
    await saver.aget_delta_channel_history(config={}, channels=[])

    assert set(seen) == set(ASYNC_TO_SYNC.values()), "a sync twin was never called"
    main = loop_thread._thread_id if hasattr(loop_thread, "_thread_id") else None
    assert main is None or all(name != main for name in seen.values())
    import threading

    here = threading.current_thread().name
    assert all(thread != here for thread in seen.values()), (
        "a sync checkpoint call ran on the event loop's own thread"
    )
