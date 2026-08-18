"""Writing run events, and the header projection folded from them.

The header (:class:`aegis.runs.models.Run`) is a second table in a design whose stated
rule is one tracking mechanism, so it is built to a stricter contract than "keep it
roughly up to date": **it is a pure fold over the events and nothing else.**

That contract is what this module is shaped around. :func:`apply_event` is the only
place a header field is ever computed, and both paths go through it:

* :func:`record_events` appends events and folds the same batch onto the stored header,
  so a console has an O(1) row to list runs from.
* :func:`rebuild_run_header` reads a run's events back and folds them from empty.

Because both reduce the identical function over the identical rows, "the projection
agrees with the log" is not a discipline anyone has to maintain — it is arithmetic, and
:func:`reconcile_run_header` is the operation that makes the stated tie-break (**if the
two disagree, events win**) executable rather than aspirational.

The event vocabulary is :mod:`aegis.agent.events` verbatim — the same payloads that
stream to the browser. Nothing here re-derives what a run did from some parallel source,
which is the property that stops the durable record from drifting away from the live one.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aegis.core.types import GuardVerdict, RunStatus
from aegis.runs.models import Run, RunEvent
from aegis.runs.partitions import partition_name_for

__all__ = [
    "RunEventRecord",
    "RunHeader",
    "RunPartitionMissingError",
    "apply_event",
    "fold_events",
    "load_run_events",
    "load_run_header",
    "read_run_header",
    "rebuild_run_header",
    "reconcile_run_header",
    "record_events",
]

#: Columns of the wire event that are *promoted* to their own ``run_events`` column.
#: They stay in the payload as well: the payload is the event as it streamed, and a
#: reader reconstructing the stream from the log must get back what was sent, not what
#: this module chose to model.
_PROMOTED = ("trace_id", "agent_id", "span_id")


class RunPartitionMissingError(RuntimeError):
    """An event's timestamp falls outside every existing ``run_events`` partition.

    The classic partitioning trap, and the reason this is an exception rather than a
    warning: PostgreSQL rejects the row (``no partition of relation … found for row``),
    so without a translation the caller sees a generic ``IntegrityError`` mentioning a
    constraint it never wrote and concludes the event data was bad. It was not — the
    *schema* is missing a month, and the fix is
    :func:`aegis.runs.partitions.ensure_run_event_partitions`.

    Attributes:
        partition: The partition the row would have needed.
        moment: The timestamp that had nowhere to go.
    """

    def __init__(self, partition: str, moment: datetime) -> None:
        """Build the error naming the missing partition and the fix."""
        super().__init__(
            f"run_events has no partition covering {moment.isoformat()} "
            f"(expected {partition!r}); the event was NOT written. Roll the partitions "
            "forward with aegis.runs.partitions.ensure_run_event_partitions()."
        )
        self.partition = partition
        self.moment = moment


@dataclass(frozen=True, slots=True)
class RunEventRecord:
    """One event in the exact shape the fold consumes, whoever produced it.

    The fold has to give the same answer for an event on its way *into* the table and
    the same event read back *out* of it — that is the whole basis for the projection
    being regenerable. Making both sides construct this record first is what guarantees
    it, rather than two nearly-identical folds that drift.

    Attributes:
        event_type: The wire ``type`` discriminator.
        seq: The orchestrator's monotonic per-run counter; the fold's ordering key.
        ts: When the event happened.
        payload: The event as it streamed.
        agent_id: The emitting agent, when one is named (Phase 5).
        trace_id: The OTel trace this run reconciles with in Phoenix.
        span_id: The emitting span, when one is named.
    """

    event_type: str
    seq: int
    ts: datetime
    payload: Mapping[str, Any]
    agent_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None


@dataclass(frozen=True, slots=True)
class RunHeader:
    """The ``runs`` row as data — every field a fold over that run's events.

    Frozen because the fold is a fold: :func:`apply_event` returns a new header rather
    than mutating one, so an incremental update and a full rebuild are the same
    expression evaluated over different-length inputs.
    """

    run_id: str
    tenant_id: int | None = None
    user_id: int | None = None
    agent_id: str | None = None
    trace_id: str | None = None
    status: RunStatus | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    cache_hit: bool = False
    event_count: int = 0
    last_seq: int = -1
    node_count: int = 0
    tool_call_count: int = 0
    approval_count: int = 0
    guardrail_block_count: int = 0
    error_message: str | None = None

    def as_columns(self) -> dict[str, Any]:
        """Return the header as a ``runs`` column mapping, ready to write."""
        return dataclasses.asdict(self)


def _coerce_status(value: Any) -> RunStatus:  # noqa: ANN401 - wire value
    """Return ``value`` as a :class:`RunStatus`, refusing anything else.

    Args:
        value: The ``status`` field of a ``run_finished`` event.

    Returns:
        The corresponding enum member.

    Raises:
        ValueError: If the value is not a known terminal status. Deliberately fatal
            rather than stored as text: ``runs.status`` is a Postgres enum column, so an
            unknown value would fail at the INSERT anyway — but there, it would fail
            with the whole batch already folded and a message about a type cast. Failing
            here names the event and the value.
    """
    if isinstance(value, RunStatus):
        return value
    try:
        return RunStatus(value)
    except ValueError as exc:
        raise ValueError(
            f"run_finished carried status {value!r}, which is not one of "
            f"{[s.value for s in RunStatus]}"
        ) from exc


def apply_event(header: RunHeader, record: RunEventRecord) -> RunHeader:
    """Fold one event onto a header and return the new header.

    The only place a header field is computed. Two derivations are worth stating because
    they are choices, not conventions:

    * **Usage totals come from the terminal ``run_finished`` event**, not from summing
      ``node_finished``. That matches :func:`aegis.agent.harness.run_summary`, which is
      what the live UI shows, and it is the figure the gateway actually metered; summing
      nodes would double-count a cached answer and miss any usage outside a node.
    * **``duration_ms`` is the sum of the node durations**, which is also what
      ``run_summary`` reports. Wall-clock ``finished_at - started_at`` is a different
      quantity — it includes however long a human took at an approval gate — and calling
      that "duration" is how a parked run comes to look like a slow one.

    Args:
        header: The header so far.
        record: The event to fold in.

    Returns:
        A new :class:`RunHeader`. The input is never mutated.

    Raises:
        ValueError: If a ``run_finished`` event carries an unknown status.
    """
    payload = record.payload
    changes: dict[str, Any] = {
        "event_count": header.event_count + 1,
        "last_seq": max(header.last_seq, record.seq),
    }
    if record.agent_id and header.agent_id is None:
        changes["agent_id"] = record.agent_id
    if record.trace_id and header.trace_id is None:
        changes["trace_id"] = record.trace_id
    if header.started_at is None or record.ts < header.started_at:
        changes["started_at"] = record.ts

    match record.event_type:
        case "run_started":
            # The trace id lives on this event; promote it even when the writer did not.
            if header.trace_id is None and payload.get("trace_id"):
                changes["trace_id"] = payload["trace_id"]
        case "node_started":
            changes["node_count"] = header.node_count + 1
        case "node_finished":
            changes["duration_ms"] = header.duration_ms + int(payload.get("duration_ms") or 0)
        case "tool_call":
            changes["tool_call_count"] = header.tool_call_count + 1
        case "approval_required":
            changes["approval_count"] = header.approval_count + 1
        case "guardrail":
            if payload.get("verdict") == GuardVerdict.BLOCK.value:
                changes["guardrail_block_count"] = header.guardrail_block_count + 1
        case "error":
            changes["error_message"] = payload.get("message") or header.error_message
        case "budget_exceeded":
            changes["error_message"] = payload.get("message") or header.error_message
        case "run_finished":
            changes["status"] = _coerce_status(payload.get("status"))
            changes["finished_at"] = record.ts
            changes["prompt_tokens"] = int(payload.get("prompt_tokens") or 0)
            changes["completion_tokens"] = int(payload.get("completion_tokens") or 0)
            changes["cost_usd"] = float(payload.get("cost_usd") or 0.0)
            changes["cache_hit"] = bool(payload.get("cache_hit"))
        case _:
            # Every other event type contributes to the counts above and nothing else.
            # Unknown types are *stored and counted*, never rejected: Phase 5 adds
            # multi-agent events, and a record layer that dropped what it did not
            # recognise would silently lose exactly the events worth inspecting.
            pass
    return dataclasses.replace(header, **changes)


def fold_events(
    run_id: str,
    records: Iterable[RunEventRecord],
    *,
    tenant_id: int | None = None,
    user_id: int | None = None,
) -> RunHeader:
    """Fold a whole event stream into a header, from empty.

    Args:
        run_id: The run these events belong to.
        records: The events, in any order — they are sorted by ``seq`` first, so a
            caller cannot make the projection depend on its read order.
        tenant_id: The owning tenant, which is a property of the run rather than of any
            one event.
        user_id: The user who started it, likewise.

    Returns:
        The folded :class:`RunHeader`.
    """
    header = RunHeader(run_id=run_id, tenant_id=tenant_id, user_id=user_id)
    for record in sorted(records, key=lambda r: r.seq):
        header = apply_event(header, record)
    return header


def _record_from_event(event: Mapping[str, Any], *, ts: datetime) -> RunEventRecord:
    """Build the fold's input record from one wire event.

    Args:
        event: The stamped event dict as :func:`aegis.agent.run_agent` yields it.
        ts: The timestamp to record it at.

    Returns:
        The :class:`RunEventRecord` for this event.

    Raises:
        ValueError: If the event carries no ``type`` or no ``seq``. Both are stamped by
            the orchestrator on every event, so their absence means the caller is
            recording something that never went through it — which would produce a
            record that cannot be replayed in order.
    """
    event_type = event.get("type")
    if not event_type:
        raise ValueError(f"run event has no 'type': {dict(event)!r}")
    seq = event.get("seq")
    if seq is None:
        raise ValueError(f"run event {event_type!r} has no 'seq'; it was not stamped")
    return RunEventRecord(
        event_type=str(event_type),
        seq=int(seq),
        ts=ts,
        payload=dict(event),
        agent_id=event.get("agent_id"),
        trace_id=event.get("trace_id"),
        span_id=event.get("span_id"),
    )


def _record_from_row(row: RunEvent) -> RunEventRecord:
    """Build the fold's input record from a stored ``run_events`` row."""
    return RunEventRecord(
        event_type=row.event_type,
        seq=row.seq,
        ts=row.ts,
        payload=row.payload or {},
        agent_id=row.agent_id,
        trace_id=row.trace_id,
        span_id=row.span_id,
    )


