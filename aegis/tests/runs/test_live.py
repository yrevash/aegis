"""The durable run record against a real PostgreSQL — partitions, RLS and the projection.

Three of these could not be written against SQLite at all, which is the point of the
module: range partitioning, the ``no partition of relation`` failure and the tenant
policy are PostgreSQL behaviours, and the whole design rests on them behaving as claimed
rather than as remembered.

Every assertion runs over the suite's ``NOSUPERUSER NOBYPASSRLS`` role (see
``tests/conftest.py``) except where an owner connection is needed for DDL or for a
non-vacuity count, which is called out where it happens.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from aegis.governance.rls import set_tenant_scope
from aegis.runs.models import Run, RunEvent
from aegis.runs.partitions import (
    ensure_run_event_partitions,
    partition_name_for,
    prune_run_event_partitions,
    run_event_partitions,
)
from aegis.runs.record import (
    RunPartitionMissingError,
    read_run_header,
    rebuild_run_header,
    reconcile_run_header,
    record_events,
)

from .._seed import ensure_tenants
from ._stream import full_run

_TENANT_A = 501
_TENANT_B = 502


@pytest_asyncio.fixture
async def db(pg_sessionmaker: async_sessionmaker) -> async_sessionmaker:
    """The unprivileged sessionmaker, with both tenants materialised for the FKs."""
    await ensure_tenants(pg_sessionmaker, _TENANT_A, _TENANT_B)
    return pg_sessionmaker


async def _write(db, tenant_id: int, run_id: str, events, *, now=None):  # noqa: ANN001, ANN202
    """Record a run's events under a bound tenant scope and commit."""
    async with db() as session:
        await set_tenant_scope(session, tenant_id)
        header = await record_events(
            session, run_id=run_id, events=events, tenant_id=tenant_id, now=now
        )
        await session.commit()
        return header


async def test_an_event_outside_every_partition_fails_loudly_and_writes_nothing(db):
    """The classic partitioning trap: the row must not land nowhere, quietly.

    PostgreSQL rejects it — ``no partition of relation "run_events" found for row`` — and
    the record layer translates that into an error naming the missing month and the fix,
    because the raw failure reads like bad event data rather than a schema with a hole in
    it. The second half of the test is the important half: **nothing** from the batch was
    written, so a caller that retries after rolling the partitions forward writes each
    event once.
    """
    stale = datetime.now(UTC) - timedelta(days=400)
    events = full_run("run-stale")

    async with db() as session:
        await set_tenant_scope(session, _TENANT_A)
        with pytest.raises(RunPartitionMissingError) as caught:
            await record_events(
                session,
                run_id="run-stale",
                events=events,
                tenant_id=_TENANT_A,
                now=stale,
            )
        await session.rollback()

    assert caught.value.partition == partition_name_for(stale)
    assert "ensure_run_event_partitions" in str(caught.value), (
        "the error must name the fix; a missing partition is a schema problem and the "
        "message is the only place an operator learns that"
    )

    async with db() as session:
        await set_tenant_scope(session, _TENANT_A)
        landed = (
            await session.execute(
                select(func.count()).select_from(RunEvent).where(RunEvent.run_id == "run-stale")
            )
        ).scalar_one()
        assert landed == 0
        assert await read_run_header(session, "run-stale") is None


async def test_the_header_rebuilt_from_events_equals_the_maintained_one(db):
    """The projection's whole justification, over a run with every event type.

    ``record_events`` maintains the header as it writes; ``rebuild_run_header`` folds the
    stored events from empty. Equality here is what makes "the header is regenerable, and
    if the two disagree events win" a fact rather than an intention — and it runs over
    rows that made a real round trip through ``jsonb`` and ``timestamptz``, so a value
    the database rounds or reorders shows up.
    """
    events = full_run("run-alpha")
    maintained = await _write(db, _TENANT_A, "run-alpha", events)

    async with db() as session:
        await set_tenant_scope(session, _TENANT_A)
        stored = await read_run_header(session, "run-alpha")
        rebuilt = await rebuild_run_header(session, "run-alpha", tenant_id=_TENANT_A)

    assert stored == maintained
    assert rebuilt == stored
    assert rebuilt.event_count == len(events)
    assert rebuilt.status is not None


