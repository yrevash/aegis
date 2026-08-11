"""Dependency injection for the agent graph — the seam that makes it testable.

Every cross-module capability the graph needs (the LLM gateway, retrieval, the
guardrails, the ML spine, the action tools, the audit sink, the tenant scope) is
reached through a callable on :class:`AgentDeps`. This module holds only the
*contract* — the dataclasses + type aliases + the risk-ordering helpers — so it
imports nothing heavy and nothing host-specific: a host application wires the real
module functions in its composition root (``AgentDeps.default()`` lives host-side,
mirroring ``gateway.configure(...)``), while tests inject fakes and drive the entire
vertical slice with no live infrastructure, no API keys, and no network.

:class:`AgentConfig` holds the human-gate threshold: the minimum tool risk that
forces a human approval gate (the money-shot). ML is a supporting solution signal,
never a flow decider.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from aegis.core.types import RiskLevel
from aegis.ml.types import MLExplainResponse

from .router import load_roster

__all__ = [
    "AgentConfig",
    "AgentDeps",
    "MemoryDeps",
    "ToolOutcome",
    "risk_at_least",
    "risk_rank",
]


# Structural aliases for the injected callables (kept loose to avoid coupling).
CompleteFn = Callable[..., Awaitable[Any]]
RetrieveFn = Callable[..., Awaitable[Any]]
GuardFn = Callable[[str], Awaitable[Any]]
PredictFn = Callable[[dict[str, Any]], MLExplainResponse]
ToolDefsFn = Callable[[str], list[dict[str, Any]]]
RiskFn = Callable[[str], RiskLevel]
RenderPromptFn = Callable[..., str]
FeaturesFn = Callable[[str, str | None], dict[str, Any]]
DescribeFn = Callable[[MLExplainResponse], str]
RosterFn = Callable[[], Any]
TenantFn = Callable[[], int | None]
AuditFn = Callable[..., Awaitable[Any]]

# Relative order used to compare risk levels.
_RISK_RANK: dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
}


def risk_rank(risk: RiskLevel) -> int:
    """Return the ordinal severity of ``risk`` (LOW=0, MEDIUM=1, HIGH=2)."""
    return _RISK_RANK[risk]


def risk_at_least(risk: RiskLevel, floor: RiskLevel) -> bool:
    """Return whether ``risk`` is at least as severe as ``floor``."""
    return _RISK_RANK[risk] >= _RISK_RANK[floor]


class ToolOutcome(Protocol):
    """Structural view of an action-tool result (see adapter ``ToolActionResult``)."""

    ok: bool
    summary: str


RunToolFn = Callable[..., Awaitable[ToolOutcome]]


def _no_tenant() -> int | None:
    """Default tenant provider — ungoverned (single-tenant / offline) runs."""
    return None


@dataclass
class AgentConfig:
    """Human-gate threshold and streaming knobs.

    **ML never gates.** By founder decision the human-in-the-loop gate is driven by
    **tool risk only** (``gate_min_risk``): a proposed action at or above that tier
    routes to the human approval inbox — the money-shot. ML is a *solution signal*,
    not a flow decider; a low-confidence or failed prediction never defers, abstains,
    or terminates a run.

    Attributes:
        gate_min_risk: The minimum tool risk that forces the human gate. This is the
            **only** gating signal (risk-driven, never ML).
        run_ml: Whether to run the best-effort ML solution step when a subject is
            resolved. The prediction is injected as supporting evidence, never gates.
        stream_chunk_words: How many words per streamed answer ``token`` event.
        approval_park_timeout: Seconds the live ``/query`` socket holds a gate open
            before *parking* the run (durable row remains the source of truth).
            ``None`` (default) waits indefinitely — the live money-shot gate.
        max_plan_iterations: The iteration budget for the bounded self-repair loop —
            the maximum number of planning rounds a single run may take (a HARD cap
            that guarantees termination). ``1`` disables looping (single linear pass);
            the default ``2`` allows one re-plan after a failed/insufficient action.
        default_persona_id: The persona id a run falls back to when the request names
            none. Neutral by default; the host wires its adapter's default persona.
    """

    gate_min_risk: RiskLevel = RiskLevel.HIGH
    run_ml: bool = True
    stream_chunk_words: int = 4
    max_plan_iterations: int = 2
    approval_park_timeout: float | None = None
    default_persona_id: str = "default"
    #: Retrieval intelligence (docs/EVAL_STRATEGY.md). ``query_rewrite_enabled`` runs a
    #: cheap-model, context-aware rewrite before retrieval; ``agentic_retrieval_enabled``
    #: runs the bounded Self-RAG/FLARE loop (retrieve → judge sufficiency → reformulate →
    #: re-retrieve, capped by ``agentic_retrieval_max_rounds``); ``answer_cache_enabled``
    #: reuses a semantically-equivalent prior answer (scoped per tenant+persona+role),
    #: skipping the generation call. All ON in production; test fakes pin them off for
    #: deterministic single-shot behaviour.
    query_rewrite_enabled: bool = True
    agentic_retrieval_enabled: bool = True
    agentic_retrieval_max_rounds: int = 2
    answer_cache_enabled: bool = True


class MemoryDeps(Protocol):
    """Long-term-memory capability consumed by the memory nodes (a structural seam).

    The concrete, DB-backed implementation lives host-side (it opens tenant-scoped
    sessions and writes the memory stores); :mod:`aegis.agent` only depends on this
    read/write shape. ``AgentDeps.memory = None`` (the test-fake default) makes the
    ``recall_memory``/``persist_memory`` nodes silent no-ops — today's exact stream.
    """

    async def assemble(
        self,
        *,
        subject_id: str,
        session_id: str,
        persona: str | None,
        query: str,
        query_vec: list[float] | None,
    ) -> Any:  # noqa: ANN401 - structural AssembledMemory (host type)
        """Recall + assemble the working-memory block for one turn (READ path)."""
        ...

    async def persist(
        self,
        *,
        subject_id: str,
        session_id: str,
        turn_index: int,
        user_text: str,
        assistant_text: str,
        query_vec: list[float] | None,
        run_id: str | None,
        trace_id: str | None,
    ) -> None:
        """Persist the user + assistant turns and cadence-fire consolidation (WRITE)."""
        ...


class AnswerCache(Protocol):
    """Answer-level semantic cache seam (skip generation on an equivalent prior answer)."""

    async def get(self, embedding: list[float], *, scope: str) -> Any:  # noqa: ANN401 - host AnswerHit
        """Return a cache hit for ``embedding`` within ``scope`` (or ``None``)."""
        ...

    async def set(
        self,
        *,
        query: str,
        embedding: list[float],
        answer: str,
        scope: str,
        sources: Any,  # noqa: ANN401 - host source-list type
    ) -> Any:  # noqa: ANN401 - host cache-entry type
        """Store ``answer`` under ``scope`` keyed by ``embedding``."""
        ...


@dataclass
class AgentDeps:
    """The concrete capabilities the graph calls, injectable for testing."""

    complete: CompleteFn
    retrieve: RetrieveFn
    check_input: GuardFn
    check_output: GuardFn
    predict_explain: PredictFn
    tool_definitions_for: ToolDefsFn
    run_tool: RunToolFn
    tool_risk: RiskFn
    render_system_prompt: RenderPromptFn
    features_for: FeaturesFn
    describe_prediction: DescribeFn
    #: Supervisor roster provider — returns the host adapter's routable specialists.
    #: Defaults to the core ``qa``-only fallback roster, so test fakes that omit it
    #: still route (to ``qa``); the host wires the real adapter roster here.
    agent_roster: RosterFn = field(default=load_roster)
    config: AgentConfig = field(default_factory=AgentConfig)
    #: Long-term memory capability. ``None`` (the test-fake default) makes the
    #: ``recall_memory``/``persist_memory`` nodes silent no-ops — today's exact stream.
    memory: MemoryDeps | None = None
    #: Answer-level semantic cache (skip the generation call on a semantically-
    #: equivalent prior answer). ``None`` (the test-fake default, or when stores/answer
    #: cache are disabled) makes the qa cache lookup/store a silent no-op.
    answer_cache: AnswerCache | None = None
    #: Tenant-scope provider for the answer-cache partition key + route audit. Defaults
    #: to ungoverned (``None``); the host wires its governance context here.
    current_tenant_id: TenantFn = field(default=_no_tenant)
    #: Best-effort audit sink for the supervisor hand-off row. ``None`` (the default)
    #: makes the route audit a silent no-op; the host wires ``record_audit`` here,
    #: already gated on whether a durable store is configured.
    record_audit: AuditFn | None = None
