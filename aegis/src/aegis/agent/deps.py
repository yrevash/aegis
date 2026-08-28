"""Dependency injection for the agent graph — the seam that makes it testable.

Every cross-module capability the graph needs (the LLM gateway, retrieval, the
guardrails, the action tools, the audit sink, the tenant scope) is
reached through a callable on :class:`AgentDeps`. This module holds only the
*contract* — the dataclasses + type aliases + the risk-ordering helpers — so it
imports nothing heavy and nothing host-specific: a host application wires the real
module functions in its composition root (``AgentDeps.default()`` lives host-side,
mirroring ``gateway.configure(...)``), while tests inject fakes and drive the entire
vertical slice with no live infrastructure, no API keys, and no network.

:class:`AgentConfig` holds the human-gate threshold: the minimum tool risk that
forces a human approval gate (the money-shot). That threshold, compared against the
tool's own declared risk, is the whole gating rule — there is no second signal.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from aegis.adapter import AgentRosterLike
from aegis.core.models import ModelRole
from aegis.core.types import GuardResult, RiskLevel
from aegis.gateway.types import LLMResult
from aegis.retrieval.types import RetrievalScope

from .router import load_roster

if TYPE_CHECKING:  # pragma: no cover - typing only; .subagent imports this module
    from .subagent import SubAgentSpec

__all__ = [
    "ActivePromptFn",
    "AgentConfig",
    "AgentDeps",
    "AuditFn",
    "CompleteFn",
    "EmbedQueryFn",
    "InputGuardFn",
    "MemoryDeps",
    "OutputGuardFn",
    "RenderPromptFn",
    "RetrieveFn",
    "ReadBack",
    "ReadBackFn",
    "ReadOnlyFn",
    "RiskFn",
    "RosterFn",
    "RunToolFn",
    "SubAgentRosterFn",
    "TenantFn",
    "ToolDefsFn",
    "ToolOutcome",
    "risk_at_least",
    "risk_rank",
]


# ─────────────────────────────────────────────────────────────────────────────
# The injected capabilities, as Protocols with named parameters and real returns.
#
# These were ``Callable[..., Awaitable[Any]]`` — a type that accepts anything and
# therefore tells a caller nothing. Signature misinterpretation is one of the two
# dominant error classes when an AI integrates an unfamiliar library, and ``...`` is
# that error class written down: it cannot say that ``check_output`` takes the retrieved
# contexts, that ``run_tool``'s four audit fields are keyword-only and have **no
# defaults**, or that ``complete`` hands back a result with ``tool_calls`` and ``usage``
# on it. Every parameter below is named, and every return type is the real one.
#
# The leading ``/`` on the positional parameters of ``RunToolFn`` / ``RenderPromptFn``
# is deliberate: it makes them positional-only in the *contract*, so an implementation
# may call its first argument ``persona`` where the caller calls it ``persona_id``
# without a type checker objecting to a difference that cannot matter — those call sites
# all pass positionally. ``CompleteFn`` deliberately does NOT do this, because it must
# stay interchangeable with :class:`aegis.retrieval.protocols.CompleteFn`, which names
# ``role``/``messages``: the graph passes ``deps.complete`` straight into the retrieval
# pipeline, so the two spellings of one seam have to be assignable to each other.
# ─────────────────────────────────────────────────────────────────────────────


class CompleteFn(Protocol):
    """Structural type of the injected chat-completion callable (the LLM gateway).

    Bound host-side to :func:`aegis.gateway.complete`. The return type is the real
    :class:`~aegis.gateway.types.LLMResult` rather than ``Any`` because the graph reads
    four fields off it — ``content``, ``tool_calls``, ``usage`` and ``model`` — and a
    fake that returns a bare string type-checks fine against ``Any`` and then fails at
    the first ``.tool_calls``.

    All three keywords are required of an implementation, and narrowing this type is
    what proved it: the graph hands ``deps.complete`` straight to the retrieval
    pipeline's own :class:`aegis.retrieval.protocols.CompleteFn` (query rewrite,
    agentic retrieval), which calls it with ``response_format=`` and ``temperature=``.
    A binding that accepted only ``tools`` therefore type-checked against the old
    ``Callable[..., Awaitable[Any]]`` and raised ``TypeError`` the first time a query
    rewrite ran. A host may of course accept *more* (the gateway's own ``complete``
    also takes ``max_tokens``); accepting extra optional keywords never breaks a caller.
    """

    async def __call__(
        self,
        role: ModelRole,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResult:
        """Complete a chat request for ``role`` and return the normalised result."""
        ...


class RetrieveFn(Protocol):
    """Structural type of the injected retrieval callable.

    Spelled out as a Protocol rather than a loose ``Callable[..., Awaitable[Any]]``
    because this signature is a **security boundary**, and a loose alias is what let the
    graph call retrieval without a tenant while holding one two lines above. ``scope``
    is keyword-only and has no default, so every implementation and every call site has
    to name it.
    """

    async def __call__(
        self, query: str, *, scope: RetrievalScope
    ) -> Any:  # noqa: ANN401 - structural RetrievalResult (host/package type)
        """Retrieve for ``query`` within ``scope`` and return a retrieval result."""
        ...


class InputGuardFn(Protocol):
    """Structural type of an *inbound* rail: screen one piece of text, return a verdict.

    Bound to ``check_input`` and — when a host wires the dedicated rail — to
    ``check_tool_result``. One argument, because that is genuinely all the inbound chain
    takes; the tool-result rail falls back to this one precisely because the two have
    the same shape (see :func:`aegis.agent.rails.screen_tool_result`).
    """

    async def __call__(self, text: str, /) -> GuardResult:
        """Screen ``text`` and return the rail's verdict (PASS/FLAG/REDACT/BLOCK)."""
        ...