async def test_events_written_in_two_batches_still_rebuild_identically(db):
    """A run is streamed, so the header is folded in pieces — that must not change it."""
    events = full_run("run-split")
    await _write(db, _TENANT_A, "run-split", events[:9])
    await _write(db, _TENANT_A, "run-split", events[9:])

    async with db() as session:
        await set_tenant_scope(session, _TENANT_A)
        stored = await read_run_header(session, "run-split")
        rebuilt = await rebuild_run_header(session, "run-split", tenant_id=_TENANT_A)
    assert stored == rebuilt
    assert stored.event_count == len(events)


async def test_a_header_that_disagrees_with_the_events_is_corrected_from_them(db):
    """Events win — and there is an operation that makes that true, not just a rule."""
    await _write(db, _TENANT_A, "run-drift", full_run("run-drift"))

    async with db() as session:
        await set_tenant_scope(session, _TENANT_A)
        row = await session.get(Run, "run-drift")
        row.cost_usd = 999.0
        row.event_count = 1
        await session.commit()

    async with db() as session:
        await set_tenant_scope(session, _TENANT_A)
        rebuilt, changed = await reconcile_run_header(session, "run-drift", tenant_id=_TENANT_A)
        await session.commit()

    assert changed is True
    assert rebuilt.cost_usd == pytest.approx(0.42)
    async with db() as session:
        await set_tenant_scope(session, _TENANT_A)
        assert await read_run_header(session, "run-drift") == rebuilt
        _, changed_again = await reconcile_run_header(session, "run-drift")
    assert changed_again is False, "a reconcile that changes nothing must report nothing"


async def test_a_tenant_reads_only_its_own_events(db, pg_owner_engine: AsyncEngine):
    """Through the same policy the Phase 1 isolation suite proves, on real rows.

    The owner read is the non-vacuity control: "A sees only A's events" is trivially true
    if B never wrote any, and the owner bypasses RLS so it counts what is physically
    there.
    """
    await _write(db, _TENANT_A, "run-a", full_run("run-a"))
    await _write(db, _TENANT_B, "run-b", full_run("run-b"))

    async with pg_owner_engine.connect() as conn:
        runs = (
            await conn.execute(text("SELECT DISTINCT run_id FROM run_events ORDER BY run_id"))
        ).scalars().all()
    assert runs == ["run-a", "run-b"]

    for tenant_id, own in ((_TENANT_A, "run-a"), (_TENANT_B, "run-b")):
        async with db() as session:
            await set_tenant_scope(session, tenant_id)
            visible = (
                await session.execute(select(RunEvent.run_id).distinct())
            ).scalars().all()
            headers = (await session.execute(select(Run.run_id))).scalars().all()
            await session.rollback()
        assert visible == [own], f"tenant {tenant_id} saw {visible}"
        assert headers == [own], f"tenant {tenant_id} saw headers {headers}"


async def test_rolling_the_partitions_forward_is_idempotent(pg_owner_engine: AsyncEngine):
    """It runs on every boot and on a schedule; a second run must change nothing."""
    now = datetime.now(UTC)
    # The first day of next month, computed without a calendar library: day 28 is in
    # every month, so +4 days always lands in the next one.
    next_month = (now.replace(day=28) + timedelta(days=4)).replace(day=1)
    async with pg_owner_engine.connect() as conn:
        before = await run_event_partitions(conn)
    assert [p.name for p in before] == [
        partition_name_for(now),
        partition_name_for(next_month),
    ], "create_all should have left exactly this month's and next month's partitions"

    await ensure_run_event_partitions(pg_owner_engine)
    async with pg_owner_engine.connect() as conn:
        assert await run_event_partitions(conn) == before

    # A window further out adds one partition and leaves the existing ones alone.
    await ensure_run_event_partitions(pg_owner_engine, months_ahead=2)
    async with pg_owner_engine.connect() as conn:
        after = await run_event_partitions(conn)
    assert [p.name for p in after[: len(before)]] == [p.name for p in before]
    assert len(after) == len(before) + 1


