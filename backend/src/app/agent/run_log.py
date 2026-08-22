"""The live agent run's entries in the durable run record — the write side.

**There is no second log here, and that is the whole design**, exactly as in
:mod:`app.jobs.ingest_log`. Phase 3 §3.6 built the append-only, tenant-scoped,
replayable record (``run_events`` plus the regenerable ``runs`` header,
:mod:`aegis.runs.record`) and the ingest pipeline was wired into it — but the *agent*
run, the thing the product exists to do, never was. ``aegis.runs`` was imported by the
demo seeder and by nothing on the live path, so ``runs`` held ninety days of synthetic
corpus and zero real runs: a tenant could send ten questions, have every one of them
metered in ``usage_ledger``, and find no record that a single run had happened.

This module closes that. It stores the **same events** the SSE stream already carried —
:mod:`aegis.agent.events` verbatim, as the wire schema validated them — so what a
console reads back cannot disagree with what the browser was shown.

Four properties, each a line of code rather than a convention:

**It cannot break the answer.** The record is written from a tracked background task
fired *after* the terminal ``run_finished`` has been handed to the client, on its own
session and its own transaction. The user's answer is already delivered; a failure here
costs a log, never a reply. It is not silent either — every failure is logged at
``exception`` level naming the run, and a missing monthly partition is named as such
together with the call that fixes it.

**It is idempotent on ``run_id``.** :func:`record_run` refuses to write a run that
already has a header, so a retry (or a second fire from a resumed generator) appends
nothing and double-counts nothing. ``run_id`` is a uuid minted per run, so this is a
genuine identity check and not a heuristic.

**A run that parks at a gate is recorded twice, and that is not a contradiction of the
line above.** Its *stream* ends at the gate, so the first write is a true header saying
``awaiting_approval``; the continuation a human's decision drives is headless, arrives
minutes later, and belongs to the same run. :func:`record_run_continuation` appends it
and re-folds, numbering from the stored header's ``last_seq`` so ``seq`` stays monotonic
across the park boundary. Its identity check is the header's *status* rather than its
existence — only a run recorded as ``awaiting_approval`` has a continuation outstanding —
because ``record_run``'s check would refuse precisely the row we mean to extend. Without
this half, a dashboard showed a run still waiting for a human that the ``approvals`` row
recorded as approved and completed: two of our own tables disagreeing about one run.

**It is tenant-attributed, through the same RLS every governed write uses.** The tenant
and user are read from the sealed per-request governance context at *fire* time — never
from the client, and never re-derived inside the background task, where the request
context no longer exists — and the session binds them with
:func:`aegis.governance.rls.set_tenant_scope` before the first INSERT. The row therefore
belongs to the tenant that made the run, and the ``tenant_isolation`` policy applies to
it exactly as it applies to a read.

**It respects the partitioning.** ``run_events`` is ``PARTITIONED BY RANGE (ts)``;
:func:`app.data.session.bootstrap` rolls the window forward on every boot, and a row
that still finds no partition surfaces as :class:`~aegis.runs.record.
RunPartitionMissingError` naming the month rather than as a generic integrity error.

**What is deliberately not stored: ``token``.** Those events are transport chunking of
an answer ``generate`` had already produced in full and ``guard_output`` had already
cleared (see ``aegis.agent.graph.stream_answer``) — presentation, not what the run did.
A 300-word answer is some fifty of them, so storing them would make the durable record's
size a function of answer length while adding nothing recoverable: the answer text is
already durable in ``chat_messages`` and ``memory_message``. The demo corpus makes the
same choice, so the real and seeded shapes of a run agree. Their sequence numbers are
still spent — ``seq`` is the position in the stream as it was sent — so the gaps in a
stored run's ``seq`` are exactly the token chunks, which is the honest rendering.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from aegis.core.types import RunStatus
from aegis.runs.record import RunPartitionMissingError, read_run_header, record_events

logger = logging.getLogger(__name__)

__all__ = [
    "PRESENTATION_EVENT_TYPES",
    "TERMINAL_EVENT_TYPE",
    "durable_events",
    "fire_run_record",
    "parked_run_owner",
    "record_run",
    "record_run_continuation",
    "routed_agent_id",
    "save_run_continuation",
]

#: Event types the durable record deliberately omits. See the module docstring.
PRESENTATION_EVENT_TYPES = frozenset({"token"})

#: The terminal event that closes every run, whatever its outcome. Recording fires on it
#: rather than on generator exit, because a disconnected SSE client closes the generator
#: at a ``yield`` and an ``await`` in that unwinding path is not a place to put a
#: database write.
TERMINAL_EVENT_TYPE = "run_finished"

#: Live record-writing tasks, kept referenced so the event loop cannot GC one mid-flight;
#: the done-callback surfaces any exception rather than letting it vanish.
_RECORD_TASKS: set[asyncio.Task[Any]] = set()


def _on_record_done(task: asyncio.Task[Any]) -> None:
    """Discard a finished record task and surface any exception it swallowed.

    Loud, never silent: a background task whose exception nobody retrieves is exactly how
    a subsystem comes to be "wired" and hold nothing. The missing-partition case is named
    separately because its remedy is a schema call rather than a code fix, and the
    generic line would send a reader hunting for a bug in the writer.
    """
    _RECORD_TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        return
    if isinstance(exc, RunPartitionMissingError):
        logger.error(
            "A run was NOT recorded: %s The answer itself was delivered; roll the "
            "partitions forward with "
            "aegis.runs.partitions.ensure_run_event_partitions().",
            exc,
            exc_info=exc,
        )
        return
    logger.error("Durable run record failed", exc_info=exc)


def durable_events(
    events: Iterable[Any], timestamps: Sequence[datetime] | None = None
) -> tuple[list[dict[str, Any]], list[datetime] | None]:
    """Return the stream's events as plain dicts, minus the presentation-only ones.

    Args:
        events: The stamped wire events, in order. Either the host's ``StreamEvent``
            models (the live path) or plain mappings (tests, and any host that injected
            the default dict stamp).
        timestamps: When each event was emitted, positionally. Filtered alongside the
            events so the two stay aligned — a drift here would file a run's events under
            each other's instants, which is worse than having no times at all.

    Returns:
        ``(records, times)``. The dicts are JSON-safe: ``mode="json"`` because the
        payload lands in a ``jsonb`` column, and an enum or a ``datetime`` left as a
        Python object is not something the driver can serialise — discovering that inside
        the background task would lose the whole run's record over one field.
    """
    out: list[dict[str, Any]] = []
    times: list[datetime] | None = None if timestamps is None else []
    for index, event in enumerate(events):
        payload = (
            dict(event) if isinstance(event, Mapping) else event.model_dump(mode="json")
        )
        if payload.get("type") in PRESENTATION_EVENT_TYPES:
            continue
        out.append(payload)
        if times is not None and timestamps is not None and index < len(timestamps):
            times.append(timestamps[index])
    if times is not None and len(times) != len(out):
        # Positional alignment is the whole contract; without it the times are not
        # merely imprecise, they are attached to the wrong events. Drop them and let the
        # record layer fall back to one batch instant, loudly.
        logger.warning(
            "Run-record timestamps did not line up with its events (%d vs %d); "
            "recording the batch at one instant instead.",
            len(times),
            len(out),
        )
        times = None
    return out, times


def routed_agent_id(events: Sequence[Mapping[str, Any]]) -> str | None:
    """Return the specialist this run was dispatched to, from its ``routing`` event.

    ``run_events.agent_id`` is "which agent emitted this", and on a single-pass run the
    answer is "the supervisor", which every event spells as ``None``. Left at that, a
    real run's header would carry no lane at all while a seeded one carries ``qa`` — two
    shapes for one table, and a runs list that can only caption the fabricated half.

    The supervisor's own ``routing`` event already names the specialist it handed the
    turn to, so this reads it rather than inventing a second notion of who ran. It is
    passed as :func:`aegis.runs.record.record_events`' *default* attribution, which
    applies only to events that name no agent themselves — so a fan-out's per-lane rows
    keep their real sub-agent ids and the header still folds to ``team``.

    Args:
        events: The run's stored events, in order.

    Returns:
        The specialist role (``"qa"``, ``"memory"``, …), or ``None`` when the run never
        reached the router (a blocked input rail, an error before dispatch).
    """
    for event in events:
        if event.get("type") == "routing":
            role = event.get("role")
            return str(role) if role else None
    return None


async def record_run(
    *,
    run_id: str,
    events: Sequence[Mapping[str, Any]],
    tenant_id: int | None,
    user_id: int | None,
    timestamps: Sequence[datetime] | None = None,
) -> bool:
    """Write one completed run's events and folded header, or say why it did not.

    Args:
        run_id: The run's id — also the idempotency key.
        events: The run's stamped events, already filtered by :func:`durable_events`.
        tenant_id: The tenant the run belongs to, from the governance context. ``None``
            is a platform-level run (an operator probe), which is invisible to every
            tenant under ``tenant_isolation`` — the intended reading, not a leak.
        user_id: The user who asked, likewise.
        timestamps: When each event was emitted. The whole run is written in one
            transaction at the end, so without these every row would carry the flush
            time and the header would fold ``started_at == finished_at`` — a runs list
            reporting that every run took no time at all.

    Returns:
        ``True`` when this call wrote the run, ``False`` when there was nothing to write
        or the run was already recorded.
    """
    if not events:
        return False
    from app.data.session import get_sessionmaker, set_tenant_scope

    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, tenant_id)
        if await read_run_header(session, run_id) is not None:
            # A retry, or a second fire for one run. Appending would double the event
            # count and re-fold a header that is already correct, so it does neither.
            logger.info("Run %s is already recorded; not recording it again.", run_id)
            return False
        await record_events(
            session,
            run_id=run_id,
            events=events,
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=routed_agent_id(events),
            timestamps=timestamps,
        )
        await session.commit()
    return True


def _numbered(
    run_id: str,
    base_seq: int,
    payloads: Sequence[Mapping[str, Any]],
    timestamps: Sequence[datetime] | None,
) -> tuple[list[dict[str, Any]], list[datetime] | None]:
    """Stamp a resumed run's continuation so its ``seq`` continues the parked half's.

    ``seq`` is the fold's ordering key (:func:`aegis.runs.record.fold_events` sorts by
    it), so a continuation numbered from zero would interleave with the parked half and
    the header would be folded in the wrong order — ``finished_at`` taken from the park
    rather than from the completion. Numbering from the stored header's ``last_seq``
    keeps one monotonic sequence across the park boundary, which is the only reading
    under which the log replays as the run happened.

    The numbering is applied **before** :func:`durable_events` filters, so the gaps a
    stored run's ``seq`` shows are the token chunks on both sides of the park — the same
    honest rendering the live path gives.

    Args:
        run_id: The run being continued.
        base_seq: The first sequence number available to the continuation.
        payloads: The continuation's events, in order, as the graph emitted them.
        timestamps: When each was collected, positionally.

    Returns:
        ``(records, times)`` ready for :func:`aegis.runs.record.record_events`.
    """
    from app.agent.events import stamp

    stamped: list[Any] = []
    for offset, payload in enumerate(payloads):
        numbered = {**dict(payload), "run_id": run_id, "seq": base_seq + offset}
        try:
            stamped.append(stamp(dict(payload), run_id=run_id, seq=base_seq + offset))
        except Exception:  # noqa: BLE001 - a shape the wire schema refuses is still data
            # Deliberately not fatal, and deliberately not silent. The continuation's
            # payloads come from the same builders the live stream validates, so this
            # cannot fire without something having genuinely drifted — and losing a whole
            # run's record over one event would be the wrong way to find that out.
            logger.warning(
                "A continuation event of run %s did not validate against the wire "
                "schema; recording it unvalidated.",
                run_id,
                exc_info=True,
            )
            stamped.append(numbered)
    return durable_events(stamped, timestamps)


async def parked_run_owner(run_id: str, *, tenant_id: int | None) -> int | None:
    """Return the user a parked run belongs to, from the run's own stored header.

    **Not the approver, and that is the whole point.** The person who decides a gate is
    frequently not the person whose run it is — an admin clearing an inbox, say — so the
    approver's id is the wrong answer to "whose spend is this?". The header carries the
    owner the run was created under, which is the right one.

    Used by :func:`app.agent.resume_parked_run` to bind a governance context around the
    headless continuation. Without it the continuation ran **ungoverned**: measured on
    ``taif_run1``, a rejected run's resume made real model calls and wrote *zero*
    ``usage_ledger`` rows, because the gateway skips both enforcement and ledgering when
    no context is bound. The money was spent, capped by nothing and recorded nowhere.

    Args:
        run_id: The parked run.
        tenant_id: The tenant to bind the read under — resolved from the ``approvals``
            row, never from the caller's context (the decision endpoints bind none).

    Returns:
        The run's ``user_id``, or ``None`` when the run has no header, no owner, or the
        read fails. ``None`` is safe here: it degrades to tenant-level attribution and
        tenant-level caps, which is strictly better than no governance at all.
    """
    try:
        from app.data.session import get_sessionmaker, set_tenant_scope

        async with get_sessionmaker()() as session:
            await set_tenant_scope(session, tenant_id)
            header = await read_run_header(session, run_id)
            return header.user_id if header is not None else None
    except Exception:  # noqa: BLE001 - never break a resume to learn who owns it
        logger.warning(
            "Could not read run %s's header to find its owner; its continuation will be "
            "governed and ledgered at tenant scope with no user attributed.",
            run_id,
            exc_info=True,
        )
        return None


async def record_run_continuation(
    *,
    run_id: str,
    events: Sequence[Mapping[str, Any]],
    tenant_id: int | None,
    timestamps: Sequence[datetime] | None = None,
) -> bool:
    """Append a resumed run's continuation to its log and re-fold its header.

    The other half of :func:`record_run`. A run that parks at a human approval gate ends
    its *stream* at the gate, so ``record_run`` wrote a header saying
    ``awaiting_approval`` — correct at the time, and permanently wrong afterwards,
    because the continuation the human's decision drove was headless and its events went
    nowhere. The ``approvals`` row said approved; the ``runs`` row said still waiting.

    Appending here re-folds the header through the same
    :func:`aegis.runs.record.apply_event` the first half went through, so the run ends up
    with the terminal status the continuation reached, the true ``finished_at``, and the
    node durations of *both* halves summed into ``duration_ms``.

    **Idempotent, on the header's own status.** ``record_run``'s guard ("this run already
    has a header") is exactly wrong here — the header is the thing we are appending to —
    so the identity check is the state the header is in: only a run recorded as
    ``awaiting_approval`` has an outstanding continuation. A second resume, a replayed
    decision, or a retry finds a terminal header and appends nothing. (Two further layers
    sit above it: the optimistic ``PENDING → RESUMING`` transition means only one caller
    ever resumes, and a completed checkpoint has no pending step to drive.)

    Args:
        run_id: The run being continued.
        events: The continuation's events, in order, unfiltered and unnumbered.
        tenant_id: The tenant from the sealed governance context — never the client, and
            never re-derived from the parked run's own row.
        timestamps: When each event was emitted, positionally.

    Returns:
        ``True`` when this call appended the continuation, ``False`` when there was
        nothing to append or the run was already recorded as finished.
    """
    if not events:
        return False
    from app.data.session import get_sessionmaker, set_tenant_scope

    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, tenant_id)
        header = await read_run_header(session, run_id)
        if header is None:
            logger.error(
                "Run %s resumed from its approval gate but has NO durable header to "
                "append to, so its continuation was not recorded. The parked half never "
                "landed — the SSE client went away before it could be written, or the "
                "run parked while stores were off — which leaves the approvals row as "
                "the only account of what happened.",
                run_id,
            )
            return False
        if header.tenant_id is not None and header.tenant_id != tenant_id:
            # Cheap, and it closes the one way this path could cross a boundary: the
            # tenant is resolved from the *approval* row and the events are appended to
            # the *run*, so a mismatch means those two records disagree about who owns
            # the run. Postgres' WITH CHECK would refuse the INSERT anyway; refusing here
            # says why, and says it before half a run's events are built.
            logger.error(
                "Refusing to append run %s's continuation: the run belongs to tenant "
                "%s but the resume resolved tenant %s.",
                run_id,
                header.tenant_id,
                tenant_id,
            )
            return False
        if header.status is not RunStatus.AWAITING_APPROVAL:
            logger.info(
                "Run %s is recorded as %s rather than parked, so its continuation was "
                "already appended; not appending it twice.",
                run_id,
                header.status.value if header.status else "in flight",
            )
            return False
        records, times = _numbered(run_id, header.last_seq + 1, events, timestamps)
        if not records:
            return False
        await record_events(
            session,
            run_id=run_id,
            events=records,
            tenant_id=tenant_id,
            # None on purpose: ``record_events`` preserves the header's own user, and the
            # person who approved a gate is not the person whose run it is. Overwriting
            # it with the approver would re-attribute the run to its reviewer.
            user_id=None,
            agent_id=routed_agent_id(records) or header.agent_id,
            timestamps=times,
        )
        await session.commit()
    return True


async def save_run_continuation(
    *,
    run_id: str,
    events: Sequence[Mapping[str, Any]],
    tenant_id: int | None,
    timestamps: Sequence[datetime] | None = None,
) -> bool:
    """Append a continuation, loudly tolerating any failure.

    Awaited rather than fired as a background task, unlike :func:`fire_run_record`. There
    is no stream to protect here — the decision request has already driven the whole
    graph synchronously — and awaiting buys the property that matters to a console: by
    the time ``POST /v1/approvals/{id}/decision`` answers, the ``runs`` row and the
    ``approvals`` row agree. One commit is not what makes that request slow.

    Returns:
        ``True`` when the continuation was appended; ``False`` on any failure or no-op.
    """
    try:
        from app.config import get_settings

        if not get_settings().stores_enabled:
            return False
        return await record_run_continuation(
            run_id=run_id,
            events=events,
            tenant_id=tenant_id,
            timestamps=timestamps,
        )
    except RunPartitionMissingError as exc:
        logger.error(
            "A resumed run was NOT recorded: %s The approved action itself ran; roll the "
            "partitions forward with aegis.runs.partitions.ensure_run_event_partitions().",
            exc,
            exc_info=exc,
        )
        return False
    except Exception:  # noqa: BLE001 - recording must never break the resume
        logger.exception(
            "Appending run %s's continuation to the durable record failed. The approved "
            "action ran and the approvals row is correct, but the run header still says "
            "awaiting_approval; aegis.runs.record.reconcile_run_header will not fix it, "
            "because the events are missing from the log too.",
            run_id,
        )
        return False


def fire_run_record(
    *,
    run_id: str,
    events: Sequence[Any],
    tenant_id: int | None,
    user_id: int | None,
    timestamps: Sequence[datetime] | None = None,
) -> None:
    """Schedule the durable record off the hot path (tracked; never blocks the stream).

    Mirrors :func:`app.agent.orchestrator._fire_trace_eval` — the established shape for
    "do this after the run, never at the cost of the run". Gated on
    ``settings.stores_enabled`` because the offline lite demo has no database to write
    to, and that is a configuration rather than a failure.

    Args:
        run_id: The run that just reached its terminal event.
        events: Every event the run streamed, in order (unfiltered).
        tenant_id: The owning tenant, resolved from the governance context *here*, while
            the request's context is still bound.
        user_id: The acting user, likewise.
        timestamps: When each event was emitted, positionally.
    """
    try:
        from app.config import get_settings

        if not get_settings().stores_enabled:
            return
        records, times = durable_events(events, timestamps)
        if not records:
            return
        task = asyncio.create_task(
            record_run(
                run_id=run_id,
                events=records,
                tenant_id=tenant_id,
                user_id=user_id,
                timestamps=times,
            )
        )
        _RECORD_TASKS.add(task)
        task.add_done_callback(_on_record_done)
    except Exception:  # noqa: BLE001 - the kickoff must never disturb the stream
        logger.exception("Durable run-record kickoff failed for run %s", run_id)