class OutputGuardFn(Protocol):
    """Structural type of the *outbound* rail, which additionally takes the contexts.

    ``contexts`` is the retrieved passages the answer was generated from, and it is what
    makes the grounding self-check possible: judged without them, an answer can only be
    checked for form, not for support. The old ``Callable[..., Awaitable[Any]]`` could
    not say this parameter existed, so a host was free to bind a one-argument
    ``check_output(text)`` — which type-checked, and then raised ``TypeError`` on the
    one call site in the graph that passes ``contexts=``.
    """

    async def __call__(
        self, text: str, /, contexts: list[str] | None = None
    ) -> GuardResult:
        """Screen ``text``, grounding it against ``contexts`` when they are supplied."""
        ...


ToolDefsFn = Callable[[str], list[dict[str, Any]]]
RiskFn = Callable[[str], RiskLevel]

#: Whether a tool only reads. Defaults to "no tool is read-only", which preserves the
#: exact behaviour every existing caller and test fake had before this seam existed.
ReadOnlyFn = Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class ReadBack:
    """The read-only call that proves whether a write actually landed.

    This is what makes the repair loop a *verifier* rather than a self-critique. A model
    asked "did that go well?" about its own work is the failure mode the 2026 literature
    is clearest on — ungrounded self-correction does not reliably help and often degrades.
    Reading the record back is grounded in something outside the model, and it is also the
    difference between a demo that is real and one that is a scripted animation.

    Attributes:
        tool: The read-only tool to call. Must be read-only AND below the gate threshold,
            so verification can never itself become an action or raise an approval.
        args: Arguments for that call.
        expect: A substring the read-back summary must contain for the write to count as
            landed. Deliberately a substring rather than a predicate — it has to be
            serialisable into the verification event a reader will see.
        describe: One human sentence naming what was checked, for that event.
    """

    tool: str
    args: dict[str, Any]
    expect: str
    describe: str


#: Given a write that just executed, the read-only call that proves whether it landed.
#: ``None`` — the default, and what every existing test fake will return — means tier 2
#: is INCONCLUSIVE and the verifier falls through to its judge. It never means the write
#: is assumed to have worked; an unverifiable write is reported as unverified.
ReadBackFn = Callable[[str, Mapping[str, Any]], "ReadBack | None"]


