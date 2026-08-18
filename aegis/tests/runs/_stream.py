"""One synthetic run carrying **every** event type the agent can emit.

Shared by the fold tests and the live rebuild test, and built from the real
:mod:`aegis.agent.events` builders rather than from hand-written dicts, so a change to a
payload's shape reaches these tests instead of being mirrored wrongly in them.

:func:`every_event_type_covered` is the guard that keeps "every event type" honest: the
builder function names *are* the wire ``type`` discriminators, so the set of builders in
that module and the set of types in :func:`full_run` must match exactly. Adding an event
to the agent without adding it here fails the suite rather than quietly narrowing what
the projection is proved against.
"""

from __future__ import annotations

import inspect
from typing import Any

from aegis.agent import events
from aegis.core.types import GuardStage, GuardVerdict, RiskLevel, RunStatus


class _Provenance:
    """The duck-typed shape :func:`aegis.agent.events.provenance` reads."""

    origins = ["vector"]
    fusion = "rrf"
    cache = None


def full_run(run_id: str = "run-full") -> list[dict[str, Any]]:
    """Return one complete, stamped run: every event type, in a plausible order.

    Args:
        run_id: The run id to stamp on every event.

    Returns:
        The ordered event dicts, each carrying ``run_id`` and a monotonic ``seq`` exactly
        as :func:`aegis.agent.run_agent`'s stamp adds them.
    """
    payloads = [
        events.run_started("trace-abcdef"),
        events.routing(role="qa", reason="default", used_llm=False),
        events.guardrail(GuardStage.INPUT, GuardVerdict.PASS, "clean"),
        events.memory(recalled_fact_count=2, recalled_message_count=3, tokens_used=40),
        events.node_started("retrieve", "Retrieving"),
        events.retrieval("ok", num_candidates=5),
        events.provenance(_Provenance(), cache_hit=False),
        events.node_finished(
            "retrieve",
            "Retrieving",
            120,
            model="embed",
            prompt_tokens=10,
            completion_tokens=0,
            cost_usd=0.001,
        ),
        events.node_started("plan", "Planning"),
        events.reasoning("first, look up the policy"),
        events.node_finished("plan", "Planning", 300, model="gpt", prompt_tokens=100),
        events.tool_call("call-1", "escalate", {"id": "R1"}, RiskLevel.HIGH),
        events.approval_required(
            "appr-1", action="escalate", args={"id": "R1"}, risk=RiskLevel.HIGH,
            rationale="destructive",
        ),
        events.approval_queued(
            "appr-1", action="escalate", args={"id": "R1"}, risk=RiskLevel.HIGH,
            rationale="destructive",
        ),
        events.tool_result("call-1", True, "escalated"),
        events.reflection(
            iteration=1, max_iterations=2, done=False, will_retry=True, reason="retry"
        ),
        events.guardrail(GuardStage.OUTPUT, GuardVerdict.BLOCK, "leaked a secret"),
        events.token("The answer "),
        events.token("is 42."),
        events.error("a downstream call failed"),
        events.budget_exceeded(
            scope="tenant", scope_id=1, limit_type="usd_cap", limit=10.0, used=11.0,
            message="over the cap",
        ),
        events.run_finished(
            RunStatus.COMPLETED,
            prompt_tokens=110,
            completion_tokens=20,
            cost_usd=0.42,
            cache_hit=False,
        ),
    ]
    return [{**payload, "run_id": run_id, "seq": seq} for seq, payload in enumerate(payloads)]


def every_event_type_covered() -> tuple[set[str], set[str]]:
    """Return ``(types the agent can build, types full_run emits)``.

    Returns:
        Two sets a test can compare. They are equal when :func:`full_run` really does
        carry every event type.
    """
    buildable = {
        name
        for name, fn in inspect.getmembers(events, inspect.isfunction)
        if not name.startswith("_") and fn.__module__ == events.__name__
    }
    emitted = {event["type"] for event in full_run()}
    return buildable, emitted
