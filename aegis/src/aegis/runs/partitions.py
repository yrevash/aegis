"""Monthly partitions for ``run_events`` — created with the table, rolled and pruned.

``run_events`` is ``PARTITION BY RANGE (ts)``, which is the **one irreversible decision**
in this schema: a partitioned table cannot be conjured out of a large heap table without
a migration, and this project deliberately has no migration tool. Partitioning is
therefore decided at creation, and this module is what makes that decision survivable —
a partitioned table with no partition covering "now" accepts no writes at all.

Three jobs, one definition of a partition between them:

* **Creation.** ``ensure_run_event_partitions`` creates the current and next month, and
  it also runs from a ``create_all`` hook so every schema-building site in the repo gets
  partitions without having to remember to ask. A schema built without them would look
  perfectly healthy until the first event was written.
* **Rolling forward.** The same call from a scheduled job (Phase 3 §3.5) keeps a month
  of headroom. It is idempotent — ``CREATE TABLE IF NOT EXISTS`` and a policy dropped
  before it is recreated — so running it every hour is as correct as running it monthly.
* **Retention.** ``prune_run_event_partitions`` **drops whole partitions**. Dropping a
  partition is a catalog operation; ``DELETE FROM run_events WHERE ts < …`` over ten
  million rows is minutes of write amplification, a bloated heap and a vacuum problem.
  That difference is the reason the table is partitioned at all.

**Every partition gets its own copy of the ``tenant_isolation`` policy**, built by
:func:`aegis.governance.rls.tenant_policy_statements` so there is one definition of
"governed" rather than two. This is not belt-and-braces: verified against a live
PostgreSQL 14, a partition's rows are filtered by the parent's policies only when they
are reached *through* the parent. A query naming ``run_events_2026_08`` directly is
filtered by that partition's own policies — and by nothing else, so a partition without
one is a hole that reads perfectly healthy from the parent.

Everything here is PostgreSQL-only and a clean no-op elsewhere: on SQLite (the portable
unit-test schema) ``run_events`` materialises as an ordinary table, which is exactly
right — there is nothing to partition and nothing to isolate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from aegis.governance.rls import tenant_policy_statements
from aegis.runs.models import RUN_EVENTS_TABLE, RunEvent

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "RunEventPartition",
    "ensure_run_event_partitions",
    "partition_name_for",
    "prune_run_event_partitions",
    "run_event_partition_statements",
    "run_event_partitions",
]

#: How a partition of ``run_events`` is named: the parent, then the month it covers.
#: Parsed as well as generated (see :func:`_month_of`), because retention decides what
#: to **drop** from this name — so a relation attached to the parent whose name this
#: pattern does not match is left alone rather than guessed at.
_PARTITION_NAME = re.compile(rf"^{RUN_EVENTS_TABLE}_(?P<year>\d{{4}})_(?P<month>\d{{2}})$")


@dataclass(frozen=True, slots=True)
class RunEventPartition:
    """One live partition of ``run_events``, as the catalog reports it.

    Attributes:
        name: The partition's relation name.
        starts_at: First instant it covers (inclusive), parsed from the name.
        ends_at: First instant it does **not** cover (exclusive).
    """

    name: str
    starts_at: datetime
    ends_at: datetime


def _month_start(moment: datetime) -> datetime:
    """Return the first instant of ``moment``'s month, in UTC.

    Args:
        moment: Any instant; naive input is read as UTC, matching
            :class:`aegis.data.UtcDateTime`'s bind behaviour.

    Returns:
        Midnight on the first of that month, timezone-aware in UTC.
    """
    moment = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month(month_start: datetime) -> datetime:
    """Return the first instant of the month after ``month_start``."""
    if month_start.month == 12:
        return month_start.replace(year=month_start.year + 1, month=1)
    return month_start.replace(month=month_start.month + 1)


def partition_name_for(moment: datetime) -> str:
    """Return the name of the partition that holds an event at ``moment``.

    Args:
        moment: The event timestamp.

    Returns:
        The partition relation name, e.g. ``run_events_2026_08``.
    """
    start = _month_start(moment)
    return f"{RUN_EVENTS_TABLE}_{start.year:04d}_{start.month:02d}"


def _month_of(partition: str) -> datetime | None:
    """Return the month a partition name covers, or ``None`` if it is not one of ours.

    Args:
        partition: A relation name attached to ``run_events``.

    Returns:
        The first instant of the covered month, or ``None`` when the name was not
        generated by :func:`partition_name_for` — in which case nothing here will drop
        it. Guessing at a name we did not write is how retention deletes somebody's
        hand-made archive partition.
    """
    match = _PARTITION_NAME.match(partition)
    if match is None:
        return None
    return datetime(
        int(match["year"]), int(match["month"]), 1, tzinfo=UTC
    )


def run_event_partition_statements(
    moment: datetime | None = None, *, months_ahead: int = 1
) -> tuple[str, ...]:
    """Return the DDL creating (and governing) the partitions covering a window.

    Pure and synchronous so the identical statements can be issued from a ``create_all``
    event hook — where the connection is a *synchronous* one SQLAlchemy hands to the
    listener — and from the async helpers below. One builder, so a partition created by
    the schema and one created by the scheduler cannot differ.

    Args:
        moment: The instant the window starts from; defaults to now. The partition
            covering it is always created.
        months_ahead: How many further months to create beyond that one. The default of
            1 is the headroom the roll-forward job maintains: a month is long enough
            that a missed run is noticed, and short enough that an empty partition costs
            nothing.

    Returns:
        The ordered DDL statements: for each month, ``CREATE TABLE IF NOT EXISTS …
        PARTITION OF …`` followed by that partition's ``tenant_isolation`` policy.

    Raises:
        ValueError: If ``months_ahead`` is negative.
    """
    if months_ahead < 0:
        raise ValueError(f"months_ahead must be >= 0, got {months_ahead}")
    start = _month_start(moment or datetime.now(UTC))
    statements: list[str] = []
    for _ in range(months_ahead + 1):
        end = _next_month(start)
        name = partition_name_for(start)
        statements.append(
            f'CREATE TABLE IF NOT EXISTS "{name}" PARTITION OF "{RUN_EVENTS_TABLE}" '
            f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
        )
        # A partition reached directly is governed by its own policies and nothing
        # else — see the module docstring. Same builder as the bootstrap uses, with the
        # parent named so the partition inherits the parent's flavour of policy.
        statements.extend(
            tenant_policy_statements(name, policy_for=RUN_EVENTS_TABLE)
        )
        start = end
    return tuple(statements)


async def _execute(
    target: AsyncConnection | AsyncEngine, statements: Iterable[str]
) -> None:
    """Run ``statements`` on a connection, or in one transaction on an engine.

    Args:
        target: An open connection (the caller owns the transaction) or an engine (one
            is opened here).
        statements: The DDL to execute, in order.
    """
    if isinstance(target, AsyncEngine):
        async with target.begin() as conn:
            for statement in statements:
                await conn.execute(text(statement))
        return
    for statement in statements:
        await target.execute(text(statement))


async def ensure_run_event_partitions(
    target: AsyncConnection | AsyncEngine,
    *,
    moment: datetime | None = None,
    months_ahead: int = 1,
) -> tuple[str, ...]:
    """Create the partitions covering now and the next ``months_ahead`` months.

    Idempotent, and safe to call on every boot and on a schedule: the ``CREATE TABLE``
    is ``IF NOT EXISTS`` and the policy DDL drops before it creates.

    Args:
        target: An open async connection, or an engine to open one on.
        moment: The instant to start the window at; defaults to now.
        months_ahead: Months of headroom beyond the current one.

    Returns:
        The partition names the window covers, oldest first. Existing partitions are
        included — the call describes the state it guarantees, not the rows it changed.
    """
    if _dialect(target) != "postgresql":
        return ()
    await _execute(target, run_event_partition_statements(moment, months_ahead=months_ahead))
    start = _month_start(moment or datetime.now(UTC))
    names = []
    for _ in range(months_ahead + 1):
        names.append(partition_name_for(start))
        start = _next_month(start)
    return tuple(names)


def _dialect(target: Any) -> str:  # noqa: ANN401 - engine|connection|sync connection
    """Return the dialect name behind an engine or a connection."""
    return target.dialect.name


_PARTITION_CATALOG_SQL = """
SELECT c.relname
  FROM pg_class c
  JOIN pg_inherits i ON i.inhrelid = c.oid
  JOIN pg_class p ON p.oid = i.inhparent
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = current_schema()
   AND p.relname = :parent
 ORDER BY c.relname