class RenderPromptFn(Protocol):
    """Structural type of the persona system-prompt renderer.

    Takes a persona **id**, not a persona object: the core has no domain persona type,
    and the host resolves the id through its own adapter (which is also where the
    LLM-Ops prompt version and the platform floor get composed in).
    """

    def __call__(self, persona_id: str, /, extra_context: str | None = None) -> str:
        """Render the system prompt for ``persona_id``, appending ``extra_context``."""
        ...


#: Provider of the adapter's SUPERVISOR roster (the routable specialists). The return is
#: :class:`~aegis.adapter.AgentRosterLike` — ``default_role`` / ``roles()`` / ``named()``
#: / ``specialists`` — rather than ``Any``, so a roster missing the fall-through role is
#: a type error and not a run that routes nowhere.
RosterFn = Callable[[], AgentRosterLike]
#: Provider of the adapter's SUB-agent roster (the fan-out team) — a sequence of
#: :class:`~aegis.agent.subagent.SubAgentSpec` entries; ``None``/absent ⇒ no team and
#: every turn is SINGLE. Quoted because ``subagent`` imports this module, not the other
#: way round: the contract is here, the mechanism is there.
SubAgentRosterFn = Callable[[], "Sequence[SubAgentSpec]"]
#: Synchronous read of the LLM-Ops registry's ACTIVE prompt version for a ``prompt_key``
#: — ``(system_prompt, config, version)`` or ``None`` when nothing is active. A host
#: binds :func:`aegis.ops.registry.get_cached_active` here; the seam exists because
#: ``aegis.ops`` pulls SQLAlchemy and ``aegis.agent`` must stay import-light.
ActivePromptFn = Callable[[str], "tuple[str, dict[str, Any], int] | None"]

#: Tier-1 skill cards resolved **for one named agent**: ``(agent_id) -> [card, …]``.
#: Bound host-side to ``aegis.skills.store.resolve_skills`` under the caller's own
#: tenant/user scope. It exists because the run's working-memory block is assembled once
#: and shared by every lane, so it can only ever carry the *main* lane's answer to
#: "which skills are in force" — and a skill assigned to the research lane would then be
#: offered to all four lanes and to the main persona besides, which is the opposite of
#: assigning it. ``None`` ⇒ a lane inherits the shared block unchanged, which is exactly
#: today's behaviour and stays correct while nothing is assigned to anybody.
SkillCardsFn = Callable[[str], "Awaitable[list[str]]"]
TenantFn = Callable[[], int | None]


class AuditFn(Protocol):
    """Structural type of the durable audit sink (the supervisor hand-off row).

    Every parameter is keyword-only and every one of the first five is **required**,
    which is the contract ``aegis.governance.audit.record_audit`` already has: an audit
    row with no ``trace_id`` cannot be correlated to the spans it describes, and one
    with no ``payload`` records that something happened without recording what.
    """

    async def __call__(
        self,
        *,
        action: str,
        actor: str | None,
        model: str | None,
        trace_id: str | None,
        payload: dict[str, Any],
        approved_by: str | None = None,
        tenant_id: int | None = None,
    ) -> None:
        """Persist one audit record for ``action``."""
        ...
#: Embed one query string for memory recall; returns ``None`` when unavailable.
EmbedQueryFn = Callable[[str], Awaitable[list[float] | None]]

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


class RunToolFn(Protocol):
    """Structural type of the action-tool executor — the one seam that changes the world.

    The four keyword arguments have **no defaults** on purpose. They are the audit trail
    of a consequential action (who asked, which model proposed it, which trace it
    belongs to, and which human approved it at the gate), and a default would let a
    caller drop the approver silently — which is the difference between an approved
    action and an unattributed one. ``Callable[..., Awaitable[ToolOutcome]]`` could not
    express that any of them existed.
    """

    async def __call__(
        self,
        persona_id: str,
        tool_name: str,
        args: dict[str, Any],
        /,
        *,
        actor: str | None,
        model: str | None,
        trace_id: str | None,
        approver: str | None,
    ) -> ToolOutcome:
        """Execute ``tool_name`` for ``persona_id`` and return its outcome."""
        ...


