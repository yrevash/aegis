"""Builders for the streamed agent event payloads (plain dict factories).

Graph nodes never construct a wire model directly. Instead they emit *partial*
event dicts through the LangGraph stream writer, and the orchestrator stamps each
one with the run-scoped ``run_id`` and a monotonic ``seq`` and validates it against
the host's locked event union — via an **injected** ``stamp`` callable, so this
module (and all of :mod:`aegis.agent`) never imports the host's API schema layer.

Centralising the wire shape here keeps the node code free of bookkeeping. Every
builder returns a plain ``dict`` whose ``type`` selects the union variant; the
injected stamp finishes the job.
"""

from __future__ import annotations

from typing import Any

from aegis.core.types import GuardStage, GuardVerdict, RiskLevel, RunStatus


def node_started(node: str, label: str) -> dict[str, Any]:
    """Build a ``node_started`` payload for a visible plan step."""
    return {"type": "node_started", "node": node, "label": label}


def node_finished(
    node: str,
    label: str,
    duration_ms: int,
    *,
    model: str | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cost_usd: float = 0.0,
) -> dict[str, Any]:
    """Build a ``node_finished`` payload carrying the node's timing and usage."""
    return {
        "type": "node_finished",
        "node": node,
        "label": label,
        "duration_ms": duration_ms,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost_usd": cost_usd,
    }


def reasoning(text: str) -> dict[str, Any]:
    """Build a ``reasoning`` payload carrying a chunk of the planner's plan."""
    return {"type": "reasoning", "text": text}


def guardrail(
    stage: GuardStage,
    verdict: GuardVerdict,
    reason: str,
    *,
    layer: str | None = None,
    redactions: list[str] | None = None,
    before_masked: str | None = None,
    after: str | None = None,
) -> dict[str, Any]:
    """Build a ``guardrail`` payload carrying a rail verdict and redaction detail.

    ``redactions`` is a list of detector *kinds* (never raw values); the masked
    ``before_masked``/``after`` text carries only redacted placeholders.
    """
    return {
        "type": "guardrail",
        "stage": stage.value,
        "verdict": verdict.value,
        "reason": reason,
        "layer": layer,
        "redactions": [{"kind": kind} for kind in (redactions or [])],
        "before_masked": before_masked,
        "after": after,
    }