async def record_events(
    session: AsyncSession,
    *,
    run_id: str,
    events: Sequence[Mapping[str, Any]],
    tenant_id: int | None = None,
    user_id: int | None = None,
    job_id: int | None = None,
    agent_id: str | None = None,
    now: datetime | None = None,
) -> RunHeader:
    """Append events to the durable log and fold them onto the run's header.

    The caller is responsible for the transaction (and, on PostgreSQL, for having bound
    the tenant scope with :func:`aegis.governance.rls.set_tenant_scope`): a run's events
    and the header they imply must commit together, or a crash between them would leave
    the projection claiming a run ended when the log says it did not.

    Args:
        session: The async session, inside the caller's transaction.
        run_id: The run these events belong to.
        events: The stamped wire events, in order.
        tenant_id: The owning tenant; ``None`` for a platform-level run.
        user_id: The user who started the run.
        job_id: The durable job that triggered it, when one did.
        agent_id: The agent to attribute events to when they do not name one themselves.
        now: The timestamp to record events at; defaults to the wall clock. Explicit
            rather than left to the database's ``DEFAULT now()`` so the fold sees the
            same instant that is stored — a header computed from a timestamp the writer
            never saw would not be reproducible from the log.

    Returns:
        The run's header after folding the batch in.

    Raises:
        RunPartitionMissingError: If an event's timestamp falls outside every existing
            partition. Nothing in the batch is written.
        ValueError: If an event is unstamped or carries an unknown terminal status.
    """
    ts = now or datetime.now(UTC)
    records = [_record_from_event(event, ts=ts) for event in events]
    header = await read_run_header(session, run_id)
    if header is None:
        header = RunHeader(run_id=run_id, tenant_id=tenant_id, user_id=user_id)
    else:
        header = dataclasses.replace(
            header,
            tenant_id=tenant_id if tenant_id is not None else header.tenant_id,
            user_id=user_id if user_id is not None else header.user_id,
        )
    for record in records:
        session.add(
            RunEvent(
                run_id=run_id,
                tenant_id=tenant_id,
                seq=record.seq,
                ts=record.ts,
                event_type=record.event_type,
                agent_id=record.agent_id or agent_id,
                job_id=job_id,
                trace_id=record.trace_id,
                span_id=record.span_id,
                payload=dict(record.payload),
            )
        )
        header = apply_event(
            header,
            dataclasses.replace(record, agent_id=record.agent_id or agent_id),
        )
    try:
        await session.flush()
    except IntegrityError as exc:
        raise _partition_error(exc, records) from exc
    await _write_header(session, header)
    return header


