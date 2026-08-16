"""Shared API contracts — the interface between backend and frontend.

`docs/architecture/backend.md` §7 and `docs/learn/30-frontend.md` both insist this schema is agreed
*before* either side builds against it. Everything the frontend renders during a
run arrives as one of the `StreamEvent` variants below; every endpoint request /
response is a model here. Keep this file the single source of truth and generate
the TypeScript types from it (do not hand-maintain a parallel copy).

SSE wire format: each event is emitted as an SSE message whose `event:` field is
the variant's `type` and whose `data:` field is the model's JSON.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from aegis.core.types import (  # noqa: F401 - re-exported for identity, see docstrings below
    ApprovalDecision,
    GuardStage,
    GuardVerdict,
    RiskLevel,
    RunStatus,
)
from aegis.forecast.types import (  # noqa: F401 - re-exported: identity with aegis.forecast
    BacktestReport,
    BudgetBurndown,
    BurndownPoint,
    CandidateScore,
    ExcludedModel,
    ForecastResult,
    HorizonPoint,
)
from aegis.governance.types import (  # noqa: F401 - re-exported: identity with aegis.governance
    AdminUserRow,
    AuditLogRow,
    BudgetRow,
    GovernanceDashboard,
    Role,
    TenantRow,
    UsageByModel,
    UsageSeriesPoint,
)
from aegis.ml.types import (  # noqa: F401 - re-exported: identity with the ML spine's types
    EnsembleMember,
    MLExplainResponse,
    ModelCard,
    ShapFeature,
)
from aegis.retrieval.types import (  # noqa: F401 - re-exported: identity with aegis.retrieval
    FusionMethod,
    GraphEdge,
    GraphNode,
    RetrievalOrigin,
)
from aegis.security.posture import (  # noqa: F401 - re-exported: identity with aegis.security
    PostureEntry,
    PostureSignals,
)
from aegis.vision import (  # noqa: F401 - re-exported: identity with aegis.vision
    ControlReport,
    ImageFacts,
    OutputRailVerdict,
    PIIRegion,
    ScreenVerdict,
    VisionAnalysis,
)
from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────


# ``Role`` (the four-valued RBAC role) now lives in ``aegis.governance.types`` — a
# governance concept — and is re-exported above under its historical name/location so
# every existing importer (routes, agent, schemas) is unchanged.


# ``RunStatus`` and ``GuardStage`` now live in ``aegis.core.types`` (shared cross-module
# contracts driven by ``aegis.agent``) and are re-exported above under their historical
# name/location so every existing importer (routes, agent, schemas) is unchanged.


# GuardVerdict now lives in ``aegis.core.types`` (imported above) so the backend
# and the ``aegis`` package can never define diverging verdict enums — this
# module re-exports it under its historical name/location for every existing
# importer (``pass`` | ``block`` | ``redact``, plus an additive ``flag``).


# ``RiskLevel`` now lives in ``aegis.core.types`` (a shared cross-module contract, like
# ``GuardVerdict``) and is re-exported above under its historical name/location so the
# approvals model and every existing importer are unchanged.


# RetrievalOrigin, FusionMethod re-exported above from aegis.retrieval.types (§4.3);
# GraphNode, GraphEdge re-exported below at their original position in this file so
# the class ordering (and any doc anchors referencing it) stays stable.


# ─────────────────────────────────────────────────────────────────────────────
# SSE stream events (discriminated union on `type`)
# ─────────────────────────────────────────────────────────────────────────────


class _BaseEvent(BaseModel):
    """Fields common to every streamed event."""

    run_id: str = Field(description="Correlates all events of one query run.")
    seq: int = Field(description="Monotonic sequence number within the run.")


class RunStarted(_BaseEvent):
    """A run has begun; carries the trace id for observability correlation."""

    type: Literal["run_started"] = "run_started"
    trace_id: str


class NodeStarted(_BaseEvent):
    """The agent entered a graph node (a visible step in the plan)."""

    type: Literal["node_started"] = "node_started"
    node: str = Field(description="Node name, e.g. 'plan', 'retrieve', 'generate'.")
    label: str = Field(description="Human-readable step label for the trace panel.")


class NodeFinished(_BaseEvent):
    """A graph node completed; carries its timing and (if it called a model) usage.

    Emitted once per node after it runs so the frontend can show the whole process
    with per-step latency and cost. ``model`` and the token/cost fields are only
    populated for nodes that made an LLM call (e.g. ``plan``, ``generate``).
    """

    type: Literal["node_finished"] = "node_finished"
    node: str = Field(description="Node name that just finished.")
    label: str = Field(description="Human-readable step label for the trace panel.")
    duration_ms: int = Field(description="Wall-clock time the node body took.")
    model: str | None = Field(
        default=None, description="Deployment id used, if the node called a model."
    )
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


class Reasoning(_BaseEvent):
    """A chunk of the planner's reasoning/plan text (glass-box thinking)."""

    type: Literal["reasoning"] = "reasoning"
    text: str = Field(description="A sentence (or few) of the planner's plan.")


class Redaction(BaseModel):
    """One redaction a guardrail applied (kind only — never the raw value)."""

    kind: str = Field(description="Detector kind that fired, e.g. 'EMAIL', 'SSN'.")


class Guardrail(_BaseEvent):
    """An input or output rail produced a verdict."""

    type: Literal["guardrail"] = "guardrail"
    stage: GuardStage
    verdict: GuardVerdict
    reason: str = Field(description="Why it passed/blocked/redacted (demoable).")
    layer: str | None = Field(
        default=None,
        description="Which check fired: 'pii' | 'injection' | 'schema' | ...",
    )
    redactions: list[Redaction] = Field(
        default_factory=list, description="Redactions applied (kinds only)."
    )
    before_masked: str | None = Field(
        default=None,
        description="Masked text before redaction (NEVER raw PII on the wire).",
    )
    after: str | None = Field(
        default=None, description="The masked/redacted text forwarded downstream."
    )


class ScoredSource(BaseModel):
    """One reranked source with its relevance score (for the retrieval panel)."""

    id: str = Field(description="Source/chunk identifier.")
    label: str = Field(description="Short snippet of the source text (for display).")
    score: float = Field(description="Rerank relevance score (higher is better).")


class RetrievalStep(_BaseEvent):
    """Retrieval progress; carries the graph delta so the viz can animate."""

    type: Literal["retrieval"] = "retrieval"
    status: Literal["started", "candidates", "reranked", "done"]
    num_candidates: int = 0
    scored_sources: list[ScoredSource] = Field(default_factory=list)
    touched_nodes: list[GraphNode] = Field(default_factory=list)
    touched_edges: list[GraphEdge] = Field(default_factory=list)


class ToolCall(_BaseEvent):
    """The agent decided to call an action tool."""

    type: Literal["tool_call"] = "tool_call"
    call_id: str
    tool: str
    args: dict = Field(default_factory=dict)
    risk: RiskLevel = RiskLevel.LOW