def retrieval(
    status: str,
    *,
    num_candidates: int = 0,
    scored_sources: list[dict[str, Any]] | None = None,
    nodes: list[dict[str, Any]] | None = None,
    edges: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a ``retrieval`` payload, optionally carrying a graph delta."""
    return {
        "type": "retrieval",
        "status": status,
        "num_candidates": num_candidates,
        "scored_sources": scored_sources or [],
        "touched_nodes": nodes or [],
        "touched_edges": edges or [],
    }


def tool_call(
    call_id: str, tool: str, args: dict[str, Any], risk: RiskLevel
) -> dict[str, Any]:
    """Build a ``tool_call`` payload for a proposed action."""
    return {
        "type": "tool_call",
        "call_id": call_id,
        "tool": tool,
        "args": args,
        "risk": risk.value,
    }


def tool_result(call_id: str, ok: bool, summary: str) -> dict[str, Any]:
    """Build a ``tool_result`` payload for a completed (or failed) action."""
    return {
        "type": "tool_result",
        "call_id": call_id,
        "ok": ok,
        "summary": summary,
    }


def approval_required(
    approval_id: str,
    *,
    action: str,
    args: dict[str, Any],
    risk: RiskLevel,
    rationale: str,
    actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build an ``approval_required`` payload for the human-in-the-loop gate.

    ``action``/``args``/``risk`` describe the **representative** (highest-risk) call and
    are unchanged. ``actions`` is every call this one gate authorises: a fan-out
    aggregates the proposals of all its lanes into a single gate, so a payload that
    named only the representative described one write while three would run. The
    ``rationale`` spells the same list out in prose, because that is the field a
    dialog and the durable inbox row already render.
    """
    return {
        "type": "approval_required",
        "approval_id": approval_id,
        "action": action,
        "args": args,
        "risk": risk.value,
        "rationale": rationale,
        "actions": list(actions or []),
    }


def provenance(prov: Any, *, cache_hit: bool) -> dict[str, Any]:  # noqa: ANN401 - Provenance duck-type
    """Build a ``provenance`` payload from a retrieval ``Provenance`` (§4.3).

    Reads the P0-frozen shape on the retrieval ``Provenance`` (``origins``,
    ``fusion``, optional ``cache`` lineage) and flattens it onto the wire event. An
    unpopulated provenance yields an honest empty default.

    Args:
        prov: The ``Provenance`` object off a ``RetrievalResult``.
        cache_hit: Whether the result was served from the semantic cache.
    """
    origins = [
        o.value if hasattr(o, "value") else str(o)
        for o in getattr(prov, "origins", []) or []
    ]
    fusion = getattr(prov, "fusion", None)
    cache = getattr(prov, "cache", None)
    return {
        "type": "provenance",
        "origins": origins,
        "fusion": fusion.value if hasattr(fusion, "value") else (fusion or "none"),
        "cache_hit": bool(cache_hit),
        "cache_kind": getattr(cache, "kind", None),
        "original_query": getattr(cache, "original_query", None),
        "cached_at": getattr(cache, "cached_at", None),
    }


def approval_queued(
    approval_id: str,
    *,
    action: str,
    args: dict[str, Any],
    risk: RiskLevel,
    rationale: str,
    sla_deadline: str | None = None,
    assignee_tier: str | None = None,
) -> dict[str, Any]:
    """Build an ``approval_queued`` payload (durable inbox row persisted; §1.3)."""
    return {
        "type": "approval_queued",
        "approval_id": approval_id,
        "action": action,
        "args": args,
        "risk": risk.value,
        "rationale": rationale,
        "sla_deadline": sla_deadline,
        "assignee_tier": assignee_tier,
    }


def verification(
    *,
    outcome: str,
    method: str,
    reason: str,
    repairable: bool,
    evidence: str = "",
    round: int = 0,  # noqa: A002 - the wire name; this is the planning round
) -> dict[str, Any]:
    """Build a ``verification`` payload for one grounded check of a round's outcome.

    Emitted by the ``verify`` node between ``act`` and ``reflect``. It reports what was
    checked and *how* — which is the point of the event. ``method`` is the tier that
    reached the verdict: ``deterministic`` (the rows decided it), ``read-back`` (a
    read-only call proved whether the write landed) or ``unverifiable`` (nothing in this
    deployment could confirm it, reported as such rather than assumed).

    ``outcome`` is one of ``VERIFIED``, ``FAILED``, ``BLOCKED``, ``OSCILLATING``,
    ``GATHERED`` or ``UNVERIFIED``. ``repairable`` says whether another round could
    plausibly help: a rail refusal and a repeated call are both failures that retrying
    cannot fix, and saying so on the wire is what stops the console implying otherwise.
    """
    return {
        "type": "verification",
        "outcome": outcome,
        "method": method,
        "reason": reason,
        "repairable": repairable,
        "evidence": evidence,
        "round": round,
    }


def reflection(
    *,
    iteration: int,
    max_iterations: int,
    done: bool,
    will_retry: bool,
    reason: str,
) -> dict[str, Any]:
    """Build a ``reflection`` payload for one bounded self-repair decision.

    Emitted by the ``reflect`` node after an action executes: it reports whether the
    goal was judged met, whether the agent will loop back to ``plan`` for another
    round, and which iteration (of the hard cap) this reflection follows.
    """
    return {
        "type": "reflection",
        "iteration": iteration,
        "max_iterations": max_iterations,
        "done": done,
        "will_retry": will_retry,
        "reason": reason,
    }


def routing(
    *,
    role: str,
    reason: str,
    used_llm: bool = False,
    depth: str = "single",
    fanout: int = 0,
    decided_by: str = "auto",
) -> dict[str, Any]:
    """Build a ``routing`` payload — the supervisor's visible specialist hand-off.

    Emitted once by the ``route`` node right after the input rail: it names the
    specialist role the turn was dispatched to (``qa`` → the full pipeline, ``memory``
    → the memory specialist), why, and whether the cheap-LLM tiebreak was consulted.
    Purely additive — a client that does not know this variant simply ignores it.

    It also carries the turn's **width**: ``depth`` (single | team), ``fanout`` (how
    many sub-agents) and ``decided_by`` (auto | user | tenant_default | platform_cap),
    so the trace reads *"TEAM ×3 — you selected Team mode"* or *"SINGLE — single-intent
    query, answering in one pass"*. **Never a width with no explanation.**
    """
    return {
        "type": "routing",
        "role": role,
        "reason": reason,
        "used_llm": used_llm,
        "depth": depth,
        "fanout": fanout,
        "decided_by": decided_by,
    }


def agent_status(
    *,
    agent_id: str,
    role: str,
    label: str,
    status: str,
    detail: str = "",
) -> dict[str, Any]:
    """Build an ``agent_status`` payload — one sub-agent's lifecycle beat.

    Emitted by :func:`aegis.agent.subagent.run_subagent` through its own scoped writer,
    so a fan-out produces interleaved beats from every concurrent lane. ``status`` is
    one of ``started`` | ``thinking`` | ``acting`` | ``done`` | ``failed`` | ``timeout``
    — and ``timeout`` is a **designed** terminal state, not an error.
    """
    return {
        "type": "agent_status",
        "agent_id": agent_id,
        "role": role,
        "label": label,
        "status": status,
        "detail": detail,
    }


def synthesis(
    *,
    contributing: list[dict[str, Any]],
    omitted: list[dict[str, Any]],
    summary: str,
) -> dict[str, Any]:
    """Build a ``synthesis`` payload naming who contributed **and who did not**.

    Partial failure otherwise reads as a bug: one agent times out, its card sits
    spinning, and the audience concludes the thing is broken. Naming the omitted agent
    and its terminal state turns that into visible, graceful degradation — which is
    only true if it is designed, so the omission is a first-class field here rather
    than an absence the client has to infer.
    """
    return {
        "type": "synthesis",
        "contributing": contributing,
        "omitted": omitted,
        "summary": summary,
    }


def memory(
    *,
    recalled_fact_count: int,
    recalled_message_count: int,
    tokens_used: int,
) -> dict[str, Any]:
    """Build a ``memory`` payload summarising one turn's long-term-memory recall.

    Emitted by the ``recall_memory`` node only when memory is active; a silent
    pass-through (no event) on the single-shot path keeps the trace back-compatible.
    """
    return {
        "type": "memory",
        "recalled_fact_count": recalled_fact_count,
        "recalled_message_count": recalled_message_count,
        "tokens_used": tokens_used,
    }


def token(text: str) -> dict[str, Any]:
    """Build a ``token`` payload carrying a chunk of the final answer."""
    return {"type": "token", "text": text}


def run_started(trace_id: str) -> dict[str, Any]:
    """Build a ``run_started`` payload carrying the trace id."""
    return {"type": "run_started", "trace_id": trace_id}


def run_finished(
    status: RunStatus,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cost_usd: float = 0.0,
    cache_hit: bool = False,
) -> dict[str, Any]:
    """Build the terminal ``run_finished`` payload with usage/cost."""
    return {
        "type": "run_finished",
        "status": status.value,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost_usd": cost_usd,
        "cache_hit": cache_hit,
    }


def error(message: str) -> dict[str, Any]:
    """Build an ``error`` payload."""
    return {"type": "error", "message": message}


def budget_exceeded(
    *,
    scope: str,
    scope_id: int | None,
    limit_type: str,
    limit: float | None,
    used: float | None,
    message: str,
) -> dict[str, Any]:
    """Build a ``budget_exceeded`` payload for a tripped tenant/user cap (§3.3).

    Terminal event surfaced when the LiteLLM chokepoint refused a call before spend
    because a per-tenant/user token/usd/rpm/tpm cap was hit.

    Args:
        scope: Which level tripped — ``"tenant"`` or ``"user"``.
        scope_id: Id of the tripped scope.
        limit_type: Which cap tripped — ``"token_cap"`` | ``"usd_cap"`` | ``"rpm"`` |
            ``"tpm"``.
        limit: The configured cap value.
        used: Consumption at refusal time.
        message: Human-readable explanation for the UI/audit.
    """
    return {
        "type": "budget_exceeded",
        "scope": scope,
        "scope_id": scope_id,
        "limit_type": limit_type,
        "limit": limit,
        "used": used,
        "message": message,
    }