async def test_retention_drops_whole_partitions_and_leaves_the_live_one(
    db, pg_owner_engine: AsyncEngine
):
    """Retention is a ``DROP``, and it only ever drops a fully expired month.

    Dropping a partition unlinks a table; deleting ten million rows rewrites every one of
    them and leaves the heap bloated until a vacuum catches up. That difference is why
    this table is partitioned at all, so the test asserts the *mechanism* — the partition
    is gone from the catalog — and not merely that the rows are.
    """
    old = datetime.now(UTC).replace(day=1) - timedelta(days=90)
    await ensure_run_event_partitions(pg_owner_engine, moment=old, months_ahead=0)
    old_partition = partition_name_for(old)

    # Written over the owner connection: the point of this test is retention, and the
    # event predates the window the unprivileged path is expected to write into.
    async with pg_owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO run_events (run_id, tenant_id, seq, ts, event_type, payload) "
                "VALUES ('run-old', :tenant, 0, :ts, 'token', '{}'::jsonb)"
            ),
            {"tenant": _TENANT_A, "ts": old},
        )
    await _write(db, _TENANT_A, "run-new", full_run("run-new"))

    async with pg_owner_engine.connect() as conn:
        names = {p.name for p in await run_event_partitions(conn)}
        assert old_partition in names
        total_before = (
            await conn.execute(text("SELECT count(*) FROM run_events"))
        ).scalar_one()

    horizon = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    async with pg_owner_engine.begin() as conn:
        dropped = await prune_run_event_partitions(conn, before=horizon)
    assert dropped == (old_partition,)

    async with pg_owner_engine.connect() as conn:
        names_after = {p.name for p in await run_event_partitions(conn)}
        total_after = (
            await conn.execute(text("SELECT count(*) FROM run_events"))
        ).scalar_one()
    assert old_partition not in names_after
    assert partition_name_for(datetime.now(UTC)) in names_after
    assert total_after == total_before - 1

    # And the current month's events are untouched, which is the half a too-eager
    # retention job gets wrong.
    async with db() as session:
        await set_tenant_scope(session, _TENANT_A)
        assert (await read_run_header(session, "run-new")).event_count == len(full_run())


async def test_a_partition_created_after_the_grants_is_still_writable_by_the_app_role(
    db, pg_owner_engine: AsyncEngine
):
    """Next month's partition is created long after the serving role was granted DML.

    If a partition needed its own ``GRANT``, the platform would work perfectly until the
    first of the month and then start refusing every event — the worst possible time to
    discover it. PostgreSQL checks privileges on the table named in the query, so a write
    routed *through* ``run_events`` is checked against ``run_events``; this is the test
    that says so about this cluster rather than about the documentation.
    """
    now = datetime.now(UTC)
    # Two months out: past the window ``create_all`` opened, so this partition is created
    # here, after the template's grants were issued.
    future = (now.replace(day=28) + timedelta(days=32)).replace(day=1)
    await ensure_run_event_partitions(pg_owner_engine, moment=future, months_ahead=0)

    header = await _write(db, _TENANT_A, "run-future", full_run("run-future"), now=future)
    assert header.event_count == len(full_run())

    async with db() as session:
        await set_tenant_scope(session, _TENANT_A)
        stored = await read_run_header(session, "run-future")
        rebuilt = await rebuild_run_header(session, "run-future", tenant_id=_TENANT_A)
    assert stored == rebuilt
