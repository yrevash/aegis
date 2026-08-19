"""Harness data accessors: the tweakable-config schema + the per-run trace record.

These are the two DATA surfaces the (later) harness UI renders. Both are derived from
artefacts the agent ALREADY produces, so neither can drift from real behaviour:

- :func:`harness_config` reflects EVERY :class:`~aegis.agent.deps.AgentConfig` knob as a
  typed, defaulted, bounded descriptor (the "tweak the agent" panel) alongside the
  effective value. It is pure metadata over :meth:`AgentConfig.as_dict` — the same knobs
  the graph actually reads.
- :func:`run_summary` folds the SAME ordered event stream :func:`aegis.agent.run_agent`
  emits into one structured record: the nodes touched (with per-node duration / model /
  tokens / cost), the planner's reasoning, the gate decision + risk tier, the tool calls
  and their results, the bounded self-repair iterations, the recalled memory, the final
  answer and the terminal outcome. Because it consumes the emitted events verbatim, the
  "how it worked" record can never diverge from what streamed to the client.

Neither accessor imports a host schema; both operate on plain dicts (and, defensively,
on any attribute-bearing wire event) so they are reusable from the pure package and from
a host that stamps its own event models.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from aegis.core.types import RiskLevel, RunStatus

from .deps import AgentConfig

__all__ = ["harness_config", "run_summary"]


# ── Tweakable-config schema ───────────────────────────────────────────────────


@dataclass(frozen=True)
class _KnobSpec:
    """Static UI metadata for one tweakable :class:`AgentConfig` knob."""

    key: str
    type: str  # "bool" | "int" | "float" | "enum" | "str"
    doc: str
    allowed: tuple[Any, ...] | None = None
    minimum: float | None = None
    maximum: float | None = None
    nullable: bool = False


# The full, ordered catalogue of tweakable knobs. Every AgentConfig field appears
# here exactly once (guarded by ``test_harness_config_covers_every_knob``); the order
# is a sensible UI grouping (autonomy first, then retrieval, then streaming).
_KNOB_SPECS: tuple[_KnobSpec, ...] = (
    _KnobSpec(
        "gate_min_risk",
        "enum",
        "Minimum tool-risk tier that forces the human approval gate. This is the ONLY "
        "gating signal.",
        allowed=tuple(r.value for r in RiskLevel),
    ),
    _KnobSpec(
        "self_repair_enabled",
        "bool",
        "Enable the bounded Reflexion self-repair loop (reflect → re-plan after a failed "
        "or insufficient action). Off = a single linear pass.",
    ),
    _KnobSpec(
        "max_plan_iterations",
        "int",
        "Hard cap on planning rounds — guarantees termination. 1 = single linear pass; "
        "the default 2 allows one re-plan.",
        minimum=1,
    ),
    _KnobSpec(
        "query_rewrite_enabled",
        "bool",
        "Run a cheap, context-aware query rewrite before retrieval.",
    ),
    _KnobSpec(
        "agentic_retrieval_enabled",
        "bool",
        "Run the bounded Self-RAG/FLARE loop (retrieve → judge sufficiency → reformulate "
        "→ re-retrieve).",
    ),
    _KnobSpec(
        "agentic_retrieval_max_rounds",
        "int",
        "Maximum rounds the agentic-retrieval loop may take before finalising.",
        minimum=1,
    ),
    _KnobSpec(
        "answer_cache_enabled",
        "bool",
        "Reuse a semantically-equivalent prior answer (scoped per tenant+persona+role), "
        "skipping the generation call.",
    ),
    _KnobSpec(
        "stream_chunk_words",
        "int",
        "How many words per streamed answer 'token' event.",
        minimum=1,
    ),
    _KnobSpec(
        "approval_park_timeout",
        "float",
        "Seconds the live socket holds a gate open before parking the run. None waits "
        "indefinitely (the live money-shot gate).",
        minimum=0.0,
        nullable=True,
    ),
    _KnobSpec(
        "default_persona_id",
        "str",
        "The persona id a run falls back to when the request names none.",
    ),
    _KnobSpec(
        "team_enabled",
        "bool",
        "Master switch for the adaptive multi-agent fan-out. Off = every turn runs "
        "single-pass, whatever the classifier or the user asked for.",
    ),
    _KnobSpec(
        "max_parallel_agents",
        "int",
        "Platform cap on team width. A user's explicit width is narrowed by this and "
        "never widened past it.",
        minimum=1,
        maximum=8,
    ),
    _KnobSpec(
        "max_concurrent_agents",
        "int",
        "How many sub-agents may hold a gateway slot at once (the semaphore over the "
        "fan-out).",
        minimum=1,
        maximum=8,
    ),
    _KnobSpec(
        "subagent_max_steps",
        "int",
        "Hard step cap on one sub-agent's bounded ReAct loop — the guarantee it "
        "terminates.",
        minimum=1,
        maximum=10,
    ),
    _KnobSpec(
        "subagent_timeout_s",
        "float",
        "Per-sub-agent wall clock. Exceeding it is a DESIGNED terminal state: the agent "
        "is named as omitted and its siblings finish.",
        minimum=1.0,
    ),
    _KnobSpec(
        "team_wall_clock_s",
        "float",
        "Wall clock for the whole fan-out. Whatever has not landed by then is omitted, "
        "and the synthesis says so.",
        minimum=1.0,
    ),
)

_DEFAULTS: dict[str, Any] = AgentConfig().as_dict()


def harness_config(config: AgentConfig | None = None) -> dict[str, Any]:
    """Return the tweakable-config record for the harness UI.

    Args:
        config: The effective configuration to report values from. Defaults to a fresh
            :class:`AgentConfig` (all-defaults) when omitted.

    Returns:
        A dict with two keys:

        - ``knobs``: an ordered list of knob descriptors, each carrying ``key``, ``type``,
          the effective ``value``, the ``default``, a human ``doc`` string and — where
          they apply — ``allowed`` values, ``minimum``/``maximum`` bounds and a
          ``nullable`` flag. This is the metadata a UI needs to render an editable form.
        - ``effective``: the flat effective-values map (:meth:`AgentConfig.as_dict`),
          exactly what the graph reads.
    """
    config = config or AgentConfig()
    effective = config.as_dict()
    knobs: list[dict[str, Any]] = []
    for spec in _KNOB_SPECS:
        knob: dict[str, Any] = {
            "key": spec.key,
            "type": spec.type,
            "value": effective[spec.key],
            "default": _DEFAULTS[spec.key],
            "doc": spec.doc,
        }
        if spec.allowed is not None:
            knob["allowed"] = list(spec.allowed)
        if spec.minimum is not None:
            knob["minimum"] = spec.minimum
        if spec.maximum is not None:
            knob["maximum"] = spec.maximum
        if spec.nullable:
            knob["nullable"] = True
        knobs.append(knob)
    return {"knobs": knobs, "effective": effective}


# ── Per-run trace record ──────────────────────────────────────────────────────


def _get(ev: Any, key: str, default: Any = None) -> Any:  # noqa: ANN401 - dict|model
    """Read ``key`` off an event that is either a mapping or an attribute-bearing model."""
    if isinstance(ev, Mapping):
        return ev.get(key, default)
    return getattr(ev, key, default)


def _etype(ev: Any) -> Any:  # noqa: ANN401 - event type discriminator
    """Return the event's ``type`` discriminator."""
    return _get(ev, "type")