class ToolResult(_BaseEvent):
    """An action tool returned (or failed)."""

    type: Literal["tool_result"] = "tool_result"
    call_id: str
    ok: bool
    summary: str


class ApprovalRequired(_BaseEvent):
    """The run paused at the human-in-the-loop gate (bounded autonomy)."""

    type: Literal["approval_required"] = "approval_required"
    approval_id: str
    action: str = Field(description="The proposed action awaiting approval.")
    args: dict = Field(default_factory=dict)
    risk: RiskLevel
    rationale: str = Field(description="Why the gate triggered (risk/uncertainty).")


class AnswerChunk(_BaseEvent):
    """A streamed chunk of the final answer text."""

    type: Literal["token"] = "token"
    text: str


class RunFinished(_BaseEvent):
    """Terminal event; carries usage for the token/cost dashboard."""

    type: Literal["run_finished"] = "run_finished"
    status: RunStatus
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    cache_hit: bool = False


class ErrorEvent(_BaseEvent):
    """A terminal error event for a failed run."""

    type: Literal["error"] = "error"
    message: str


class ApprovalQueued(_BaseEvent):
    """The run was persisted to the durable approvals inbox (§1.3).

    Distinct from :class:`ApprovalRequired` (the in-run, socket-held gate): this
    event announces that a durable ``PENDING`` row exists so the run survives a
    restart and can be resolved later from the async inbox, with an SLA deadline
    and an escalation tier.
    """

    type: Literal["approval_queued"] = "approval_queued"
    approval_id: str
    action: str = Field(description="The proposed action awaiting approval.")
    args: dict = Field(default_factory=dict)
    risk: RiskLevel
    rationale: str = Field(description="Why the gate triggered (risk/uncertainty).")
    sla_deadline: str | None = Field(
        default=None, description="ISO 8601 UTC deadline before SLA escalation fires."
    )
    assignee_tier: str | None = Field(
        default=None, description="Approver tier the row is currently assigned to."
    )


class ProvenanceEvent(_BaseEvent):
    """Where the retrieval answer context came from (§4.3) — honest, never silent.

    Mirrors :class:`app.retrieval.models.Provenance`; surfaced so the UI/audit can
    show "answered from cache of query X at T" or "vector+graph fused via RRF".
    """

    type: Literal["provenance"] = "provenance"
    origins: list[RetrievalOrigin] = Field(
        default_factory=list, description="Per-source origins that contributed."
    )
    fusion: FusionMethod = Field(
        default=FusionMethod.NONE, description="How the ranked lists were fused."
    )
    cache_hit: bool = False
    cache_kind: str | None = Field(
        default=None, description="'cache-exact' | 'cache-near' when served from cache."
    )
    original_query: str | None = Field(
        default=None, description="The original cached query, on a cache hit."
    )
    cached_at: str | None = Field(
        default=None, description="ISO 8601 UTC time the cached entry was written."
    )


class BudgetExceeded(_BaseEvent):
    """A per-tenant/user budget or rate limit was hit at the gateway (§3.3).

    Terminal event — the model call was refused before spend, so the run degrades to
    "budget exceeded" instead of runaway cost.
    """

    type: Literal["budget_exceeded"] = "budget_exceeded"
    scope: str = Field(description="Which level tripped: 'tenant' | 'user'.")
    scope_id: int | None = Field(default=None, description="Id of the tripped scope.")
    limit_type: str = Field(
        description="Which cap tripped: 'token_cap' | 'usd_cap' | 'rpm' | 'tpm'."
    )
    limit: float | None = Field(default=None, description="The configured cap value.")
    used: float | None = Field(default=None, description="Consumption at refusal time.")
    message: str = Field(description="Human-readable explanation for the UI/audit.")


class Reflection(_BaseEvent):
    """One self-repair reflection after an action (Reflexion-style bounded loop).

    Emitted by the ``reflect`` node once per iteration: it judges whether the goal
    is met from the executed :class:`ToolResult` outcomes and decides whether to loop
    back to ``plan`` for another bounded round or finalise the answer. Purely additive
    and back-compatible — a client that does not know this variant simply ignores it.
    """

    type: Literal["reflection"] = "reflection"
    iteration: int = Field(
        description="1-based planning round this reflection follows (hard-capped)."
    )
    max_iterations: int = Field(
        description="The configured iteration budget (hard cap on planning rounds)."
    )
    done: bool = Field(description="Whether the goal was judged met (all actions ok).")
    will_retry: bool = Field(
        description="Whether the agent loops back to plan for another round."
    )
    reason: str = Field(description="Demoable explanation of the self-repair decision.")


class RoutingEvent(_BaseEvent):
    """The supervisor routed the turn to a specialist (the visible hand-off; additive).

    Emitted once by the ``route`` node, right after the input rail and before the
    specialist runs. It makes the multi-agent hand-off auditable: which specialist role
    the turn was dispatched to (``qa`` → the full retrieve+tools pipeline, ``memory`` →
    the memory specialist), a demoable reason, and whether the cheap-LLM tiebreak was
    consulted. A client that does not know this variant simply ignores it, so it is
    fully back-compatible.
    """

    type: Literal["routing"] = "routing"
    role: str = Field(description="The specialist role the turn was dispatched to.")
    reason: str = Field(description="Demoable explanation of the routing decision.")
    used_llm: bool = Field(
        default=False,
        description="Whether the cheap-LLM tiebreak was consulted (else deterministic).",
    )


class MemoryEvent(_BaseEvent):
    """Long-term memory recall summary for one turn (glass-box; purely additive).

    Emitted once by the ``recall_memory`` node when long-term memory is active (a
    ``session_id`` and resolved subject are present). It surfaces how much durable
    context was recalled into the working-memory block — nothing on the single-shot
    path, where the node is a silent pass-through. A client that does not know this
    variant simply ignores it, so it is fully back-compatible.
    """

    type: Literal["memory"] = "memory"
    recalled_fact_count: int = Field(
        default=0, description="Number of semantic facts recalled into working memory."
    )
    recalled_message_count: int = Field(
        default=0, description="Number of episodic/raw turns recalled into working memory."
    )
    tokens_used: int = Field(
        default=0, description="Token size of the assembled working-memory block."
    )


StreamEvent = Annotated[
    RunStarted
    | NodeStarted
    | NodeFinished
    | Reasoning
    | Guardrail
    | RetrievalStep
    | ToolCall
    | ToolResult
    | ApprovalRequired
    | AnswerChunk
    | RunFinished
    | ErrorEvent
    | ApprovalQueued
    | ProvenanceEvent
    | BudgetExceeded
    | Reflection
    | MemoryEvent
    | RoutingEvent,
    Field(discriminator="type"),
]
"""Any event the frontend may receive over the `/query` SSE stream."""