def _no_tenant() -> int | None:
    """Default tenant provider — ungoverned (single-tenant / offline) runs."""
    return None


@dataclass
class AgentConfig:
    """Human-gate threshold and streaming knobs.

    The human-in-the-loop gate is driven by **tool risk** (``gate_min_risk``): a
    proposed action at or above that tier routes to the human approval inbox — the
    money-shot. Nothing else can force or skip the gate, which is what makes the
    guarantee explainable on stage: read the tool's declared risk, read this floor.

    **The values here are the floor, not the last word.** A host that governs tenants
    resolves this config *per run* through
    :func:`aegis.settings.agent.resolve_agent_config`, which folds the tenant's
    ``TIGHTEN_ONLY`` settings on by taking whichever value is stricter — so a tenant may
    ask for more oversight (a lower ``gate_min_risk``) or fewer agents, and can never
    loosen what the host wired here. Nothing in this module reads a database; the seam
    is the host's, and the fold is per run precisely so one tenant's floor can never run
    another tenant's turn.

    Attributes:
        gate_min_risk: The minimum tool risk that forces the human gate. This is the
            **only** gating signal. The platform default is HIGH: what deserves a gate
            depends on the tenant, so the platform does not presume MEDIUM for everyone
            — and a tenant that tightens to MEDIUM gets MEDIUM (``agent.gate_min_risk``
            in the settings catalogue).
        stream_chunk_words: How many words per streamed answer ``token`` event.
        approval_park_timeout: Seconds the live ``/query`` socket holds a gate open
            before *parking* the run (durable row remains the source of truth).
            ``None`` (default) waits indefinitely — the live money-shot gate.
        max_plan_iterations: The iteration budget for the bounded self-repair loop —
            the maximum number of planning rounds a single run may take (a HARD cap
            that guarantees termination). ``1`` disables looping (single linear pass);
            the default ``2`` allows one re-plan after a failed/insufficient action.
        self_repair_enabled: Master switch for the bounded Reflexion self-repair loop.
            ``True`` (default) keeps today's behaviour — a failed/insufficient action
            re-plans while the ``max_plan_iterations`` budget remains. ``False`` forces a
            single linear pass: the ``reflect`` node still runs and reports the outcome,
            but never routes back to ``plan`` (a UI-friendly on/off that does not require
            reasoning about the iteration budget).
        default_persona_id: The persona id a run falls back to when the request names
            none. Neutral by default; the host wires its adapter's default persona.
        team_enabled: Master switch for the adaptive multi-agent fan-out. ``False``
            forces every turn SINGLE whatever the classifier or the user asked for.
        max_parallel_agents: The platform cap on team width (``agent.team.max_parallel``
            in the settings catalogue, ``TIGHTEN_ONLY``: a platform admin may lower it
            without a deploy and a tenant may lower it further for themselves, never
            raise it). A user's explicit width is **narrowed** by this and never widened
            past it — ``Custom`` mode is not a way around a budget cap, so the clamp
            lives where the cap is read. It is a ceiling and nothing else: it may not
            become a second reason to reduce a width the user explicitly chose.
        max_concurrent_agents: How many sub-agents may hold a gateway slot at once
            (the semaphore over the fan-out). Lower than ``max_parallel_agents`` on
            purpose: width is what the user asked for, concurrency is what the gateway
            can take.
        subagent_max_steps: The hard step cap on one sub-agent's ReAct loop — the
            guarantee it terminates.
        subagent_timeout_s: The per-sub-agent wall clock. Exceeding it is a **designed**
            terminal state (``timeout``), not an error: the agent is named as omitted
            and its siblings finish.
        team_wall_clock_s: The whole fan-out's wall clock, and a backstop **above** the
            per-lane bounds, never a tighter deadline competing with them. Whatever has
            not landed by then is omitted, and the synthesis says so.
    """

    gate_min_risk: RiskLevel = RiskLevel.HIGH
    stream_chunk_words: int = 4
    max_plan_iterations: int = 4
    #: Hard ceiling on the tokens one sub-agent lane's trajectory may reach before its
    #: next model call, estimated over the whole ``messages`` list.
    #:
    #: Aegis has no trajectory compaction: nothing summarises or evicts a run's own turn
    #: history. Its memory subsystem is excellent and governs *the store across turns* —
    #: it never sees what one run accumulates. Until compaction exists, the honest answer
    #: to "what happens on a very long run" is a stated bound rather than a shrug.
    #:
    #: **36000, chosen as roughly 3x the observed peak.** Measured 2026-08-27 on this
    #: deployment across two fan-out runs: peak 11,859 tokens per lane. That is TWO
    #: SAMPLES, which is thin, and the number should be revisited against a real
    #: workload rather than trusted as calibrated. Recording the sample size here is the
    #: point — a ceiling whose provenance is unwritten becomes folklore.
    max_trajectory_tokens: int = 36000
    #: Hard ceiling on ONE tool result's contribution to a lane's trajectory. A longer
    #: summary is truncated with an explicit marker before it is appended; the full text
    #: stays on the result record, so the model loses the tail and the audit does not.
    #:
    #: This is the bound that actually bites first in practice: a run's real exposure is
    #: one unbounded tool result, not a long conversation.
    max_tool_result_tokens: int = 4000
    self_repair_enabled: bool = True
    approval_park_timeout: float | None = None
    default_persona_id: str = "default"
    #: Retrieval intelligence (docs/architecture/eval-strategy.md). ``query_rewrite_enabled`` runs a
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
    #: Adaptive multi-agent (phase 5). ``team_enabled`` is the master switch;
    #: ``max_parallel_agents`` is the platform cap a manual width is clamped against;
    #: the rest bound one sub-agent's loop and the fan-out's wall clock. A fan-out is
    #: additionally impossible unless the host wires ``AgentDeps.subagent_roster`` —
    #: the mechanism is core-owned, the roster's CONTENT is the adapter's.
    team_enabled: bool = True
    max_parallel_agents: int = 4
    max_concurrent_agents: int = 3
    subagent_max_steps: int = 4
    subagent_timeout_s: float = 45.0
    #: The whole fan-out's wall clock, and a **backstop above** the per-lane bounds
    #: rather than a second, tighter deadline competing with them. The shipped numbers
    #: have to fit inside it or lanes get cut by the team clock before their own fires,
    #: and the synthesis then says "was cut short" about an agent that in fact timed out:
    #: ``max_parallel_agents / max_concurrent_agents`` = 2 waves × ``subagent_timeout_s``
    #: (90s) + ``_STAGGER_S`` × 3 (0.75s) + the shared pool's own 20s = 110.75s worst
    #: case. 90.0 did not fit; 120.0 does, with the arithmetic written down so the next
    #: change to any of the four numbers can be checked against it.
    team_wall_clock_s: float = 120.0

    def as_dict(self) -> dict[str, Any]:
        """Return the effective knob values as a plain JSON-friendly dict.

        Every field is surfaced (enums as their string ``value``) so the harness UI —
        and :func:`aegis.agent.harness_config` — can render and round-trip the effective
        configuration without importing this dataclass or any host type. The set of keys
        is the complete, authoritative list of tweakable knobs.
        """
        return {
            "gate_min_risk": self.gate_min_risk.value,
            "stream_chunk_words": self.stream_chunk_words,
            "max_plan_iterations": self.max_plan_iterations,
            "max_trajectory_tokens": self.max_trajectory_tokens,
            "max_tool_result_tokens": self.max_tool_result_tokens,
            "self_repair_enabled": self.self_repair_enabled,
            "approval_park_timeout": self.approval_park_timeout,
            "default_persona_id": self.default_persona_id,
            "query_rewrite_enabled": self.query_rewrite_enabled,
            "agentic_retrieval_enabled": self.agentic_retrieval_enabled,
            "agentic_retrieval_max_rounds": self.agentic_retrieval_max_rounds,
            "answer_cache_enabled": self.answer_cache_enabled,
            "team_enabled": self.team_enabled,
            "max_parallel_agents": self.max_parallel_agents,
            "max_concurrent_agents": self.max_concurrent_agents,
            "subagent_max_steps": self.subagent_max_steps,
            "subagent_timeout_s": self.subagent_timeout_s,
            "team_wall_clock_s": self.team_wall_clock_s,
        }


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
    check_input: InputGuardFn
    check_output: OutputGuardFn
    tool_definitions_for: ToolDefsFn
    run_tool: RunToolFn
    tool_risk: RiskFn
    render_system_prompt: RenderPromptFn
    #: Whether a named tool only reads. Defaults to "nothing is read-only", which is
    #: exactly the behaviour every caller had before this seam existed, so no existing
    #: construction or test fake changes meaning. The host wires the real registry here.
    tool_read_only: ReadOnlyFn = field(default=lambda _name: False)
    #: How to prove a write landed. Defaults to "no write can be read back", which makes
    #: the verifier fall through to its judge rather than assume success.
    read_back_for: ReadBackFn = field(default=lambda _name, _args: None)
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
    #: Query-embedding provider for **memory recall**. Both memory branches need a
    #: query vector to rank semantic facts, but neither can get one from ``retrieve``:
    #: ``recall_memory`` runs upstream of it, and ``answer_memory`` is on a branch that
    #: never reaches it. Without this hook ``assemble(query_vec=None)`` silently falls
    #: back to recency-only facts, i.e. semantic recall that is not semantic.
    #: ``None`` keeps that degraded behaviour for test fakes; the host wires the
    #: gateway embedder. Best-effort: a failure degrades to recency, never fails a run.
    embed_query: EmbedQueryFn | None = None
    #: The **sub-agent** roster the adaptive fan-out draws its team from — the adapter's
    #: declaration of which sub-agents exist, what each is for, and which tools each may
    #: reach. Deliberately ``None`` by default and deliberately NOT defaulted to a core
    #: roster: the core owns the fan-out *mechanism*, a roster is domain *content*, and
    #: inventing one here would make every host fan out to agents it never declared.
    #: ``None`` ⇒ no team is possible and every turn is SINGLE, whatever was asked.
    subagent_roster: SubAgentRosterFn | None = None
    #: The ``TOOL_RESULT`` rail: screen a tool's output **before** it enters any agent's
    #: context (§5.7). A tool result is third-party text arriving without a human having
    #: typed it, which is the OWASP LLM01 surface — and until this seam existed the only
    #: caller of the rail in the whole codebase was web search, so a poisoned record,
    #: row or summary from any other tool was pasted verbatim into the generation prompt
    #: and into every lane's transcript.
    #:
    #: ``None`` does **not** mean unscreened: the rail is deliberately the *inbound*
    #: chain (see ``Guardrails.check_tool_result``), so the graph falls back to
    #: ``check_input`` — which every host and every fake already wires — and a deployment
    #: cannot end up with tool results screened by nothing simply by not knowing about a
    #: new field. A host binds ``guardrails.check_tool_result`` here to additionally get
    #: the tool named in the verdict's rationale.
    check_tool_result: InputGuardFn | None = None
    #: The LLM-Ops registry read for a sub-agent's system prompt (§5.9b). Bound host-side
    #: to ``aegis.ops.registry.get_cached_active`` — the SAME process-wide active cache
    #: the main persona prompt already resolves through, so improving a sub-agent's
    #: prompt is promoting a version through the existing eval gate rather than editing a
    #: string in a file. ``None`` (or a miss, or a raise) ⇒ the adapter's shipped
    #: ``SubAgentSpec.system_prompt`` is the floor, exactly as the main prompt behaves:
    #: a registry outage degrades to the shipped prompt, never to none.
    active_prompt: ActivePromptFn | None = None
    #: Per-lane skill cards (§10.2 tier 1, per agent). See :data:`SkillCardsFn`. A lane
    #: that has this seam replaces the shared block's skills section with its own
    #: resolved set; a lane without it inherits the shared one. Best-effort in both
    #: directions: a raise or a timeout leaves the inherited block in place rather than
    #: failing the lane, because a skills outage must not be why an agent does not run.
    skill_cards_for: SkillCardsFn | None = None
