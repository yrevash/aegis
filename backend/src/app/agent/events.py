"""Backend shim: the streamed-event dict builders now live in ``aegis.agent.events``.

Graph nodes never construct the wire model directly — they emit *partial* event
dicts, and the orchestrator stamps each with the run-scoped ``run_id`` and a
monotonic ``seq`` before validating it against the locked
:data:`~app.api.schemas.StreamEvent` discriminated union.

The plain dict *builders* moved into the standalone ``aegis.agent.events`` (they
depend on no host schema). The one host coupling — :func:`stamp`, which validates a
built dict against this platform's locked ``StreamEvent`` union — stays here and is
injected into ``aegis.agent``'s ``run_agent`` as the event-validator seam. This
module re-exports every builder by identity so existing ``events.<builder>`` call
sites are unchanged.
"""

from __future__ import annotations

from typing import Any

from aegis.agent.events import (
    approval_queued,
    approval_required,
    budget_exceeded,
    error,
    guardrail,
    memory,
    node_finished,
    node_started,
    provenance,
    reasoning,
    reflection,
    retrieval,
    routing,
    run_finished,
    run_started,
    token,
    tool_call,
    tool_result,
)
from pydantic import TypeAdapter

from app.api.schemas import StreamEvent

__all__ = [
    "approval_queued",
    "approval_required",
    "budget_exceeded",
    "error",
    "guardrail",
    "memory",
    "node_finished",
    "node_started",
    "provenance",
    "reasoning",
    "reflection",
    "retrieval",
    "routing",
    "run_finished",
    "run_started",
    "stamp",
    "token",
    "tool_call",
    "tool_result",
]

# A reusable validator for the whole discriminated union (built once).
_STREAM_EVENT_ADAPTER: TypeAdapter[StreamEvent] = TypeAdapter(StreamEvent)


def stamp(payload: dict[str, Any], *, run_id: str, seq: int) -> StreamEvent:
    """Stamp a partial event ``payload`` with correlation fields and validate it.

    This is the **event-validator seam** injected into ``aegis.agent``'s ``run_agent``:
    the graph core produces validator-agnostic dicts, and this host binds them to the
    locked wire schema.

    Args:
        payload: A builder dict carrying at least a ``type`` discriminator.
        run_id: The id correlating every event of one run.
        seq: The monotonic sequence number within the run.

    Returns:
        The concrete, validated :data:`~app.api.schemas.StreamEvent` variant.
    """
    return _STREAM_EVENT_ADAPTER.validate_python(
        {**payload, "run_id": run_id, "seq": seq}
    )