# ─────────────────────────────────────────────────────────────────────────────
# Request / response models per endpoint (see docs/architecture/backend.md §10)
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Platform identity — the Aegis capabilities manifest (see app/capabilities.py)
# ─────────────────────────────────────────────────────────────────────────────


class AegisModuleRow(BaseModel):
    """One Aegis module in the capabilities manifest — branded name + honest tech.

    The API projection of :class:`app.capabilities.AegisModule`. Mirrors it field
    for field so the manifest is exposed verbatim (no hiding, no renaming). ``tech``
    is always carried alongside ``name`` — the branding never stands without the
    real technology underneath.
    """

    key: str = Field(description="Stable machine key, e.g. 'gateway'.")
    name: str = Field(description="Branded module name, e.g. 'Aegis Gateway'.")
    tech: str = Field(description="Honest underlying tech, e.g. 'LiteLLM'.")
    summary: str = Field(description="One honest line describing what the module does.")
    category: str = Field(
        description="Coarse grouping: runtime | knowledge | trust | ops | platform."
    )
    module_path: str = Field(
        description="Importable path of the real implementing code, e.g. 'app.core.llm'."
    )
    status: str = Field(description="'live' (always runs) or 'optional' (gated dependency).")


class CapabilitiesResponse(BaseModel):
    """Body for `GET /platform/capabilities` — the whole Aegis module manifest.

    An honest, machine-readable "what Aegis is" surface: the product name/tagline
    plus every branded module paired with its real tech, so the frontend Platform
    view (and any integrator) can render one cohesive product from one source.
    """

    product: str = Field(description="Product name — 'Aegis'.")
    tagline: str = Field(description="One-line honest product description.")
    module_count: int = Field(description="Number of Aegis modules declared.")
    modules: list[AegisModuleRow] = Field(
        default_factory=list, description="Every Aegis module, branded name + honest tech."
    )


class PublicMetricsResponse(BaseModel):
    """Body for `GET /platform/public-metrics` — the pre-login efficiency figures.

    A deliberately **narrow** subset of :class:`MetricsResponse`, safe to serve
    without a bearer token because it carries ratios and counts only. The absolute
    money figures (``cost_saved_usd``, ``baseline_cost_usd``,
    ``cost_per_1k_queries_usd``), the effective ``routing`` map and everything
    per-tenant stay behind ``require_auth`` on ``GET /metrics``: publishing a cost
    base invites "on what workload?", a question the landing page cannot answer.

    Every field is nullable-or-zero by design and the console renders an honest
    "not yet measured" rather than a fabricated figure — the same no-fakes rule the
    authenticated dashboards follow. ``tests/api/test_public_surfaces.py`` asserts
    the withheld field names never appear in this body.
    """

    cache_hit_rate: float = Field(
        description="Measured share of retrievals served from the semantic cache."
    )
    small_model_share: float = Field(
        description="Measured share of chat calls routed to a small model."
    )
    total_calls: int = Field(
        default=0,
        description=(
            "Chat completions served since this process started. Process-wide, not "
            "per-day; resets on restart."
        ),
    )
    actions_approved: int = Field(
        default=0,
        description="Human-gate approvals cleared. 0 when none — never fabricated.",
    )
    p95_latency_ms: float | None = Field(
        default=None,
        description="95th-percentile run duration, or null when no runs recorded.",
    )


class AboutResponse(BaseModel):
    """Body for `GET /about` — a trivial product identity card."""

    product: str = Field(description="Product name — 'Aegis'.")
    version: str = Field(description="API/product version.")
    tagline: str = Field(description="One-line honest product description.")
    modules: int = Field(description="Number of Aegis modules declared.")


class LoginRequest(BaseModel):
    """Body for `POST /auth/login`."""

    username: str
    password: str


class LoginResponse(BaseModel):
    """Response for `POST /auth/login` — the role, tenant and bearer (JWT) token.

    ``tenant_id`` is additive (optional) so the demo/global principals that carry no
    tenant still serialise; the frontend reads it to scope tenant-admin surfaces.
    """

    role: Role
    token: str
    tenant_id: int | None = None


class QueryRequest(BaseModel):
    """Body for `POST /query` (response is the SSE stream, not JSON)."""

    query: str
    persona: str | None = Field(
        default=None, description="Adapter persona id; scopes data + tools."
    )
    session_id: str | None = Field(
        default=None,
        description="Conversation/session id for multi-turn long-term memory. When "
        "omitted the run is single-shot and memory stays inert (no behaviour change).",
    )


class GraphResponse(BaseModel):
    """Body for `GET /graph` — the current context graph for the viz."""

    nodes: list[GraphNode]
    edges: list[GraphEdge]


class MLExplainRequest(BaseModel):
    """Body for `POST /ml/explain` — the features for one prediction."""

    features: dict = Field(description="Feature name → value for one prediction.")


class MetricsResponse(BaseModel):
    """Body for `GET /metrics` — live figures for the efficiency dashboard."""

    cache_hit_rate: float
    small_model_share: float
    cost_per_1k_queries_usd: float
    quality_score: float | None = None
    routing: dict[str, str] = Field(description="Effective role → model map.")
    cost_saved_usd: float = Field(
        default=0.0,
        description="Measured savings vs an all-generation-model baseline.",
    )
    baseline_cost_usd: float = Field(
        default=0.0,
        description="What the chat calls would have cost at the generation rate.",
    )
    total_calls: int = Field(
        default=0,
        description=(
            "Measured chat completions served since this process started (the "
            "gateway usage tally). Not a per-day figure — the honest process-wide "
            "count of LLM calls; resets on restart."
        ),
    )
    actions_approved: int = Field(
        default=0,
        description=(
            "Count of human-gate approvals that were cleared (durable approvals "
            "rows in the terminal APPROVED state). 0 when none / the store is "
            "unavailable — never fabricated."
        ),
    )
    p95_latency_ms: float | None = Field(
        default=None,
        description=(
            "95th-percentile whole-run duration in milliseconds, from the "
            "per-process latency window (aegis.observability.latency_summary). "
            "Null when no runs have been recorded — an honest empty state."
        ),
    )


# ``ApprovalDecision`` now lives in ``aegis.core.types`` (a shared cross-module contract
# consumed by ``aegis.agent``'s approvals + orchestrator) and is re-exported above under
# its historical name/location so every existing importer is unchanged.


class ApprovalRequest(BaseModel):
    """Body for `POST /approval` — resolve a paused action."""

    approval_id: str
    decision: ApprovalDecision


class ApprovalResponse(BaseModel):
    """Response for `POST /approval` — whether the decision was accepted."""

    approval_id: str
    accepted: bool