#: The ``agent_status`` values that end a lane. The last one a lane emits is its
#: terminal state; ``timeout`` is a DESIGNED one, not an error.
_TERMINAL_AGENT_STATUS = frozenset({"done", "failed", "timeout"})


def _agent_records(agent_events: list[Any]) -> list[dict[str, Any]]:
    """Fold the agent-stamped slice of the stream into ONE record per sub-agent.

    Same fold, one dimension down: a lane emits ``node_started``/``node_finished`` (its
    model, tokens, cost and duration), ``agent_status`` beats, ``reasoning`` and its
    ``tool_call``/``tool_result`` pairs — all of them stamped with its ``agent_id``, so
    the record for one agent is a projection of exactly what that agent streamed.

    A proposed HIGH-risk action shows here as a tool call with ``ok`` still ``None``:
    the lane asked, and the absence of a result in this lane is the honest signal that
    **nothing ran here**. It runs, or does not, at the main graph's one human gate.

    Args:
        agent_events: The events carrying an ``agent_id``, in stream order.

    Returns:
        One record per agent, in the order the agents first appear on the wire.
    """
    grouped: dict[str, list[Any]] = {}
    for event in agent_events:
        grouped.setdefault(str(_get(event, "agent_id")), []).append(event)
    return [_one_agent(agent_id, evs) for agent_id, evs in grouped.items()]