def _partition_error(exc: IntegrityError, records: Sequence[RunEventRecord]) -> Exception:
    """Translate a missing-partition INSERT failure, and leave anything else alone.

    Args:
        exc: The integrity error PostgreSQL raised.
        records: The batch being written, used to name the month that had nowhere to go.

    Returns:
        A :class:`RunPartitionMissingError` when that is what happened, otherwise the
        original exception — never a swallowed one. Matching on the message text is
        deliberate and narrow: PostgreSQL reports this as a check violation against a
        constraint that does not exist in our schema, so there is no error code to key
        on, and mis-classifying an unrelated integrity error would be worse than not
        translating this one.
    """
    if "no partition of relation" not in str(exc.orig):
        return exc
    moment = min((record.ts for record in records), default=datetime.now(UTC))
    return RunPartitionMissingError(partition_name_for(moment), moment)


async def _write_header(session: AsyncSession, header: RunHeader) -> None:
    """Insert or update the ``runs`` row for ``header``.

    A read-then-write rather than a dialect-specific upsert: this module runs on the
    SQLite unit schema as well as on PostgreSQL, and the transaction is the caller's, so
    the read is already inside it.

    Args:
        session: The async session.
        header: The header to persist.
    """
    row = await session.get(Run, header.run_id)
    columns = header.as_columns()
    if row is None:
        session.add(Run(**columns))
        return
    for name, value in columns.items():
        setattr(row, name, value)