class ApprovalRow(BaseModel):
    """One row of the durable approvals inbox (`GET /approvals`; §1.3).

    The admin-facing projection of an :class:`app.data.models.Approval`. ``status``
    is the raw :class:`app.data.models.ApprovalStatus` value (kept as a plain string
    here so this contract never imports the data layer). ``sla_deadline`` and
    ``created_at`` are serialised as ISO 8601 UTC strings for the frontend.
    """

    id: str
    run_id: str
    tenant_id: int | None = Field(
        default=None, description="Owning tenant (for cross-tenant isolation; §3.3)."
    )
    action: str = Field(description="The proposed action awaiting a decision.")
    args: dict = Field(default_factory=dict)
    risk: RiskLevel
    rationale: str | None = Field(
        default=None, description="Why the gate fired (risk/uncertainty)."
    )
    status: str = Field(description="Lifecycle status (pending/approved/…).")
    persona: str | None = Field(default=None, description="Persona that raised the run.")
    sla_deadline: str | None = Field(
        default=None, description="ISO 8601 UTC deadline before SLA escalation fires."
    )
    created_at: str = Field(description="ISO 8601 UTC time the row was enqueued.")
    ml_snapshot: dict = Field(
        default_factory=dict,
        description=(
            "Model evidence frozen at gate time. No longer populated — the agent "
            "graph runs no ML step — so this is {} on every row raised since. Kept "
            "on the contract because the underlying column is kept."
        ),
    )


class ApprovalInboxResponse(BaseModel):
    """Body for `GET /approvals` — the pending durable-approval rows."""

    rows: list[ApprovalRow]


class ApprovalDecisionRequest(BaseModel):
    """Body for `POST /approvals/{id}/decision` — resolve a durable approval."""

    decision: ApprovalDecision


class ApprovalDecisionResponse(BaseModel):
    """Response for `POST /approvals/{id}/decision` — the resolved status."""

    id: str
    status: str = Field(description="The approval's status after the decision.")
    accepted: bool = Field(
        description="Whether this call effected the decision (idempotent: False on replay)."
    )


# ``AuditLogRow`` now lives in ``aegis.governance.types`` and is re-exported above under
# its historical name so the audit response wrapper and every importer are unchanged.


class AuditLogResponse(BaseModel):
    """Body for `GET /audit` — recent audit rows, newest first (admin only)."""

    rows: list[AuditLogRow]


# ─────────────────────────────────────────────────────────────────────────────
# Admin governance surfaces (§3.3) — tenants / users / budgets / usage
# ─────────────────────────────────────────────────────────────────────────────


# ``TenantRow`` / ``AdminUserRow`` now live in ``aegis.governance.types`` and are
# re-exported above under their historical names/location.


class AdminTenantsResponse(BaseModel):
    """Body for `GET /admin/tenants` — every tenant (platform-admin only)."""

    rows: list[TenantRow]


class AdminUsersResponse(BaseModel):
    """Body for `GET /admin/users` — users, scoped to the caller's tenant."""

    rows: list[AdminUserRow]


class UserRoleUpdateRequest(BaseModel):
    """Body for `POST /admin/users/{user_id}/role` — reassign a user's RBAC role."""

    role: Role = Field(description="The new coarse role to assign the user.")


class AdminUserCreateRequest(BaseModel):
    """Body for `POST /admin/users` — provision a new user with a role + password."""

    username: str = Field(min_length=1, max_length=255, description="Unique login name.")
    role: Role = Field(description="The coarse role to grant (admin/ai_team/devops/client).")
    tenant_id: int | None = Field(
        default=None, description="Tenant the user belongs to; null for a platform user."
    )
    email: str | None = Field(default=None, max_length=320, description="Optional contact email.")
    password: str | None = Field(
        default=None, min_length=8, description="Plaintext password; Argon2-hashed on write."
    )


class TenantCreateRequest(BaseModel):
    """Body for `POST /admin/tenants` — create a client/tenant (platform-admin only)."""

    name: str = Field(min_length=1, max_length=255, description="Unique tenant (client) name.")


# ``BudgetRow`` now lives in ``aegis.governance.types`` and is re-exported above.


class AdminBudgetsResponse(BaseModel):
    """Body for `GET /admin/budgets` — the matching budget rows."""

    rows: list[BudgetRow]


class BudgetUpsertRequest(BaseModel):
    """Body for `POST /admin/budgets` — create or update a cap for a scope+window."""

    scope_type: str = Field(description="'tenant' | 'user'.")
    scope_id: int = Field(description="Id of the tenant or user the cap governs.")
    window: str = Field(default="day", description="'day' | 'month'.")
    token_cap: int | None = None
    usd_cap: float | None = None
    rpm: int | None = None
    tpm: int | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Long-term memory read/admin surfaces (docs/architecture/memory-spec.md §D)
# ─────────────────────────────────────────────────────────────────────────────


class MemoryFactRow(BaseModel):
    """One bitemporal semantic fact (`GET /memory/facts`).

    Currently-valid facts are those with ``invalid_at is None and expired_at is None``
    (``is_valid``); superseded/invalidated rows are retained for the belief timeline and
    surfaced only when ``include_invalid=true``. All timestamps are ISO 8601 UTC strings.
    """

    id: int
    subject_id: str
    fact_type: str
    subject: str
    predicate: str
    object: str
    text: str
    confidence: float
    importance: int
    access_count: int = 0
    valid_at: str | None = None
    invalid_at: str | None = None
    created_at: str | None = None
    expired_at: str | None = None
    source_turn_ids: list[int] = Field(default_factory=list)
    supersedes_id: int | None = None
    is_valid: bool = Field(description="Whether this is a currently-valid (hot-recall) fact.")


class MemoryFactsResponse(BaseModel):
    """Body for `GET /memory/facts` — the subject's semantic facts."""

    subject: str
    rows: list[MemoryFactRow] = Field(default_factory=list)


class MemoryProfileResponse(BaseModel):
    """Body for `GET /memory/profile` — the structured "human block" profile JSON."""

    subject: str
    data: dict = Field(default_factory=dict)
    updated_at: str | None = None


class MemorySessionRow(BaseModel):
    """One conversation thread (`GET /memory/sessions`)."""

    id: str
    subject_id: str
    persona: str | None = None
    turn_count: int = 0
    summary: str | None = None
    created_at: str | None = None
    last_active_at: str | None = None


class MemorySessionsResponse(BaseModel):
    """Body for `GET /memory/sessions` — the subject's conversation threads."""

    subject: str
    rows: list[MemorySessionRow] = Field(default_factory=list)


class MemoryMessageRow(BaseModel):
    """One episodic turn (`GET /memory/sessions/{id}/messages`)."""

    id: int
    session_id: str
    turn_index: int
    role: str
    origin: str
    content: str
    importance: int = 5
    created_at: str | None = None


class MemoryMessagesResponse(BaseModel):
    """Body for `GET /memory/sessions/{id}/messages` — that session's turns, in order."""

    session_id: str
    subject: str
    rows: list[MemoryMessageRow] = Field(default_factory=list)