def _one_agent(agent_id: str, events: list[Any]) -> dict[str, Any]:
    """Fold one lane's events into its harness record."""
    beats = [e for e in events if _etype(e) == "agent_status"]
    terminal = [b for b in beats if _get(b, "status") in _TERMINAL_AGENT_STATUS]
    finished = [e for e in events if _etype(e) == "node_finished"]
    last = finished[-1] if finished else None
    tools: list[dict[str, Any]] = []
    by_call: dict[Any, dict[str, Any]] = {}
    for event in events:
        etype = _etype(event)
        if etype == "tool_call":
            rec = {
                "call_id": _get(event, "call_id"),
                "tool": _get(event, "tool"),
                "args": _get(event, "args", {}),
                "risk": _get(event, "risk"),
                "ok": None,
                "summary": None,
            }
            tools.append(rec)
            by_call[rec["call_id"]] = rec
        elif etype == "tool_result":
            rec = by_call.get(_get(event, "call_id"))
            if rec is None:
                continue
            rec["ok"] = _get(event, "ok")
            rec["summary"] = _get(event, "summary")
    return {
        "agent_id": agent_id,
        "role": next((_get(b, "role") for b in beats if _get(b, "role")), None),
        "label": next((_get(b, "label") for b in beats if _get(b, "label")), None),
        "task": next(
            (_get(b, "detail") for b in beats if _get(b, "status") == "started"), None
        ),
        "status": _get(terminal[-1], "status") if terminal else (
            _get(beats[-1], "status") if beats else None
        ),
        "detail": _get(terminal[-1], "detail") if terminal else None,
        "steps": sum(1 for b in beats if _get(b, "status") == "thinking"),
        "model": _get(last, "model") if last is not None else None,
        "duration_ms": _get(last, "duration_ms") if last is not None else None,
        "prompt_tokens": _get(last, "prompt_tokens", 0) if last is not None else 0,
        "completion_tokens": _get(last, "completion_tokens", 0) if last is not None else 0,
        "cost_usd": _get(last, "cost_usd", 0.0) if last is not None else 0.0,
        "reasoning": [_get(e, "text") for e in events if _etype(e) == "reasoning"],
        "tools": tools,
        "event_count": len(events),
    }


