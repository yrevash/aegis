"""The header fold, proved over a run carrying every event type.

The ``runs`` header is a second table in a design whose rule is one tracking mechanism,
and it earns its place by being a **regenerable projection**: every field is a fold over
the events and nothing else. These tests are that claim, at the level where it is cheap
to check — the live suite then proves the same thing through PostgreSQL, over rows that
made a round trip.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime

import pytest

from aegis.core.types import RunStatus
from aegis.runs.record import RunEventRecord, RunHeader, apply_event, fold_events

from ._stream import every_event_type_covered, full_run

_TS = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


def _records(events, ts=_TS):  # noqa: ANN001, ANN202 - list[dict] -> list[RunEventRecord]
    """Turn stamped wire events into the records the fold consumes."""
    return [
        RunEventRecord(
            event_type=event["type"],
            seq=event["seq"],
            ts=ts,
            payload=event,
            trace_id=event.get("trace_id"),
        )
        for event in events
    ]


def test_the_synthetic_run_really_does_carry_every_event_type():
    """Otherwise every claim below is about a subset nobody named."""
    buildable, emitted = every_event_type_covered()
    assert emitted == buildable, (
        "aegis.agent.events can build event types the projection has never been folded "
        f"over: {sorted(buildable - emitted)}. Add them to tests/runs/_stream.py — the "
        "header is only proved against the stream it is tested with."
    )


def test_the_fold_derives_every_header_field_from_the_events():
    header = fold_events("run-full", _records(full_run()), tenant_id=7, user_id=3)

    assert header.run_id == "run-full"
    assert header.tenant_id == 7
    assert header.user_id == 3
    assert header.trace_id == "trace-abcdef"
    assert header.status is RunStatus.COMPLETED
    assert header.event_count == 25
    assert header.last_seq == 24
    assert header.node_count == 2  # two node_started events
    assert header.tool_call_count == 1
    assert header.approval_count == 1
    assert header.guardrail_block_count == 1  # the PASS verdict is not a block
    assert header.duration_ms == 420  # 120 + 300, summed from node_finished
    # Usage comes from the terminal event, which is what the gateway metered and what
    # run_summary reports — not from re-summing the nodes.
    assert (header.prompt_tokens, header.completion_tokens) == (110, 20)
    assert header.cost_usd == pytest.approx(0.42)
    assert header.cache_hit is False
    assert header.error_message == "over the cap"


def test_the_fold_is_independent_of_the_order_events_arrive_in():
    """A projection whose value depended on read order could not be regenerated."""
    events = full_run()
    shuffled = events[:]
    random.Random(7).shuffle(shuffled)
    assert fold_events("run-full", _records(shuffled)) == fold_events(
        "run-full", _records(events)
    )


def test_folding_never_mutates_the_header_it_was_given():
    """``apply_event`` returns a new header, which is what makes rebuild == increment."""
    start = RunHeader(run_id="r")
    after = apply_event(start, _records(full_run())[0])
    assert start.event_count == 0
    assert after.event_count == 1
    assert after is not start


def test_an_incremental_fold_equals_a_single_pass_over_the_whole_stream():
    """The property the ``runs`` header rests on, in one line.

    ``record_events`` folds each batch onto the stored header as it writes; the rebuild
    folds the lot from empty. They agree because they are the same reduction, and this
    fails the moment one of them grows a shortcut.
    """
    records = _records(full_run())
    incremental = RunHeader(run_id="run-full", tenant_id=7)
    for record in records:
        incremental = apply_event(incremental, record)
    assert incremental == fold_events("run-full", records, tenant_id=7)


def test_an_unstamped_event_is_refused_rather_than_recorded_out_of_order():
    from aegis.runs.record import _record_from_event

    with pytest.raises(ValueError, match="was not stamped"):
        _record_from_event({"type": "token", "text": "hi"}, ts=_TS)
    with pytest.raises(ValueError, match="no 'type'"):
        _record_from_event({"seq": 0}, ts=_TS)


def test_an_unknown_terminal_status_fails_loudly_at_the_fold():
    """``runs.status`` is an enum column, so a bad value must not reach the INSERT.

    Failing here names the event and the value; failing at the INSERT names a type cast
    in a batch that has already been folded.
    """
    bad = [{"type": "run_finished", "status": "sort-of-finished", "seq": 0}]
    with pytest.raises(ValueError, match="sort-of-finished"):
        fold_events("r", _records(bad))


def test_started_at_is_the_earliest_event_not_the_first_folded():
    early = RunEventRecord("token", 5, datetime(2026, 8, 1, tzinfo=UTC), {"type": "token"})
    late = RunEventRecord("token", 6, datetime(2026, 8, 2, tzinfo=UTC), {"type": "token"})
    assert fold_events("r", [late, early]).started_at == early.ts