class MemoryWriteRow(BaseModel):
    """One fact-write changelog entry (`GET /memory/writes`) — the "why I believe X" trail."""

    id: int
    op: str = Field(description="ADD | UPDATE | INVALIDATE | NOOP.")
    fact_id: int | None = None
    before: dict = Field(default_factory=dict)
    after: dict = Field(default_factory=dict)
    reason: str | None = None
    model: str | None = None
    trace_id: str | None = None
    ts: str | None = None


class MemoryWritesResponse(BaseModel):
    """Body for `GET /memory/writes` — the subject's fact-write changelog, newest first."""

    subject: str
    rows: list[MemoryWriteRow] = Field(default_factory=list)


class RecallDebugItem(BaseModel):
    """One ranked recalled item with its score (the glass-box recall view)."""

    key: str
    text: str
    score: float = Field(description="Precomputed relevance/similarity in [0, 1].")
    importance: int = 5
    age_days: float = 0.0
    injected: bool = Field(
        default=False, description="Whether this item made it into the working-memory block."
    )


class RecallDebugResponse(BaseModel):
    """Body for `GET /memory/recall_debug` — what would be recalled for a live query."""

    subject: str
    query: str
    facts: list[RecallDebugItem] = Field(default_factory=list)
    episodic: list[RecallDebugItem] = Field(default_factory=list)
    working_memory: str = Field(default="", description="The assembled working-memory block.")
    tokens_used: int = 0
    recalled_fact_count: int = 0
    recalled_message_count: int = 0


class MemoryForgetResponse(BaseModel):
    """Body for `POST /memory/forget` — GDPR hard-erasure receipt (audited)."""

    subject: str
    deleted_facts: int = 0
    deleted_messages: int = 0
    deleted_sessions: int = 0
    deleted_profiles: int = 0
    deleted_writes: int = 0
    deleted_jobs: int = 0


class MemoryFactDeleteResponse(BaseModel):
    """Body for `DELETE /memory/facts/{id}` — single-fact hard-erasure receipt (audited)."""

    fact_id: int
    deleted: bool


# ``UsageByModel`` / ``UsageSeriesPoint`` now live in ``aegis.governance.types`` and are
# re-exported above under their historical names/location.


class AdminUsageResponse(BaseModel):
    """Body for `GET /admin/usage` — rolled-up spend from the usage ledger (§3.3)."""

    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cost_usd: float = 0.0
    by_model: list[UsageByModel] = Field(default_factory=list)
    series: list[UsageSeriesPoint] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# LLM-Ops closed loop (`/ops/*`) — prompt-version registry, trace-eval trend,
# diagnose → release → rollback, and the staged-release approval inbox.
# ─────────────────────────────────────────────────────────────────────────────


class OpsPromptVersionRow(BaseModel):
    """One versioned system prompt in the registry (`GET /ops/prompts`)."""

    id: int
    prompt_key: str
    version: int
    status: str = Field(description="draft | staged | active | archived.")
    created_by: str | None = None
    notes: str | None = None
    created_at: str | None = Field(default=None, description="ISO 8601 UTC creation time.")


class OpsPromptsResponse(BaseModel):
    """Body for `GET /ops/prompts` — every version for a prompt key, newest first."""

    prompt_key: str
    rows: list[OpsPromptVersionRow] = Field(default_factory=list)


class OpsActivePromptResponse(BaseModel):
    """Body for `GET /ops/prompts/active` — the single live version (or none)."""

    prompt_key: str
    version: int | None = None
    status: str | None = None
    system_prompt: str | None = None
    config: dict = Field(default_factory=dict)
    created_by: str | None = None
    notes: str | None = None
    cached: bool = Field(
        default=False, description="True when served from the in-process active cache."
    )


class OpsEvalRow(BaseModel):
    """One persisted trace-eval measurement (`GET /ops/evals`)."""

    id: int
    run_id: str | None = None
    metric: str
    score: float
    passed: bool
    detail: dict = Field(default_factory=dict)
    ts: str | None = Field(default=None, description="ISO 8601 UTC measurement time.")


class OpsEvalsResponse(BaseModel):
    """Body for `GET /ops/evals` — recent eval rows, newest first."""

    rows: list[OpsEvalRow] = Field(default_factory=list)


class OpsDiagnoseRequest(BaseModel):
    """Body for `POST /ops/diagnose` — cluster failures + draft an improved prompt."""

    prompt_key: str
    limit: int = Field(default=50, ge=1, le=500)


class OpsDiagnoseResponse(BaseModel):
    """Body for `POST /ops/diagnose` — the draft id + failure breakdown."""

    draft_version_id: int | None = None
    failure_summary: str
    failures_considered: int
    metric_breakdown: dict[str, int] = Field(default_factory=dict)


class OpsReleaseRequest(BaseModel):
    """Body for `POST /ops/release` — run the eval gate + tiered decision on a draft."""

    draft_version_id: int
    autonomy: str = Field(default="tiered", description="tiered | auto | manual.")
    margin: float = Field(default=0.0, description="How much the draft must beat baseline by.")


class OpsReleaseResponse(BaseModel):
    """Body for `POST /ops/release` — the release outcome, scores and any approval id."""

    outcome: str = Field(description="promoted | staged_for_approval | rejected.")
    risk_level: str
    risk_reasons: list[str] = Field(default_factory=list)
    eval_score: float
    baseline_score: float
    reason: str
    approval_id: str | None = None


class OpsRollbackRequest(BaseModel):
    """Body for `POST /ops/rollback` — revert to the previous version for a key."""

    prompt_key: str


class OpsRollbackResponse(BaseModel):
    """Body for `POST /ops/rollback` — the newly-active version after the revert."""

    prompt_key: str
    reverted: bool
    active_version: int | None = None


class OpsReleaseApprovalRow(BaseModel):
    """One staged prompt-release awaiting a human decision (`GET /ops/releases/pending`)."""

    approval_id: str
    prompt_key: str | None = None
    draft_version_id: int | None = None
    risk: str
    reason: str | None = None
    created_at: str | None = Field(default=None, description="ISO 8601 UTC creation time.")


class OpsPendingReleasesResponse(BaseModel):
    """Body for `GET /ops/releases/pending` — the staged-release inbox."""

    rows: list[OpsReleaseApprovalRow] = Field(default_factory=list)


class OpsReleaseDecisionRequest(BaseModel):
    """Body for `POST /ops/releases/{approval_id}/decide` — resolve a staged release."""

    approved: bool


class OpsReleaseDecisionResponse(BaseModel):
    """Body for `POST /ops/releases/{approval_id}/decide` — the resolved outcome."""

    approval_id: str
    approved: bool
    outcome: str = Field(description="promoted | archived | unknown.")
    prompt_key: str | None = None
    active_version: int | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Platform surfaces — tech stack, patch check, agent risk-map, savings (§Wave-2)
#
# These mirror ``web/src/lib/api/types.ts`` **exactly** — the field names and
# unions here are the contract the Next.js console renders against. Keep them in
# lock-step. The logic that populates them lives in :mod:`app.platform`; these are
# pure data shells.
# ─────────────────────────────────────────────────────────────────────────────