def _team_totals(
    agents: list[dict[str, Any]], nodes: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Sum the per-agent records and check them against the fan-out node's own delta.

    The reconciliation is the point. ``run_team`` reports ONE summed
    ``{prompt_tokens, completion_tokens, cost_usd}`` on its ``node_finished`` — the same
    delta the graph's ``operator.add`` reducers fold into the run — so summing the lanes'
    records back up has to give that number. When it does not, an agent's spend went
    somewhere the run never counted, and ``reconciles`` says so instead of the two
    disagreeing quietly on different screens.

    Returns:
        ``None`` when the run had no fan-out. Otherwise the summed per-agent usage plus
        ``node`` (the fan-out node's own reported delta) and ``reconciles``, which is
        ``None`` when no fan-out node reported one.
    """
    if not agents:
        return None
    summed = {
        "agent_count": len(agents),
        "prompt_tokens": sum(int(a["prompt_tokens"] or 0) for a in agents),
        "completion_tokens": sum(int(a["completion_tokens"] or 0) for a in agents),
        "cost_usd": sum(float(a["cost_usd"] or 0.0) for a in agents),
    }
    fanout = [n for n in nodes if n.get("node") == "run_team"]
    node = fanout[-1] if fanout else None
    if node is None:
        return {**summed, "node": None, "reconciles": None}
    reported = {
        "prompt_tokens": int(node.get("prompt_tokens") or 0),
        "completion_tokens": int(node.get("completion_tokens") or 0),
        "cost_usd": float(node.get("cost_usd") or 0.0),
    }
    return {
        **summed,
        "node": reported,
        "reconciles": (
            summed["prompt_tokens"] == reported["prompt_tokens"]
            and summed["completion_tokens"] == reported["completion_tokens"]
            and math.isclose(summed["cost_usd"], reported["cost_usd"], rel_tol=1e-9,
                             abs_tol=1e-12)
        ),
    }


def run_summary(events: Iterable[Any]) -> dict[str, Any]:
    """Fold the emitted event stream into one structured per-run record.

    The record is derived entirely from the events :func:`aegis.agent.run_agent` yields
    (dicts under the default stamp, or a host's wire models) so it is guaranteed
    consistent with what streamed to the client — no separate bookkeeping path.

    Args:
        events: The ordered events from a single run.

    Returns:
        A JSON-friendly dict with: ``run_id``/``trace_id``/``status``; ``nodes`` (ordered,
        each with ``duration_ms``/``model``/token+cost — ``duration_ms`` is ``None`` for a
        node that started but did not finish, e.g. the interrupt-paused ``approval``
        node); ``reasoning`` (the planner's plan chunks); ``guardrails``; ``routing``;
        ``gate`` (gated?/risk tier/action/approval resolution); ``tools`` (call joined to
        its result); ``iterations`` (each bounded self-repair reflection); ``memory``;
        the reassembled ``answer``; ``totals`` (usage + summed node duration);
        ``outcome`` (the terminal ``run_finished`` usage/status); ``agents`` (§5.9a —
        ONE record per sub-agent, each with that agent's own model, tokens, cost,
        duration, reasoning and tool calls); and ``team`` (the summed per-agent usage
        and whether it **reconciles** with the fan-out node's own delta).

        Everything a sub-agent emitted is in its own record and nowhere else: the
        run-level lists are the supervisor's. A single-pass run carries no
        ``agent_id`` anywhere, so its record is byte-for-byte what it always was, with
        ``agents == []`` and ``team is None``.
    """
    events = list(events)

    # §5.9a — the agent dimension. Every event a sub-agent emits is stamped with its
    # ``agent_id`` at the writer seam (§5.4), so folding the record per agent is a
    # PARTITION of the same stream rather than a second source: what a lane emitted
    # belongs to that lane's record, and what carries no identity is supervisor /
    # graph-level work. That is also why the run-level record below is unchanged for
    # every single-pass run — nothing on that path carries an ``agent_id``.
    agent_events = [e for e in events if _get(e, "agent_id")]
    if agent_events:
        events = [e for e in events if not _get(e, "agent_id")]
    agents = _agent_records(agent_events)

    run_id = next((_get(e, "run_id") for e in events if _get(e, "run_id")), None)
    started = [e for e in events if _etype(e) == "run_started"]
    trace_id = _get(started[0], "trace_id") if started else None

    # Nodes: pair node_started with node_finished (LIFO per node name so a node that
    # runs twice — plan in a self-repair loop — yields two ordered records).
    nodes: list[dict[str, Any]] = []
    open_idx: dict[str, list[int]] = {}
    for e in events:
        t = _etype(e)
        if t == "node_started":
            node = _get(e, "node")
            nodes.append(
                {
                    "node": node,
                    "label": _get(e, "label"),
                    "duration_ms": None,
                    "model": None,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cost_usd": 0.0,
                }
            )
            open_idx.setdefault(node, []).append(len(nodes) - 1)
        elif t == "node_finished":
            node = _get(e, "node")
            stack = open_idx.get(node) or []
            idx = stack.pop() if stack else None
            if idx is None:
                nodes.append({"node": node, "label": _get(e, "label")})
                idx = len(nodes) - 1
            nodes[idx].update(
                {
                    "duration_ms": _get(e, "duration_ms"),
                    "model": _get(e, "model"),
                    "prompt_tokens": _get(e, "prompt_tokens", 0),
                    "completion_tokens": _get(e, "completion_tokens", 0),
                    "cost_usd": _get(e, "cost_usd", 0.0),
                }
            )

    # Tools: join each tool_call to its tool_result by call_id, preserving call order.
    tools: list[dict[str, Any]] = []
    by_call: dict[Any, dict[str, Any]] = {}
    for e in events:
        t = _etype(e)
        if t == "tool_call":
            rec = {
                "call_id": _get(e, "call_id"),
                "tool": _get(e, "tool"),
                "args": _get(e, "args", {}),
                "risk": _get(e, "risk"),
                "ok": None,
                "summary": None,
            }
            tools.append(rec)
            by_call[rec["call_id"]] = rec
        elif t == "tool_result":
            rec = by_call.get(_get(e, "call_id"))
            if rec is None:
                rec = {"call_id": _get(e, "call_id"), "tool": None, "args": {}, "risk": None}
                tools.append(rec)
                by_call[rec["call_id"]] = rec
            rec["ok"] = _get(e, "ok")
            rec["summary"] = _get(e, "summary")

    reasoning = [_get(e, "text") for e in events if _etype(e) == "reasoning"]
    guardrails = [
        {
            "stage": _get(e, "stage"),
            "verdict": _get(e, "verdict"),
            "reason": _get(e, "reason"),
            "layer": _get(e, "layer"),
        }
        for e in events
        if _etype(e) == "guardrail"
    ]
    iterations = [
        {
            "iteration": _get(e, "iteration"),
            "max_iterations": _get(e, "max_iterations"),
            "done": _get(e, "done"),
            "will_retry": _get(e, "will_retry"),
            "reason": _get(e, "reason"),
        }
        for e in events
        if _etype(e) == "reflection"
    ]

    routing_ev = next((e for e in events if _etype(e) == "routing"), None)
    routing = (
        {
            "role": _get(routing_ev, "role"),
            "reason": _get(routing_ev, "reason"),
            "used_llm": _get(routing_ev, "used_llm"),
        }
        if routing_ev is not None
        else None
    )

    mem_ev = next((e for e in events if _etype(e) == "memory"), None)
    memory = (
        {
            "recalled_fact_count": _get(mem_ev, "recalled_fact_count"),
            "recalled_message_count": _get(mem_ev, "recalled_message_count"),
            "tokens_used": _get(mem_ev, "tokens_used"),
        }
        if mem_ev is not None
        else None
    )

    finished = [e for e in events if _etype(e) == "run_finished"]
    outcome_ev = finished[-1] if finished else None
    status = _get(outcome_ev, "status") if outcome_ev is not None else None

    # The reported gate is the LAST ``approval_required``: a multi-round self-repair run
    # can gate more than once, and ``resolved``/``approved`` describe where the run ended
    # up, which is that gate.
    gate_idx = next(
        (i for i in range(len(events) - 1, -1, -1) if _etype(events[i]) == "approval_required"),
        None,
    )
    if gate_idx is not None:
        approval_ev = events[gate_idx]
        parked = status == RunStatus.AWAITING_APPROVAL.value
        # Scan only AFTER the gate. Scanning the whole stream mis-reports a REJECTED gate
        # as approved whenever any earlier round already executed a tool: round 1 runs a
        # LOW-risk tool (no gate), round 2 proposes a HIGH-risk one, the human rejects,
        # and the pre-gate ``tool_result`` would otherwise stand in as evidence of
        # execution. A reject routes straight to ``generate``, so nothing executes after
        # the gate it decided.
        executed_after_gate = any(
            _etype(e) == "tool_result" for e in events[gate_idx + 1 :]
        )
        gate = {
            "gated": True,
            "risk": _get(approval_ev, "risk"),
            "action": _get(approval_ev, "action"),
            "args": _get(approval_ev, "args", {}),
            "rationale": _get(approval_ev, "rationale"),
            "approval_id": _get(approval_ev, "approval_id"),
            "resolved": not parked,
            # The wire carries no explicit decision event; a resolved gate is 'approved'
            # iff an action executed AFTER it (reject routes straight to generate).
            "approved": None if parked else executed_after_gate,
        }
    else:
        gate = {"gated": False, "risk": None, "resolved": True, "approved": None}

    answer = "".join(_get(e, "text", "") for e in events if _etype(e) == "token")

    duration_ms = sum((n.get("duration_ms") or 0) for n in nodes)
    outcome = {
        "status": status,
        "prompt_tokens": _get(outcome_ev, "prompt_tokens", 0) if outcome_ev else 0,
        "completion_tokens": _get(outcome_ev, "completion_tokens", 0) if outcome_ev else 0,
        "cost_usd": _get(outcome_ev, "cost_usd", 0.0) if outcome_ev else 0.0,
        "cache_hit": _get(outcome_ev, "cache_hit", False) if outcome_ev else False,
    }

    return {
        "run_id": run_id,
        "trace_id": trace_id,
        "status": status,
        "agents": agents,
        "team": _team_totals(agents, nodes),
        "nodes": nodes,
        "reasoning": reasoning,
        "guardrails": guardrails,
        "routing": routing,
        "gate": gate,
        "tools": tools,
        "iterations": iterations,
        "memory": memory,
        "answer": answer,
        "totals": {
            "prompt_tokens": outcome["prompt_tokens"],
            "completion_tokens": outcome["completion_tokens"],
            "cost_usd": outcome["cost_usd"],
            "duration_ms": duration_ms,
            "cache_hit": outcome["cache_hit"],
        },
        "outcome": outcome,
        "event_count": len(events),
    }
