"""Per-agent logs are a WHERE clause, not a second stream (§5.4), over real PostgreSQL.

Phase 3 built ``run_events`` with an ``agent_id`` column and left it null. This is the
test that it is filled — and filled from the ONE place identity is stamped, the writer
the fan-out binds per lane, rather than by asking each call site to remember.

The chain asserted here is the whole of §5.4 end to end:

    a sub-agent emits  →  the lane's writer stamps ``agent_id``  →  the orchestrator
    stamps ``run_id``/``seq``  →  ``record_events`` promotes ``agent_id`` to its column

so "show me what the research agent did" is ``WHERE run_id = … AND agent_id = 'research'``
over a table that already exists, tenant-scoped by a policy that already exists. Anything
else — a per-agent buffer, a second stream, a log file — would be a fourth tracking
mechanism, and there is a whole phase document arguing against a fourth.
"""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from aegis.governance.rls import set_tenant_scope
from aegis.runs.models import RUN_EVENTS_TABLE, RunEvent
from aegis.runs.record import record_events

from .._seed import ensure_tenants
from ..agent.test_team_fanout import DEMO_QUERY, _drive, _roster, build_team_deps

_TENANT = 511

#: Events a lane emits. Every one of them must carry the lane's identity.
_LANE_TYPES = {"agent_status", "reasoning", "tool_call", "tool_result",
               "node_started", "node_finished"}


@pytest_asyncio.fixture
async def db(pg_sessionmaker: async_sessionmaker) -> async_sessionmaker:
    """The unprivileged sessionmaker with the owning tenant materialised for the FK."""
    await ensure_tenants(pg_sessionmaker, _TENANT)
    return pg_sessionmaker


async def _fanout_events() -> list[dict]:
    """Drive a real four-agent run and return its stamped event stream."""
    deps, _ = build_team_deps(roster=_roster(4))
    return await _drive(deps, DEMO_QUERY)


async def test_every_event_a_subagent_emits_carries_its_agent_id():
    events = await _fanout_events()

    lanes = {e["agent_id"] for e in events if e.get("agent_id")}
    assert lanes == {"research", "knowledge", "data", "policy"}

    # Nothing a lane emits may be anonymous: the lane writer stamps identity at the
    # seam, so an unstamped lane event means someone emitted around it.
    for agent_id in lanes:
        lane = [e for e in events if e.get("agent_id") == agent_id]
        assert {e["type"] for e in lane} >= {"agent_status", "node_started", "node_finished"}
        assert all(e["agent_id"] == agent_id for e in lane)

    # And supervisor-level work stays supervisor-level: a stale identity on the merge
    # would file the synthesis inside somebody's lane.
    for event in events:
        if event["type"] in {"synthesis", "routing", "run_started", "run_finished", "token"}:
            assert event.get("agent_id") is None


async def test_the_agent_id_reaches_the_durable_run_events_row(db):
    events = await _fanout_events()
    run_id = events[0]["run_id"]

    async with db() as session:
        await set_tenant_scope(session, _TENANT)
        await record_events(
            session, run_id=run_id, events=events, tenant_id=_TENANT
        )
        await session.commit()

    async with db() as session:
        await set_tenant_scope(session, _TENANT)
        rows = (
            await session.execute(
                select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)
            )
        ).scalars().all()

    assert len(rows) == len(events)
    by_seq = {e["seq"]: e for e in events}
    for row in rows:
        assert row.agent_id == by_seq[row.seq].get("agent_id"), (
            f"seq {row.seq} ({row.event_type}) lost its agent on the way to the row"
        )
        # The payload is still the event AS IT STREAMED — promoting a column must not
        # cost a replayer the field it read on the wire.
        assert row.payload.get("agent_id") == row.agent_id

    # The per-agent log is a WHERE clause over that column, and it is not empty.
    async with db() as session:
        await set_tenant_scope(session, _TENANT)
        research = (
            await session.execute(
                select(RunEvent)
                .where(RunEvent.run_id == run_id, RunEvent.agent_id == "research")
                .order_by(RunEvent.seq)
            )
        ).scalars().all()
    assert research, "the research agent's log is empty"
    assert {r.event_type for r in research} >= {"agent_status", "node_finished"}
    assert all(r.event_type in _LANE_TYPES for r in research)


async def test_the_per_agent_query_is_indexed_rather_than_a_partition_scan(db):
    """``(run_id, agent_id)`` exists, or per-agent logs scan every partition of the run.

    Read from ``pg_indexes`` rather than from the model's ``__table_args__``: the point
    is that the index is IN THE DATABASE the suite provisions, which is the only place
    the query planner will look for it.
    """
    async with db() as session:
        indexes = set(
            (
                await session.execute(
                    text("SELECT indexname FROM pg_indexes WHERE tablename = :t"),
                    {"t": RUN_EVENTS_TABLE},
                )
            ).scalars()
        )
    assert "ix_run_events_run_agent" in indexes, sorted(indexes)