class StackComponent(BaseModel):
    """One installed component in the software bill-of-materials (`GET /stack`)."""

    name: str = Field(description="Human label, e.g. 'FastAPI'.")
    category: Literal["runtime", "backend", "frontend", "infra"] = Field(
        description="Coarse layer: runtime | backend | frontend | infra."
    )
    package: str = Field(description="Distribution/package name resolved for the version.")
    version: str | None = Field(
        default=None,
        description="Real installed version, or null when the package is not installed "
        "(honest for optional-group dependencies).",
    )
    aegis_module: str | None = Field(
        default=None,
        description="Branded Aegis module this component powers, or null for shared infra.",
    )


class StackResponse(BaseModel):
    """Body for `GET /stack` — the full, live software bill of materials."""

    generated_at: str = Field(description="ISO 8601 UTC time the stack was inventoried.")
    components: list[StackComponent] = Field(default_factory=list)


class PatchCheckRequest(BaseModel):
    """Body for `POST /stack/patch-check` — optionally narrow to a subset of packages."""

    packages: list[str] | None = Field(
        default=None,
        description="Package names to check; omit/null to check the whole tracked stack.",
    )


class PatchResult(BaseModel):
    """One package's freshness verdict from the patch check."""

    name: str
    installed: str | None = Field(default=None, description="Installed version, or null.")
    latest: str | None = Field(
        default=None, description="Latest version on the registry, or null when unknown."
    )
    status: Literal["current", "outdated", "unknown"] = Field(
        description="'current'/'outdated' only after a real registry answer; else 'unknown'."
    )
    note: str | None = Field(default=None, description="Optional human note for this row.")


class PatchCheckResponse(BaseModel):
    """Body for `POST /stack/patch-check` — installed vs latest per package."""

    checked_at: str = Field(description="ISO 8601 UTC time the check ran (or was cached).")
    online: bool = Field(
        description="Whether the registry was reachable; false ⇒ results are best-effort."
    )
    note: str = Field(description="Honest summary of how to read the results.")
    results: list[PatchResult] = Field(default_factory=list)


RiskBand = Literal["low", "medium", "high"]

# Band cut-points on the 1..25 exposure scale, expressed as fractions of the worst
# cell (5 × 5 = 25): at or under a quarter of the scale is **low**, past half is
# **high**, everything between is **medium**. Stated as constants so the band is a
# published rule rather than a per-entry opinion.
_BAND_LOW_MAX = 6  # ≤ 25 × 0.25
_BAND_MEDIUM_MAX = 12  # ≤ 25 × 0.50


def risk_band(exposure: int) -> RiskBand:
    """Band a likelihood × impact exposure (1..25) as low / medium / high.

    The **single** definition of what a band means, used for the residual point so
    that the band and the coordinate can never drift apart.
    """
    if exposure <= _BAND_LOW_MAX:
        return "low"
    if exposure <= _BAND_MEDIUM_MAX:
        return "medium"
    return "high"


class RiskEntry(BaseModel):
    """One entry on the agent-risk map (OWASP-Agentic-aligned).

    Two coordinates, not one. ``likelihood`` × ``impact`` is the **inherent**
    position — where the risk sits with no Aegis control in the way.
    ``residual_likelihood`` × ``residual_impact`` is where it sits **after** the
    real control named in ``control_ref``. The movement between the two points is
    the thing worth showing a client.

    Controls overwhelmingly move **likelihood**: a human gate does not make a wrongly
    closed customer request cheaper, it makes it far less likely to ever happen. Impact
    moves only where the control genuinely shrinks the consequence (e.g. reversible
    tools).

    ``residual`` is **derived** from the residual coordinate via :func:`risk_band`
    rather than authored beside it, so a band can never contradict its own point.
    """

    id: str
    title: str
    category: str
    likelihood: int = Field(ge=1, le=5, description="Inherent 1..5 likelihood, before the control.")
    impact: int = Field(ge=1, le=5, description="Inherent 1..5 impact, before the control.")
    residual_likelihood: int = Field(
        ge=1,
        le=5,
        description="1..5 likelihood left after the control — the axis controls actually move.",
    )
    residual_impact: int = Field(
        ge=1,
        le=5,
        description="1..5 impact left after the control — moves only if the blast radius shrinks.",
    )
    control_name: str = Field(
        description="Short client-facing name of the control, e.g. 'Human approval gate'."
    )
    mitigation: str = Field(
        description="One plain-language sentence a non-engineer can read: what the control does."
    )
    control_ref: str = Field(
        description=(
            "Real file/module implementing the control — auditor provenance, not client copy. "
            "The engineering rationale for each position lives in comments in "
            "app/platform/risk_map.py rather than on the wire."
        )
    )

    @model_validator(mode="after")
    def _residual_never_exceeds_inherent(self) -> RiskEntry:
        """A control may hold a risk down; it may never make it worse."""
        if self.residual_likelihood > self.likelihood or self.residual_impact > self.impact:
            raise ValueError(
                f"{self.id}: residual ({self.residual_likelihood}×{self.residual_impact}) "
                f"exceeds inherent ({self.likelihood}×{self.impact}) — a control cannot add risk"
            )
        return self

    @property
    def inherent_exposure(self) -> int:
        """Exposure before the control: inherent likelihood × impact (1..25)."""
        return self.likelihood * self.impact

    @property
    def residual_exposure(self) -> int:
        """Exposure after the control: residual likelihood × impact (1..25)."""
        return self.residual_likelihood * self.residual_impact

    @computed_field(  # type: ignore[prop-decorator]
        description="Residual band, derived from residual_likelihood × residual_impact."
    )
    @property
    def residual(self) -> RiskBand:
        """The residual band — derived, never hand-authored (one source of truth)."""
        return risk_band(self.residual_exposure)


class RiskScale(BaseModel):
    """The 1..5 axes the map is plotted on."""

    likelihood: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5])
    impact: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5])


class RiskMapResponse(BaseModel):
    """Body for `GET /risk-map` — the agent-risk map + its scale."""

    generated_at: str = Field(description="ISO 8601 UTC time the map was generated.")
    note: str = Field(description="How to read the map (this deployment's own posture).")
    scale: RiskScale = Field(default_factory=RiskScale)
    risks: list[RiskEntry] = Field(default_factory=list)


class SavingsBreakdownRow(BaseModel):
    """One contributor to the total savings."""

    source: str
    saved_usd: float
    explanation: str = Field(description="Plain-language how-it-saves (flags any estimate).")


class SavingsResponse(BaseModel):
    """Body for `GET /savings` — baseline vs actual spend and what drove it."""

    generated_at: str = Field(description="ISO 8601 UTC time the figures were computed.")
    baseline_cost_usd: float
    actual_cost_usd: float
    saved_usd: float = Field(description="baseline − actual.")
    saved_pct: float = Field(description="Fraction saved vs baseline, 0..1.")
    note: str = Field(description="Honest framing of the figure (flags any estimate).")
    breakdown: list[SavingsBreakdownRow] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Platform read-surfaces — thin, read-only projections of the ``aegis.*``