async def load_run_events(
    session: AsyncSession, run_id: str, *, tenant_id: int | None = None
) -> tuple[RunEvent, ...]:
    """Return one run's events, ordered by ``seq``.

    Args:
        session: The async session.
        run_id: The run to read.
        tenant_id: When given, the app-level tenant filter that sits over the database's
            RLS policy — the belt-and-suspenders layer every governed read in this
            codebase carries, and the only layer at all on SQLite.

    Returns:
        The events in stream order.
    """
    statement = select(RunEvent).where(RunEvent.run_id == run_id)
    if tenant_id is not None:
        statement = statement.where(RunEvent.tenant_id == tenant_id)
    result = await session.execute(statement.order_by(RunEvent.seq))
    return tuple(result.scalars().all())


async def read_run_header(session: AsyncSession, run_id: str) -> RunHeader | None:
    """Return the **stored** header for a run, or ``None`` if there is none.

    Args:
        session: The async session.
        run_id: The run to read.

    Returns:
        The header as data, read from the ``runs`` projection — what the console shows.
        Compare it with :func:`rebuild_run_header` to find drift.
    """
    row = await session.get(Run, run_id)
    if row is None:
        return None
    return RunHeader(
        **{field.name: getattr(row, field.name) for field in dataclasses.fields(RunHeader)}
    )


async def load_run_header(session: AsyncSession, run_id: str) -> Run | None:
    """Return the stored ``runs`` ORM row for a run, or ``None``.

    Args:
        session: The async session.
        run_id: The run to read.

    Returns:
        The mapped row, for callers that want the ORM object rather than the fold's
        dataclass.
    """
    return await session.get(Run, run_id)


async def rebuild_run_header(
    session: AsyncSession, run_id: str, *, tenant_id: int | None = None
) -> RunHeader:
    """Recompute a run's header from its events alone.

    Args:
        session: The async session.
        run_id: The run to rebuild.
        tenant_id: Optional app-level tenant filter, passed through to the event read.

    Returns:
        The header the log implies. Nothing is written — see
        :func:`reconcile_run_header` for the half that does.
    """
    rows = await load_run_events(session, run_id, tenant_id=tenant_id)
    stored = await session.get(Run, run_id)
    return fold_events(
        run_id,
        [_record_from_row(row) for row in rows],
        tenant_id=rows[0].tenant_id if rows else (stored.tenant_id if stored else None),
        # The owning user is not carried on an event, so it is preserved from the header
        # being rebuilt. It is the one field the log genuinely cannot regenerate, and
        # saying so here is better than inventing an event to carry it.
        user_id=stored.user_id if stored else None,
    )


async def reconcile_run_header(
    session: AsyncSession, run_id: str, *, tenant_id: int | None = None
) -> tuple[RunHeader, bool]:
    """Rewrite the stored header from the events — **events win**.

    The operation behind the design's tie-break. A projection can only be trusted if
    there is a way to reassert it from the log, and a way that is exercised — see the
    test that runs a stream carrying every event type through both paths.

    Args:
        session: The async session, inside the caller's transaction.
        run_id: The run to reconcile.
        tenant_id: Optional app-level tenant filter for the event read.

    Returns:
        ``(header, changed)`` — the header the events imply, and whether the stored row
        disagreed with it and was corrected.
    """
    rebuilt = await rebuild_run_header(session, run_id, tenant_id=tenant_id)
    stored = await read_run_header(session, run_id)
    changed = stored != rebuilt
    if changed:
        await _write_header(session, rebuilt)
    return rebuilt, changed