"""


async def run_event_partitions(
    conn: AsyncConnection,
) -> tuple[RunEventPartition, ...]:
    """Return the live partitions of ``run_events``, oldest first.

    Read from ``pg_inherits`` rather than from what this module believes it created:
    the retention job's whole purpose is to act on partitions written months ago by an
    older process, and a list computed from the calendar would happily "confirm" a
    partition that does not exist.

    Args:
        conn: An open async connection (PostgreSQL).

    Returns:
        One :class:`RunEventPartition` per attached partition whose name this module
        recognises. A partition with any other name is omitted — see :func:`_month_of`.
    """
    if _dialect(conn) != "postgresql":
        return ()
    result = await conn.execute(text(_PARTITION_CATALOG_SQL), {"parent": RUN_EVENTS_TABLE})
    partitions: list[RunEventPartition] = []
    for (name,) in result:
        start = _month_of(name)
        if start is None:
            continue
        partitions.append(RunEventPartition(name=name, starts_at=start, ends_at=_next_month(start)))
    return tuple(sorted(partitions, key=lambda p: p.starts_at))


async def prune_run_event_partitions(
    conn: AsyncConnection, *, before: datetime
) -> tuple[str, ...]:
    """Drop every partition that ends at or before ``before``, and say which.

    Retention by ``DROP``, never by ``DELETE``: dropping a partition unlinks a file and
    a catalog row, where deleting its rows rewrites every one of them, leaves the table
    bloated until a vacuum catches up, and takes a lock on the live table while doing it.

    Only whole, fully-expired partitions are dropped. A partition that still holds one
    retained instant is kept in full — trimming inside it would be the ``DELETE`` this
    design exists to avoid.

    Args:
        conn: An open async connection (PostgreSQL).
        before: The retention horizon; nothing at or after it is dropped.

    Returns:
        The names dropped, oldest first.
    """
    if _dialect(conn) != "postgresql":
        return ()
    dropped: list[str] = []
    for partition in await run_event_partitions(conn):
        if partition.ends_at <= before:
            # The name came from the catalog and matched _PARTITION_NAME, so it is a
            # plain identifier by construction.
            await conn.execute(text(f'DROP TABLE IF EXISTS "{partition.name}"'))
            dropped.append(partition.name)
    return tuple(dropped)


@event.listens_for(RunEvent.__table__, "after_create")
def _create_initial_partitions(target: Any, connection: Any, **kw: Any) -> None:  # noqa: ANN401, ARG001
    """Give a freshly created ``run_events`` the partitions it needs to accept a row.

    Hooked to the table's own creation rather than left to each caller because this repo
    has three schema-building sites (the host bootstrap and two test fixtures) and task
    3.1 already had to touch all three for one missing import. A partitioned table with
    no partitions is not a degraded table — it rejects **every** write — so "somebody
    remembers to call the helper" is not a good enough guarantee.

    Args:
        target: The ``run_events`` :class:`~sqlalchemy.Table` being created.
        connection: The **synchronous** connection ``create_all`` is running on.
        **kw: The rest of the DDL event payload, unused.
    """
    if connection.dialect.name != "postgresql":
        return
    for statement in run_event_partition_statements():
        connection.execute(text(statement))