# accessors that back the MLOps / LLMOps / evals / token-opt / harness /
# governance / security / latency / red-team dashboards (Phase-3 · Task 3).
#
# Each mirrors exactly what its accessor returns, so the routes stay thin and
# the frontend renders the same shape in mock and live mode.
# ─────────────────────────────────────────────────────────────────────────────


class EvalsReportResponse(BaseModel):
    """Body for `GET /evals/report` — the offline regression-gate rollup.

    Projects :meth:`aegis.evals.RegressionReport.as_dict` verbatim: the overall
    score, the gate verdict, one authoritative reading per metric, and the per-case
    breakdown. Computed by running the deterministic **offline** regression gate
    (``run_regression_gate`` with no LLM) — real, reproducible numbers, never a live
    LLM-judge pass. ``source`` names how the figures were produced.
    """

    overall: float = Field(description="Mean of the per-metric aggregate values.")
    passed: bool = Field(description="The CI gate verdict.")
    metrics: list[dict[str, Any]] = Field(
        default_factory=list, description="One MetricConfig-as-dict per metric."
    )
    cases: list[dict[str, Any]] = Field(
        default_factory=list, description="Per-case metric breakdown."
    )
    source: str = Field(
        default="offline_regression_gate",
        description="How the figures were produced (deterministic offline gate).",
    )


class OpsParamsResponse(BaseModel):
    """Body for `GET /ops/params` — the tunable LLM-Ops self-improvement knobs.

    Mirrors :meth:`aegis.ops.config.LoopParams.as_dict` — the effective loop params the
    release gate reads (eval margin, blast-radius fractions, safety-term list, config
    markers, tunable keys/bounds, auto-promote ceiling).
    """

    eval_margin: float
    high_diff_fraction: float
    low_diff_fraction: float
    safety_terms: list[str]
    critical_config_markers: list[str]
    tunable_config_keys: list[str]
    tunable_max_delta: dict[str, float]
    auto_promote_ceiling: str


class GatewayOptimizationResponse(BaseModel):
    """Body for `GET /gateway/optimization` — the token-optimization surface.

    ``summary`` is :func:`aegis.gateway.optimization_summary` (measured per-role savings
    vs the frontier baseline); ``config`` is :func:`aegis.gateway.optimization_config`
    (the effective routing/fallback knobs). Offline, before any real call, the summary
    figures are honest zeros / ``None`` (nothing fabricated).
    """

    summary: dict[str, Any] = Field(description="Measured savings roll-up + per-role breakdown.")
    config: dict[str, Any] = Field(description="Effective routing / fallback / baseline knobs.")


class HarnessConfigResponse(BaseModel):
    """Body for `GET /harness/config` — the agent-harness tweakable-config record.

    Mirrors :func:`aegis.agent.harness_config`: ``knobs`` is the ordered list of knob
    descriptors a UI renders an editable form from; ``effective`` is the flat
    effective-values map the graph actually reads.
    """

    knobs: list[dict[str, Any]] = Field(default_factory=list)
    effective: dict[str, Any] = Field(default_factory=dict)


class AgentTopologyNode(BaseModel):
    """One executable node of the agent graph (see `GET /agent/topology`)."""

    id: str = Field(
        description="Stable node id — exactly the name carried on node_started/node_finished."
    )
    label: str = Field(description="Human label the node's stream events carry.")
    entry: bool = Field(default=False, description="The graph's entrypoint routes here.")
    terminal: bool = Field(default=False, description="A run can finish at this node.")


class AgentTopologyEdge(BaseModel):
    """One directed edge between two executable nodes (see `GET /agent/topology`)."""

    source: str
    target: str
    conditional: bool = Field(
        default=False,
        description="True when the edge is a branch of a conditional router, not a fixed edge.",
    )


class AgentTopologyResponse(BaseModel):
    """Body for `GET /agent/topology` — the agent graph's real node/edge shape.

    Mirrors :func:`aegis.agent.graph_topology`, which reads the topology off the
    **compiled** LangGraph rather than restating it. It exists so that anything
    drawing the agent's flow — today the console's orchestration map — derives the
    picture from the graph that actually runs instead of keeping a hand-maintained
    copy that silently drifts (the previous copy showed the human gate branching out
    of the ML step, while the graph gates on tool risk in ``gate`` and never on ML).
    Read-only, and a pure function of the wiring: no run state, no tenant data.
    """

    nodes: list[AgentTopologyNode] = Field(default_factory=list)
    edges: list[AgentTopologyEdge] = Field(default_factory=list)


class SecurityPostureResponse(BaseModel):
    """Body for `GET /security/posture` — the live threat → control posture table.

    ``entries`` is :func:`aegis.security.security_posture` (one entry per major threat,
    each with a status derived from live wiring); ``signals`` is the
    :func:`aegis.security.read_signals` snapshot the statuses were derived from, so a
    caller can see *which* knob each status tracks.
    """

    entries: list[PostureEntry] = Field(default_factory=list)
    signals: PostureSignals


class LatencyResponse(BaseModel):
    """Body for `GET /latency` — per-node + per-run latency percentiles.

    Mirrors :meth:`aegis.observability.LatencySummary.as_dict`. All figures are from
    real samples in the per-process rolling window; ``empty`` is ``True`` (with no
    per-node rows and ``None`` run percentiles) when no runs have been recorded yet —
    an honest empty state, never fabricated zeros. ``source`` / ``window_capacity``
    document where the numbers came from.
    """

    run_count: int
    per_node: list[dict[str, Any]] = Field(default_factory=list)
    run_p50_ms: float | None = None
    run_p95_ms: float | None = None
    run_max_ms: float | None = None
    slowest_node: str | None = None
    source: str
    window_capacity: int | None = None
    empty: bool = False


class RedteamReportResponse(BaseModel):
    """Body for `POST /redteam/run` — the offline attack-battery report.

    Mirrors :meth:`aegis.redteam.RedTeamReport.as_dict`: the pass verdict, the
    ``overall`` roll-up (block rate + false-positive rate), the thresholds, per-category
    reports, the leaked attacks, and every attack's verdict. Runs the deterministic
    backstops only (no completer) so it is fully offline and side-effect free.
    """

    passed: bool
    overall: dict[str, Any] = Field(description="attacksTotal / attacksBlocked / blockRate / ...")
    thresholds: dict[str, Any]
    categories: list[dict[str, Any]] = Field(default_factory=list)
    leaked: list[dict[str, Any]] = Field(default_factory=list)
    false_positive_detail: list[dict[str, Any]] = Field(
        default_factory=list, alias="falsePositiveDetail"
    )
    attacks: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


# ─────────────────────────────────────────────────────────────────────────────
# Aegis Voice — POST /voice/transcribe
# ─────────────────────────────────────────────────────────────────────────────


