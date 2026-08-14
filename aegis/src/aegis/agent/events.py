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
from aegis.ml.types import MLExplainResponse


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


def ml_explanation(resp: MLExplainResponse) -> dict[str, Any]:
    """Build an ``ml_explanation`` payload from a spine response.

    Purely informational supporting evidence (prediction + interval + SHAP drivers)
    for the answer to cite — it carries no gating semantics (ML never gates).

    ``data_source``, ``imputed_features`` and ``unknown_features`` are forwarded
    deliberately. A prediction from a model fitted on synthetic data, or one where
    the caller's features were silently replaced by training medians, is not the
    same claim as a prediction from a real model on real inputs — and this payload
    is what the answer cites as evidence. Dropping those fields here would leave
    the honesty signal stranded on the response object, visible to nobody.
    """
    return {
        "type": "ml_explanation",
        "prediction": resp.prediction,
        "conformal_interval": resp.conformal_interval,
        "conformal_confidence": resp.conformal_confidence,
        "shap_attribution": [f.model_dump() for f in resp.shap_attribution],
        "data_source": getattr(resp, "data_source", None),
        "imputed_features": list(getattr(resp, "imputed_features", ()) or ()),
        "unknown_features": list(getattr(resp, "unknown_features", ()) or ()),
    }


def approval_required(
    approval_id: str,
    *,
    action: str,
    args: dict[str, Any],
    risk: RiskLevel,
    rationale: str,
) -> dict[str, Any]:
    """Build an ``approval_required`` payload for the human-in-the-loop gate."""
    return {
        "type": "approval_required",
        "approval_id": approval_id,
        "action": action,
        "args": args,
        "risk": risk.value,
        "rationale": rationale,
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


def routing(*, role: str, reason: str, used_llm: bool = False) -> dict[str, Any]:
    """Build a ``routing`` payload — the supervisor's visible specialist hand-off.

    Emitted once by the ``route`` node right after the input rail: it names the
    specialist role the turn was dispatched to (``qa`` → the full pipeline, ``memory``
    → the memory specialist), why, and whether the cheap-LLM tiebreak was consulted.
    Purely additive — a client that does not know this variant simply ignores it.
    """
    return {
        "type": "routing",
        "role": role,
        "reason": reason,
        "used_llm": used_llm,
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
