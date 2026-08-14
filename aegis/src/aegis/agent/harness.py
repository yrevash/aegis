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
  and their results, the bounded self-repair iterations, the ML evidence, the final
  answer and the terminal outcome. Because it consumes the emitted events verbatim, the
  "how it worked" record can never diverge from what streamed to the client.

Neither accessor imports a host schema; both operate on plain dicts (and, defensively,
on any attribute-bearing wire event) so they are reusable from the pure package and from
a host that stamps its own event models.
"""

from __future__ import annotations

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
        "gating signal (risk-driven, never ML).",
        allowed=tuple(r.value for r in RiskLevel),
    ),
    _KnobSpec(
        "run_ml",
        "bool",
        "Run the best-effort ML solution signal before planning. Injected as supporting "
        "evidence only — it never gates, defers or terminates a run.",
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
        its result); ``iterations`` (each bounded self-repair reflection); ``ml`` evidence;
        ``memory``; the reassembled ``answer``; ``totals`` (usage + summed node duration);
        and ``outcome`` (the terminal ``run_finished`` usage/status).
    """
    events = list(events)

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

    ml_ev = next((e for e in events if _etype(e) == "ml_explanation"), None)
    ml = (
        {
            "prediction": _get(ml_ev, "prediction"),
            "conformal_interval": _get(ml_ev, "conformal_interval"),
            "conformal_confidence": _get(ml_ev, "conformal_confidence"),
            "shap_attribution": _get(ml_ev, "shap_attribution", []),
        }
        if ml_ev is not None
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
        "nodes": nodes,
        "reasoning": reasoning,
        "guardrails": guardrails,
        "routing": routing,
        "gate": gate,
        "tools": tools,
        "iterations": iterations,
        "ml": ml,
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
