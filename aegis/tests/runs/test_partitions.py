"""The monthly partition arithmetic, before any of it touches a database.

``run_events`` is partitioned at creation because that decision cannot be taken later —
there is no migration tool in this project and a heap table does not become a partitioned
one without one. Everything that follows from that decision is arithmetic on months, and
arithmetic is worth testing where it is cheap: a partition whose range is a day short is
a table that silently rejects a day of events.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from aegis.runs.partitions import (
    _month_of,
    partition_name_for,
    run_event_partition_statements,
)


def test_a_partition_is_named_for_the_month_it_covers():
    assert partition_name_for(datetime(2026, 8, 18, 13, 5, tzinfo=UTC)) == "run_events_2026_08"
    assert partition_name_for(datetime(2026, 1, 1, tzinfo=UTC)) == "run_events_2026_01"
    assert partition_name_for(datetime(2026, 12, 31, 23, 59, tzinfo=UTC)) == "run_events_2026_12"


def test_the_month_is_decided_in_utc_not_in_the_writers_timezone():
    """A local-midnight instant belongs to whichever month it is in UTC.

    The column is ``timestamptz`` and the partition bounds are UTC instants, so a naive
    or offset datetime that is one month in Asia/Kolkata and another in UTC must be named
    for the UTC one — otherwise the row routes to a partition the name says it is not in,
    which is the same bug as having no partition at all but harder to see.
    """
    kolkata = timezone(timedelta(hours=5, minutes=30))
    # 2026-09-01 04:00+05:30 is still 2026-08-31 22:30 UTC.
    assert partition_name_for(datetime(2026, 9, 1, 4, 0, tzinfo=kolkata)) == "run_events_2026_08"
    # A naive datetime is read as UTC, matching aegis.data.UtcDateTime's bind behaviour.
    assert partition_name_for(datetime(2026, 9, 1, 4, 0)) == "run_events_2026_09"


def test_partition_names_round_trip_through_the_parser():
    """Retention decides what to DROP from the name, so the parse must be exact."""
    moment = datetime(2027, 3, 9, tzinfo=UTC)
    assert _month_of(partition_name_for(moment)) == datetime(2027, 3, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    "name",
    ["run_events", "run_events_2026_1", "run_events_archive", "run_events_2026_13_old"],
)
def test_a_name_this_module_did_not_generate_is_not_claimed(name):
    """Anything unrecognised is left alone — retention must not guess.

    ``_month_of`` returning ``None`` is what stops ``prune_run_event_partitions`` from
    dropping a hand-made archive partition somebody attached to the parent.
    """
    assert _month_of(name) is None


def test_the_default_window_is_this_month_and_the_next():
    statements = run_event_partition_statements(datetime(2026, 8, 18, tzinfo=UTC))
    creates = [s for s in statements if s.startswith("CREATE TABLE")]
    assert len(creates) == 2
    assert 'CREATE TABLE IF NOT EXISTS "run_events_2026_08"' in creates[0]
    assert "FROM ('2026-08-01T00:00:00+00:00') TO ('2026-09-01T00:00:00+00:00')" in creates[0]
    assert 'CREATE TABLE IF NOT EXISTS "run_events_2026_09"' in creates[1]
    assert "FROM ('2026-09-01T00:00:00+00:00') TO ('2026-10-01T00:00:00+00:00')" in creates[1]


def test_consecutive_partitions_abut_exactly():
    """No gap and no overlap: one instant belongs to exactly one partition.

    A gap is an event with nowhere to go; an overlap is a ``CREATE TABLE`` PostgreSQL
    refuses outright. Asserted across a December boundary because that is where a naive
    ``month + 1`` breaks.
    """
    statements = run_event_partition_statements(
        datetime(2026, 11, 20, tzinfo=UTC), months_ahead=2
    )
    creates = [s for s in statements if s.startswith("CREATE TABLE")]
    bounds = [s.split("FROM (")[1].split(") TO (") for s in creates]
    ends = [pair[1].rstrip(")").strip("'") for pair in bounds]
    starts = [pair[0].strip("'") for pair in bounds]
    assert starts == [
        "2026-11-01T00:00:00+00:00",
        "2026-12-01T00:00:00+00:00",
        "2027-01-01T00:00:00+00:00",
    ]
    assert ends[:-1] == starts[1:]


def test_every_partition_gets_its_own_tenant_policy():
    """The partition-shaped hole: a parent's policy does not filter a direct read.

    Verified against a live PostgreSQL 14 while this was designed — with the policy on
    ``run_events`` only, a scoped connection saw one tenant through the parent and both
    tenants through the partition. So the DDL that creates a partition installs the
    policy on it, and this asserts the statements are there at all; the behavioural proof
    is in the backend's live isolation suite.
    """
    statements = run_event_partition_statements(datetime(2026, 8, 1, tzinfo=UTC), months_ahead=0)
    assert statements[1:] == (
        'ALTER TABLE "run_events_2026_08" ENABLE ROW LEVEL SECURITY',
        'ALTER TABLE "run_events_2026_08" FORCE ROW LEVEL SECURITY',
        'DROP POLICY IF EXISTS tenant_isolation ON "run_events_2026_08"',
        'CREATE POLICY tenant_isolation ON "run_events_2026_08" USING '
        "(substring(current_setting('app.tenant_id', true) from '^[0-9]+$') IS NULL "
        "OR tenant_id = substring(current_setting('app.tenant_id', true) "
        "from '^[0-9]+$')::int)",
    )


def test_the_ddl_is_idempotent_by_construction():
    """It runs on every boot and on a schedule, so a second run must be a no-op."""
    statements = run_event_partition_statements(datetime(2026, 8, 1, tzinfo=UTC))
    assert all(
        s.startswith(("CREATE TABLE IF NOT EXISTS", "ALTER TABLE", "DROP POLICY IF EXISTS"))
        or s.startswith("CREATE POLICY")
        for s in statements
    )
    assert statements == run_event_partition_statements(datetime(2026, 8, 1, tzinfo=UTC))


def test_a_negative_window_is_refused_rather_than_silently_empty():
    with pytest.raises(ValueError, match="months_ahead"):
        run_event_partition_statements(datetime(2026, 8, 1, tzinfo=UTC), months_ahead=-1)