class VoiceSegmentRow(BaseModel):
    """One time-aligned segment of a transcript (mirrors ``aegis.voice.VoiceSegment``).

    ``confidence`` is ``None`` whenever the provider reports none — which is the case
    for the fleet's hosted Whisper deployment today, because the gateway's segment
    parser carries only id/start/end/text. The console renders that as "not reported";
    it is never backfilled with a derived number.
    """

    index: int = Field(description="Position in the whole transcript (0-based).")
    start: float | None = Field(
        default=None, description="Seconds from the start of the WHOLE recording."
    )
    end: float | None = Field(default=None, description="Seconds from the start of the recording.")
    text: str = ""
    confidence: float | None = Field(
        default=None, description="Provider-reported confidence in [0,1], or null."
    )
    chunk: int = Field(default=0, description="Which chunk of a split recording produced it.")


class VoiceTranscribeResponse(BaseModel):
    """Body for `POST /voice/transcribe` — the transcript plus its rail verdict.

    Two fields carry the security contract and must be read together:

    * ``verdict`` is the **full text rail stack's** judgement of the transcript
      (transcribe-then-guard: speech is screened by exactly the rails typed input is).
    * ``agent_input`` is the only text a caller may forward to the agent. It is
      ``null`` on a BLOCK, and on a REDACT it is the *redacted* string — never the
      raw transcript. ``transcript`` stays populated as operator evidence, and a
      client that forwards it instead of ``agent_input`` has defeated the rails.

    ``controls_run`` / ``controls_skipped`` itemise the coverage: the verdict reason
    is generated from them, so it cannot claim a control that did not execute.
    """

    transcript: str = Field(default="", description="The full transcript (evidence, not input).")
    language: str | None = Field(default=None, description="Detected language, or null.")
    duration_seconds: float | None = Field(
        default=None, description="Audio duration in seconds, or null when unknown."
    )
    segments: list[VoiceSegmentRow] = Field(default_factory=list)
    has_confidence: bool = Field(
        default=False, description="Whether ANY segment carries a reported confidence."
    )
    model: str = Field(default="", description="Deployment id that answered.")
    chunk_count: int = Field(default=1, description="Requests the recording was split into.")
    chunking: str = Field(default="", description="One honest line on why it was/wasn't split.")
    cost_usd: float = Field(default=0.0, description="Ledgered cost of the transcription.")
    audio_seconds_billed: float = Field(default=0.0, description="Audio seconds billed.")
    verdict: GuardVerdict = Field(description="The text rail stack's verdict on the transcript.")
    verdict_reason: str = Field(default="", description="Why, including the coverage sentence.")
    verdict_layer: str | None = Field(default=None, description="Rail that produced the verdict.")
    redactions: list[str] = Field(
        default_factory=list, description="Redacted detector kinds (kinds only, never values)."
    )
    controls_run: list[str] = Field(default_factory=list)
    controls_skipped: list[str] = Field(default_factory=list)
    agent_input: str | None = Field(
        default=None,
        description="The ONLY text safe to send to the agent; null when the rails refused.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Forecast surface (`GET /forecast/...`) — Aegis Forecast
# ─────────────────────────────────────────────────────────────────────────────


class ForecastRefusal(BaseModel):
    """Why a forecast was NOT produced — a first-class response, not an error page.

    A time-series surface has one characteristic failure that must never be papered
    over: not enough history. Drawing a line through six points would look exactly
    like a forecast and mean nothing, so the module refuses, and the refusal travels
    to the console with its arithmetic intact — ``have`` observations, ``need``
    observations — so the UI can say *why* instead of showing an empty chart.
    """

    code: Literal[
        "insufficient_history", "degenerate_series", "fit_failed", "extra_missing"
    ] = Field(description="Machine-readable refusal reason.")
    reason: str = Field(description="Human-readable explanation, safe to render verbatim.")
    have: int | None = Field(default=None, description="Observations available, when known.")
    need: int | None = Field(default=None, description="Observations required, when known.")


class ForecastResponse(BaseModel):
    """Body for every `GET /forecast/...` route — a forecast **or** a stated refusal.

    Exactly one of ``forecast`` / ``refusal`` is populated, and ``available`` says
    which. The envelope exists so a refusal is a normal, typed, renderable outcome
    rather than an HTTP error the console would have to guess the meaning of.

    ``burndown`` is set only by the budget projection route.
    """

    available: bool = Field(description="True when `forecast` is populated.")
    forecast: ForecastResult | None = Field(
        default=None, description="The horizon-indexed forecast with its MEASURED backtest."
    )
    burndown: BudgetBurndown | None = Field(
        default=None, description="Budget burn-down projection (budget route only)."
    )
    refusal: ForecastRefusal | None = Field(
        default=None, description="Why no forecast was produced, when `available` is False."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Aegis Vision — POST /vision/analyse
# ─────────────────────────────────────────────────────────────────────────────


class VisionAnalyseRequest(BaseModel):
    """Body for `POST /vision/analyse` — one image and one question about it.

    JSON + base64 rather than multipart, deliberately: ``aegis.media``'s payloads
    already serialise their bytes as base64, browsers produce exactly this from
    ``FileReader.readAsDataURL``, and it keeps the endpoint free of a new
    ``python-multipart`` dependency. Size is bounded by the media hygiene rail's
    byte cap, which refuses an oversized payload before any model is called.

    ``mime_type`` is the client's DECLARED type and is never trusted: the hygiene
    rail sniffs the magic bytes and refuses a payload whose declaration disagrees
    with its content — that single lie is a whole rail bypass.
    """

    image_base64: str = Field(
        description="The image bytes, base64-encoded. A `data:` URL is also accepted."
    )
    mime_type: str = Field(default="image/png", description="Declared content type (verified).")
    question: str = Field(default="", description="What to ask about the image.")
    filename: str | None = Field(default=None, description="Original filename, for the audit log.")


class VisionAnalyseResponse(BaseModel):
    """Body for `POST /vision/analyse` — the analysis and its full audit record.

    ``analysis`` is :class:`aegis.vision.VisionAnalysis` verbatim (re-exported for
    identity above, so this contract cannot drift from the module's). Read three
    of its fields together or not at all:

    * ``screen`` — the image-injection screen's verdict. ``screened=False`` means
      the control could not run and the block is a fail-closed one, which is a
      different statement from "we looked and it was clean".
    * ``controls`` — one line per control **including the ones that did not run**,
      so a green result can never imply coverage nobody provided.
    * ``answer`` — empty whenever ``outcome`` is ``blocked``, because on a blocked
      run there is no model text.

    ``coverage`` is :meth:`VisionAnalysis.coverage` precomputed, so every surface
    renders the same honest one-liner instead of reassembling its own.
    """

    analysis: VisionAnalysis
    coverage: str = Field(description="One line: which controls ran, and which did not.")
