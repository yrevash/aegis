"""HTTP + SSE surface for the platform (see ``docs/architecture/backend.md`` §10).

Every endpoint from :mod:`app.api.schemas` lives here:

- ``POST /auth/login`` — issue a bearer token carrying a role (RBAC seed).
- ``POST /query`` — stream the agent's :data:`~app.api.schemas.StreamEvent`s over
  SSE; the human gate pauses mid-stream and resumes on ``POST /approval``.
- ``GET /graph`` — the accumulated context graph for the live visualisation.
- ``POST /ml/explain`` — a conformalised, SHAP-explained prediction.
- ``GET /metrics`` — the efficiency dashboard figures.
- ``POST /approval`` — resolve a paused, gated action (admin only).

RBAC is enforced with dependency guards (``require_auth`` / ``require_admin``),
and every state-changing action is written to the first-class audit log. The
agent capabilities and the ML predictor are injected through FastAPI dependencies
so the whole surface is drivable in tests with fakes and no live infrastructure.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sse_starlette import EventSourceResponse, ServerSentEvent

from app.adapter import DEFAULT_PERSONA_ID, get_persona
from app.agent import AgentDeps, decide_approval, get_approval_registry, run_agent
from app.api.schemas import (
    AboutResponse,
    AdminBudgetsResponse,
    AdminTenantsResponse,
    AdminUsageResponse,
    AdminUserCreateRequest,
    AdminUserRow,
    AdminUsersResponse,
    AegisModuleRow,
    AgentTopologyResponse,
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalInboxResponse,
    ApprovalRequest,
    ApprovalResponse,
    AuditLogResponse,
    BudgetRow,
    BudgetUpsertRequest,
    CapabilitiesResponse,
    EvalsReportResponse,
    GatewayOptimizationResponse,
    GovernanceDashboard,
    GraphEdge,
    GraphNode,
    GraphResponse,
    HarnessConfigResponse,
    LatencyResponse,
    LoginRequest,
    LoginResponse,
    MemoryFactDeleteResponse,
    MemoryFactRow,
    MemoryFactsResponse,
    MemoryForgetResponse,
    MemoryMessageRow,
    MemoryMessagesResponse,
    MemoryProfileResponse,
    MemorySessionRow,
    MemorySessionsResponse,
    MemoryWriteRow,
    MemoryWritesResponse,
    MetricsResponse,
    MLExplainRequest,
    MLExplainResponse,
    ModelCard,
    OpsActivePromptResponse,
    OpsDiagnoseRequest,
    OpsDiagnoseResponse,
    OpsEvalRow,
    OpsEvalsResponse,
    OpsParamsResponse,
    OpsPendingReleasesResponse,
    OpsPromptsResponse,
    OpsPromptVersionRow,
    OpsReleaseApprovalRow,
    OpsReleaseDecisionRequest,
    OpsReleaseDecisionResponse,
    OpsReleaseRequest,
    OpsReleaseResponse,
    OpsRollbackRequest,
    OpsRollbackResponse,
    PatchCheckRequest,
    PatchCheckResponse,
    PublicMetricsResponse,
    QueryRequest,
    RecallDebugItem,
    RecallDebugResponse,
    RedteamReportResponse,
    RiskMapResponse,
    Role,
    RunStatus,
    SavingsResponse,
    SecurityPostureResponse,
    StackResponse,
    StreamEvent,
    TenantCreateRequest,
    TenantRow,
    UserRoleUpdateRequest,
)
from app.capabilities import (
    AEGIS_MODULES,
    PRODUCT_NAME,
    PRODUCT_TAGLINE,
    PRODUCT_VERSION,
)
from app.config import get_settings
from app.core.governance import (
    GovernanceContext,
    reset_governance_context,
    set_governance_context,
)
from app.core.llm import usage_tally
from app.core.models import is_small_model, routing_table
from app.core.security import (
    PLATFORM_ADMIN,
    TENANT_ADMIN,
    coarse_role_from_fine,
    create_access_token,
    decode_access_token,
    principal_role,
    verify_password,
)
from app.data import (
    CrossTenantBudgetError,
    DuplicateTenantError,
    DuplicateUserError,
    LastPlatformAdminError,
    count_approved,
    create_tenant,
    create_user,
    effective_limits,
    get_approval,
    list_budgets,
    list_pending,
    list_recent_audit,
    list_tenants,
    list_users,
    record_audit,
    update_user_role,
    upsert_budget,
    usage_rollup,
    user_tenant_id,
)
from app.platform import (
    build_risk_map,
    build_savings,
    build_stack,
    patch_check,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ─────────────────────────────────────────────────────────────────────────────
# Authentication + RBAC (demo-grade, in-process token store)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AuthContext:
    """The authenticated principal resolved from a JWT bearer token.

    ``role`` is the true four-valued data-layer role (``admin`` / ``ai_team`` /
    ``devops`` / ``client``) that the persona and per-role guards read — carried
    honestly on the JWT's ``coarse_role`` claim, never re-derived lossily. ``fine_role``
    is the §3.3 admin sub-tier (``platform_admin`` / ``tenant_admin``) for an admin, or
    the role's own string for every other role. ``tenant_id`` / ``user_id`` pin the
    request for governance and tenant-isolation scoping.
    """

    username: str
    role: Role
    persona: str
    fine_role: str = "client"
    tenant_id: int | None = None
    user_id: int | None = None


# username → (password, coarse role). Demo credentials are a **dev-only** convenience
# for the offline money-shot demo — one login per real role (password ``demo``): they
# are consulted only when ``app_env == "dev"`` AND the username is not a real ``users``
# row, and never on a wrong password for an existing account. They are platform-scoped
# (no tenant), so their runs are ungoverned. In any non-dev environment the demo table
# is disabled entirely (C2).
_DEMO_USERS: dict[str, tuple[str, Role]] = {
    "admin": ("demo", Role.ADMIN),
    "ai": ("demo", Role.AI_TEAM),
    "aiteam": ("demo", Role.AI_TEAM),
    "devops": ("demo", Role.DEVOPS),
    "client": ("demo", Role.CLIENT),
}

_bearer = HTTPBearer(auto_error=False)


def _persona_for(role: Role) -> str:
    """Return the default persona for a coarse role.

    A ``client`` gets the self-scoped ``client`` persona; every operational role
    (``admin`` / ``ai_team`` / ``devops``) gets the full ``operations_lead`` persona.
    """
    return "client" if role is Role.CLIENT else "operations_lead"


async def _authenticate(username: str, password: str) -> AuthContext | None:
    """Return the principal for valid credentials, else ``None`` (C2).

    Authenticates against the ``users`` table first (hashed-password verification).
    The built-in demo principals are a **dev-only** fallback: they are consulted
    only when ``app_env == "dev"`` AND the username is not a real ``users`` row, so
    a real account is never overridden and a wrong password for an existing user is
    always rejected (it never falls through to the demo table). In any non-dev
    environment the demo table is disabled entirely.
    """
    real_user_exists = False
    try:
        from sqlalchemy import select

        from app.data import User, get_sessionmaker

        async with get_sessionmaker()() as session:
            row = (
                await session.execute(select(User).where(User.username == username))
            ).scalars().first()
        if row is not None:
            # A real account exists: it is the ONLY authority for this username.
            real_user_exists = True
            if row.is_active and verify_password(password, row.password_hash):
                return AuthContext(
                    username=row.username,
                    role=row.role,
                    persona=_persona_for(row.role),
                    fine_role=principal_role(row.role, row.tenant_id),
                    tenant_id=row.tenant_id,
                    user_id=row.id,
                )
            # Inactive or wrong password → reject; never consult the demo table.
            return None
    except Exception:  # noqa: BLE001 - DB optional; dev may fall back to demo principals
        logger.debug("Users-table auth lookup failed.", exc_info=True)

    # No real row for this username (or the DB was unreachable). The demo backdoor
    # is dev-only and closed everywhere else.
    if real_user_exists or not get_settings().is_dev:
        return None
    record = _DEMO_USERS.get(username)
    if record is None or record[0] != password:
        return None
    _, role = record
    return AuthContext(
        username=username,
        role=role,
        persona=_persona_for(role),
        fine_role=principal_role(role, None),
        tenant_id=None,
        user_id=None,
    )


def _mint_token(ctx: AuthContext) -> str:
    """Mint a signed JWT access token carrying ``ctx``'s claims (§3.3).

    Both the fine role (for the admin sub-tier guards) and the true coarse role (for
    the per-role guards) are carried, so :func:`require_auth` reads the four-valued
    role directly instead of re-deriving it.
    """
    return create_access_token(
        user_id=ctx.user_id,
        username=ctx.username,
        role=ctx.fine_role,
        coarse_role=ctx.role.value,
        tenant_id=ctx.tenant_id,
    )


def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AuthContext:
    """Resolve and require an authenticated principal from the JWT bearer token."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid bearer token.",
        )
    try:
        claims = decode_access_token(credentials.credentials)
    except Exception as exc:  # noqa: BLE001 - any decode failure is an auth failure
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid bearer token.",
        ) from exc
    # The coarse role is carried honestly on the token (``coarse_role`` claim); read it
    # directly rather than collapsing the fine role to a lossy admin/user pair.
    try:
        coarse = Role(claims.coarse_role)
    except ValueError as exc:  # unknown/tampered coarse role → auth failure
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid bearer token.",
        ) from exc
    # Defense-in-depth (§3.3): the fine ``role`` and the coarse ``coarse_role`` claim
    # must be mutually consistent. Every real mint path derives ``coarse_role`` from the
    # fine role via ``coarse_role_from_fine`` (see ``_mint_token`` / ``create_access_token``),
    # so an inconsistent pair — e.g. fine ``client`` presented with coarse ``admin`` — can
    # only come from a tampered token or a future mint-path bug. Reject it as invalid
    # rather than trusting the elevated coarse claim.
    if coarse_role_from_fine(claims.role) != coarse.value:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid bearer token.",
        )
    return AuthContext(
        username=claims.username,
        role=coarse,
        persona=_persona_for(coarse),
        fine_role=claims.role,
        tenant_id=claims.tenant_id,
        user_id=claims.user_id,
    )


def require_admin(auth: AuthContext = Depends(require_auth)) -> AuthContext:
    """Require that the authenticated principal holds an admin role (either tier)."""
    if auth.role is not Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires the admin role.",
        )
    return auth


def require_roles(*roles: Role) -> Callable[[AuthContext], AuthContext]:
    """Build a dependency that admits any principal whose coarse role is in ``roles``.

    Use for endpoints open to several roles at once (e.g. ``ai_team`` *or* ``admin``).
    The single-role guards below are thin specialisations of this.
    """
    allowed = frozenset(roles)

    def _dep(auth: AuthContext = Depends(require_auth)) -> AuthContext:
        if auth.role not in allowed:
            names = ", ".join(sorted(r.value for r in allowed))
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires one of the roles: {names}.",
            )
        return auth

    return _dep


def require_devops(auth: AuthContext = Depends(require_auth)) -> AuthContext:
    """Require the devops (platform/operations) role."""
    if auth.role is not Role.DEVOPS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires the devops role.",
        )
    return auth


def require_ai_team(auth: AuthContext = Depends(require_auth)) -> AuthContext:
    """Require the ai_team (AI/ML engineering) role."""
    if auth.role is not Role.AI_TEAM:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires the ai_team role.",
        )
    return auth


def require_client(auth: AuthContext = Depends(require_auth)) -> AuthContext:
    """Require the client (business/end-user) role."""
    if auth.role is not Role.CLIENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires the client role.",
        )
    return auth


def require_platform_admin(auth: AuthContext = Depends(require_auth)) -> AuthContext:
    """Require the platform-admin tier (global operator, all tenants; §3.3)."""
    if auth.fine_role != PLATFORM_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires the platform-admin role.",
        )
    return auth


def require_tenant_admin(auth: AuthContext = Depends(require_auth)) -> AuthContext:
    """Require a tenant-admin (or platform-admin) tier; scopes to the caller's tenant."""
    if auth.fine_role not in (PLATFORM_ADMIN, TENANT_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires a tenant-admin role.",
        )
    return auth


# Multi-role dependency singletons (built once at import so they are immutable
# defaults, not per-signature calls — the B008-safe idiom for ``require_roles``).
require_admin_or_devops = require_roles(Role.ADMIN, Role.DEVOPS)
"""Admit the operator (``admin``) or the platform/ops (``devops``) role."""
require_admin_or_client = require_roles(Role.ADMIN, Role.CLIENT)
"""Admit the operator (``admin``) or the business/end-user (``client``) role."""
require_admin_or_ai_team = require_roles(Role.ADMIN, Role.AI_TEAM)
"""Admit the operator (``admin``) or the AI/ML engineering (``ai_team``) role.

The AI team owns the self-improvement loop, so it may drive the Improvement-loop ops
endpoints (diagnose / release / rollback / pending-releases). The human approval gate
(deciding a staged release) stays admin-only."""


def _scope_tenant(auth: AuthContext, requested: int | None) -> int | None:
    """Resolve the tenant a request may read, enforcing cross-tenant isolation.

    A platform-admin may target any tenant (``None`` == all). A tenant-admin is
    pinned to their own tenant: a request for a *different* tenant is forbidden, and
    an omitted ``tenant_id`` defaults to their own — the app-level scoping layer that
    backs Postgres RLS (§3.3).
    """
    if auth.fine_role == PLATFORM_ADMIN:
        return requested
    if requested is not None and requested != auth.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-tenant access is not permitted.",
        )
    return auth.tenant_id


async def _resolve_governance(auth: AuthContext) -> GovernanceContext:
    """Build the per-request governance context (tenant/user + effective caps).

    An unscoped principal (no tenant — the demo/platform operators) yields an empty
    context, so the LiteLLM chokepoint enforces nothing and the ledger stays untouched.
    A tenant-bound principal gets its nearest-binding caps (user clamped to tenant).
    """
    if auth.tenant_id is None:
        return GovernanceContext(role=auth.role)
    limits = await effective_limits(auth.tenant_id, auth.user_id)
    return GovernanceContext(
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
        role=auth.role,
        limits=limits,
    )


def _resolve_persona(requested: str | None, auth: AuthContext) -> str:
    """Resolve the effective persona for a query, enforcing role scope.

    Args:
        requested: The persona id from the request body, if any.
        auth: The authenticated principal.

    Returns:
        The effective persona id.

    Raises:
        HTTPException: 400 if the persona is unknown; 403 if a ``client`` requests
            an operator-scoped persona (the operational roles may use it).
    """
    persona_id = requested or auth.persona or DEFAULT_PERSONA_ID
    try:
        persona = get_persona(persona_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown persona {persona_id!r}.",
        ) from exc
    # Only the self-scoped ``client`` role is barred from an operator-scoped persona;
    # the operational roles (admin / ai_team / devops) may drive the full persona.
    if auth.role is Role.CLIENT and persona.role is Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Persona {persona_id!r} is not permitted for your role.",
        )
    return persona_id


# ─────────────────────────────────────────────────────────────────────────────
# Process-wide dashboard state (graph accumulator + efficiency metrics)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class GraphStore:
    """Accumulates retrieval graph slices, **scoped per persona** for ``GET /graph``.

    Scoping is a security control (``security.md`` §5 / ASI03): a ``client`` must
    not see the graph a different persona's runs retrieved.
    """

    _nodes: dict[str, dict[str, GraphNode]] = field(default_factory=dict)
    _edges: dict[str, dict[tuple[str, str, str], GraphEdge]] = field(
        default_factory=dict
    )

    def merge(
        self,
        persona: str,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> None:
        """Merge a retrieval delta into ``persona``'s graph (dedup by id)."""
        node_bucket = self._nodes.setdefault(persona, {})
        edge_bucket = self._edges.setdefault(persona, {})
        for raw in nodes:
            node = GraphNode.model_validate(raw)
            node_bucket[node.id] = node
        for raw in edges:
            edge = GraphEdge.model_validate(raw)
            edge_bucket[(edge.source, edge.target, edge.relation)] = edge

    def response(self, persona: str) -> GraphResponse:
        """Return ``persona``'s accumulated graph as a :class:`GraphResponse`."""
        return GraphResponse(
            nodes=list(self._nodes.get(persona, {}).values()),
            edges=list(self._edges.get(persona, {}).values()),
        )


@dataclass
class MetricsStore:
    """Live efficiency figures for the token/cost dashboard.

    Cache-hit rate and cost are measured from real runs. ``small_model_share`` is
    derived from the configured heterogeneous routing table (the cost-story
    decision in ``docs/architecture/backend.md`` §2): the share of roles routed to a small
    model.

    ``quality_score`` is a **measured grounding proxy**, not an LLM judge: the
    fraction of finished runs that both completed cleanly (status ``COMPLETED``,
    i.e. not blocked/errored) **and** retrieved backing context (touched at least
    one graph node before answering). It is deterministic and test-friendly; a run
    that answered from nothing, or that a guardrail blocked, scores 0.
    """

    queries: int = 0
    cache_hits: int = 0
    total_cost_usd: float = 0.0
    quality_runs: int = 0
    quality_sum: float = 0.0
    _grounded_runs: set[str] = field(default_factory=set)

    def note_grounding(self, run_id: str) -> None:
        """Mark ``run_id`` as grounded (it retrieved context / touched graph nodes)."""
        self._grounded_runs.add(run_id)

    def record_run(
        self, *, run_id: str, cache_hit: bool, cost_usd: float, status: RunStatus
    ) -> None:
        """Fold one finished run into the running totals and quality proxy."""
        self.queries += 1
        self.cache_hits += int(cache_hit)
        self.total_cost_usd += cost_usd
        grounded = run_id in self._grounded_runs
        self._grounded_runs.discard(run_id)
        self.quality_runs += 1
        self.quality_sum += float(status is RunStatus.COMPLETED and grounded)

    def snapshot(self) -> MetricsResponse:
        """Return the current dashboard figures.

        ``small_model_share`` is the **measured** fraction of real chat calls
        routed to a small model (from the gateway tally); before any call it
        falls back to the config-derived share of the routing table.
        ``quality_score`` is the running grounding proxy (``None`` before any run).

        ``total_calls`` (the gateway tally's process-wide chat-completion count) and
        ``p95_latency_ms`` (the per-process latency window's run p95) are the honest
        real sources for the Overview's throughput + latency tiles — cheap, sync and
        side-effect-free. ``p95_latency_ms`` is ``None`` before any run is recorded
        (never a fabricated zero). ``actions_approved`` is left at ``0`` here (it
        needs an async store read) and is populated by the ``/metrics`` handler.
        """
        # Lazy import mirrors the ``/latency`` route: the summary reads the in-process
        # rolling window, so it stays cheap and never touches the network.
        from aegis.observability import latency_summary

        routing = routing_table()
        tally = usage_tally()
        measured_share = tally["small_model_share"]
        return MetricsResponse(
            cache_hit_rate=(self.cache_hits / self.queries) if self.queries else 0.0,
            small_model_share=(
                measured_share
                if measured_share is not None
                else _small_model_share(routing)
            ),
            cost_per_1k_queries_usd=(
                (self.total_cost_usd / self.queries) * 1000 if self.queries else 0.0
            ),
            quality_score=(
                (self.quality_sum / self.quality_runs) if self.quality_runs else None
            ),
            routing=routing,
            cost_saved_usd=tally["cost_saved_usd"],
            baseline_cost_usd=tally["baseline_cost_usd"],
            total_calls=int(tally["total_calls"]),
            p95_latency_ms=latency_summary().run_p95_ms,
        )


def _small_model_share(routing: dict[str, str]) -> float:
    """Return the fraction of routed roles served by a small model."""
    considered = {
        role: model for role, model in routing.items() if role != "embedding"
    }
    if not considered:
        return 0.0
    small = sum(is_small_model(model) for model in considered.values())
    return small / len(considered)


_graph_store = GraphStore()
_metrics_store = MetricsStore()


def get_graph_store() -> GraphStore:
    """Return the process-wide graph accumulator (overridable in tests)."""
    return _graph_store


def get_metrics_store() -> MetricsStore:
    """Return the process-wide metrics store (overridable in tests)."""
    return _metrics_store


# ─────────────────────────────────────────────────────────────────────────────
# Injectable capabilities (overridable via ``app.dependency_overrides`` in tests)
# ─────────────────────────────────────────────────────────────────────────────


def get_agent_deps() -> AgentDeps:
    """Return the agent's live capability bundle."""
    return AgentDeps.default()


def get_ml_predict() -> Callable[[dict[str, Any]], MLExplainResponse]:
    """Return the ML predict-and-explain callable."""
    from app.ml import predict_explain

    return predict_explain


async def _safe_audit(
    action: str,
    actor: str | None,
    *,
    payload: dict[str, Any],
    model: str | None = None,
    trace_id: str | None = None,
    approved_by: str | None = None,
) -> None:
    """Write an audit row, never letting a logging failure break the request."""
    try:
        await record_audit(
            action=action,
            actor=actor,
            model=model,
            trace_id=trace_id,
            payload=payload,
            approved_by=approved_by,
        )
    except Exception:  # noqa: BLE001 - audit is best-effort at the edge
        logger.warning("Audit write failed for action %s", action, exc_info=True)


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/auth/login", response_model=LoginResponse, tags=["auth"])
async def login(req: LoginRequest) -> LoginResponse:
    """Authenticate a user (hashed password) and issue a claims-bearing JWT."""
    ctx = await _authenticate(req.username, req.password)
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials."
        )
    token = _mint_token(ctx)
    await _safe_audit(
        "auth.login",
        ctx.username,
        payload={"role": ctx.fine_role, "tenant_id": ctx.tenant_id},
    )
    return LoginResponse(role=ctx.role, token=token, tenant_id=ctx.tenant_id)


# ─────────────────────────────────────────────────────────────────────────────
# Platform identity — the honest Aegis capabilities manifest (§1)
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/platform/capabilities",
    response_model=CapabilitiesResponse,
    tags=["platform"],
)
async def platform_capabilities() -> CapabilitiesResponse:
    """Return the Aegis capabilities manifest — the honest "what Aegis is" surface.

    Every branded Aegis module is listed with the real tech underneath (branding,
    never hiding), its honest one-line summary, the actual implementing
    ``module_path`` and a live/optional status. Sourced verbatim from
    :data:`app.capabilities.AEGIS_MODULES` — the single source of truth also read by
    the docs and the frontend Platform view.

    **Unauthenticated by design.** The public landing page at ``/`` renders this
    manifest, so it must answer without a bearer token. The body is product
    identity — module names, the tech underneath, one-line summaries and import
    paths — the same material already published in ``README.md``. It carries no
    tenant, user, usage or credential data, which is the same reasoning that makes
    ``GET /about`` public.
    """
    return CapabilitiesResponse(
        product=PRODUCT_NAME,
        tagline=PRODUCT_TAGLINE,
        module_count=len(AEGIS_MODULES),
        modules=[AegisModuleRow(**m.model_dump()) for m in AEGIS_MODULES],
    )


@router.get("/health", tags=["platform"])
async def health() -> dict[str, str]:
    """Public liveness probe — the frontend boot probe and load balancers hit this.

    Unauthenticated by design (no user, no tenant, no DB touch) so it answers even
    when auth or the database is unavailable.
    """
    return {"status": "ok", "product": PRODUCT_NAME, "version": PRODUCT_VERSION}


@router.get("/about", response_model=AboutResponse, tags=["platform"])
async def about() -> AboutResponse:
    """Return a trivial, public product-identity card (name, version, module count)."""
    return AboutResponse(
        product=PRODUCT_NAME,
        version=PRODUCT_VERSION,
        tagline=PRODUCT_TAGLINE,
        modules=len(AEGIS_MODULES),
    )


@router.get("/stream/guardrail-demo", tags=["platform"])
async def guardrail_demo(q: str) -> StreamingResponse:
    """Demonstrator: stream a guardrail check as a real AG-UI SSE stream.

    Proves the wire format end to end — RUN_STARTED → STEP_STARTED →
    CUSTOM(guardrail_verdict) → STEP_FINISHED → RUN_FINISHED — by running a real
    :class:`~aegis.guardrails.Guardrails` input check through an
    :class:`~aegis.core.stream.AegisEmitter` and forwarding each encoded SSE frame
    to the client as it is produced. Unauthenticated by design, matching the
    public ``/health`` convention: it touches no tenant data and exists purely to
    demonstrate the streaming spine.

    Args:
        q: The text to run the guardrail input check against.

    Returns:
        A ``text/event-stream`` response of AG-UI SSE frames.
    """
    import asyncio
    import uuid

    from aegis.core.stream import AegisEmitter
    from aegis.guardrails import Guardrails

    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def sink(frame: str) -> None:
        """Push one encoded AG-UI SSE frame onto the queue for the generator to pull."""
        await queue.put(frame)

    async def run() -> None:
        """Run the bracketed guardrail check, then signal completion to the sink."""
        em = AegisEmitter(thread_id=uuid.uuid4().hex, run_id=uuid.uuid4().hex, sink=sink)
        try:
            await em.run_started()
            await Guardrails().stream_check_input_agui(q, em)
            await em.run_finished()
        finally:
            await queue.put(None)

    async def body() -> AsyncIterator[str]:
        """Yield queued SSE frames until the run task signals completion."""
        task = asyncio.create_task(run())
        try:
            while True:
                frame = await queue.get()
                if frame is None:
                    break
                yield frame
        finally:
            await task

    return StreamingResponse(body(), media_type="text/event-stream")


# ─────────────────────────────────────────────────────────────────────────────
# Platform operations — live stack, patch freshness, agent risk-map, savings
# (Wave-2 portal surfaces; see docs/security/owasp-agentic.md for the risk map)
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/stack", response_model=StackResponse, tags=["platform"])
async def stack(
    auth: AuthContext = Depends(require_admin_or_devops),
) -> StackResponse:
    """Return the live software bill-of-materials (admin/devops — the DevOps portal).

    Backend versions are resolved from the **actually installed** distributions via
    ``importlib.metadata`` (null when an optional-group dependency isn't installed —
    honest, not guessed); the small frontend set is parsed from ``web/package.json``
    at request time. Each row maps to the branded Aegis module it powers.
    """
    return build_stack()


@router.post("/stack/patch-check", response_model=PatchCheckResponse, tags=["platform"])
async def stack_patch_check(
    req: PatchCheckRequest | None = None,
    auth: AuthContext = Depends(require_admin_or_devops),
) -> PatchCheckResponse:
    """Compare installed vs latest against the live PyPI registry (admin/devops).

    Installed versions come from ``importlib.metadata``; latest comes from a live PyPI
    JSON query (short timeout, best-effort). Each package is resolved independently: a
    package is only ever ``current`` after a real registry answer, while one package's
    network failure marks only that row ``unknown`` (never discarding resolved
    neighbours). ``online`` is ``True`` when at least one package got a real answer; only
    when *no* package is reachable does the check degrade to ``online=False`` (or the
    cached last-successful set), never a fabricated clean bill of health.
    """
    packages = req.packages if req is not None else None
    return patch_check(packages)


@router.get("/risk-map", response_model=RiskMapResponse, tags=["platform"])
async def risk_map(
    auth: AuthContext = Depends(require_admin_or_client),
) -> RiskMapResponse:
    """Return the agent-risk heat-map (admin/client — the assurance surface).

    OWASP-Top-10-for-Agentic-aligned, grounded verbatim in
    ``docs/security/owasp-agentic.md``: each risk carries an honest 1..5
    likelihood/impact, its real Aegis mitigation, and a ``control_ref`` pointing at a
    real file. Injection is never marked fully resolved — defense-in-depth, not
    prevention.
    """
    return build_risk_map()


@router.get("/savings", response_model=SavingsResponse, tags=["platform"])
async def savings(
    auth: AuthContext = Depends(require_auth),
) -> SavingsResponse:
    """Return the baseline-vs-actual savings roll-up (any authenticated principal).

    ``require_auth`` because the **Savings** figure appears on the Overview surface in
    every role's portal. Derived from the real gateway usage ledger
    (:func:`app.core.llm.usage_tally`): ``saved_usd = baseline − actual`` is the measured
    small-model-routing win; cache savings bypass the ledger and are reported honestly
    at $0 in this figure (measured as cache-hit rate elsewhere), so the headline is
    conservative rather than falsely precise.
    """
    return build_savings()


@router.post("/query", tags=["agent"])
async def query(
    req: QueryRequest,
    auth: AuthContext = Depends(require_auth),
    deps: AgentDeps = Depends(get_agent_deps),
    graph_store: GraphStore = Depends(get_graph_store),
    metrics: MetricsStore = Depends(get_metrics_store),
) -> EventSourceResponse:
    """Run a query and stream the agent's step events over SSE."""
    persona = _resolve_persona(req.persona, auth)
    # Resolve the adapter-scoped memory subject (app-level isolation key). ``None`` — or
    # a request with no ``session_id`` — keeps memory inert and the stream unchanged.
    from app.adapter.memory_spec import memory_subject_for

    memory_subject = memory_subject_for(auth.user_id, persona)
    await _safe_audit(
        "query.start",
        auth.username,
        payload={"persona": persona, "query_chars": len(req.query)},
    )
    # Resolve the caller's tenant/user + effective caps once, then bind the governance
    # context inside the streaming task so the LiteLLM chokepoint (core.llm.complete)
    # can enforce budgets/rates and write the usage ledger for this run (§3.3).
    governance = await _resolve_governance(auth)

    async def event_source() -> AsyncIterator[ServerSentEvent]:
        token = set_governance_context(governance)
        try:
            async for event in run_agent(
                req.query,
                persona=persona,
                role=auth.role.value,
                deps=deps,
                registry=get_approval_registry(),
                session_id=req.session_id,
                memory_subject=memory_subject,
            ):
                _update_dashboards(event, graph_store, metrics, persona)
                yield ServerSentEvent(event=event.type, data=event.model_dump_json())
        finally:
            reset_governance_context(token)

    return EventSourceResponse(event_source())


@router.get("/graph", response_model=GraphResponse, tags=["graph"])
async def graph(
    auth: AuthContext = Depends(require_auth),
    graph_store: GraphStore = Depends(get_graph_store),
) -> GraphResponse:
    """Return the knowledge graph for the visualisation: **Neo4j ∪ this process's delta**.

    Two sources, unioned, because they answer different questions:

    * **Neo4j** (the durable base) — the whole knowledge graph LightRAG's
      entity/relationship extractor has written. Everything the platform knows, it
      survives restarts, and it is the source of truth.
    * **The per-persona in-process slice** (:class:`GraphStore`) — the graph deltas the
      *current* runs emitted. This is what makes the visualisation move live during a
      query, and it is the only graph at all in databaseless ``STORES=off`` mode.

    Serving only Neo4j would drop the live delta (nodes a run just retrieved would not
    appear until they were ingested); serving only the slice would throw away the durable
    graph and reset on every restart. The union keeps both, deduplicated by node id and
    by ``(source, target, relation)``, with Neo4j's copy winning any conflict.

    The persona scoping on the in-process slice is preserved — it is a security control
    (a ``client`` must not see what an operations persona retrieved).
    """
    persona = _resolve_persona(None, auth)
    try:
        from app.retrieval import knowledge_graph  # noqa: PLC0415

        durable = await knowledge_graph()
    except Exception:  # noqa: BLE001 - the viz must never 500 on a store blip
        logger.warning("Neo4j knowledge-graph read failed; using the local slice.", exc_info=True)
        durable = None

    local = graph_store.response(persona)
    if durable is None:
        return local

    nodes, edges = durable
    by_id = {n.id: n for n in nodes}
    for node in local.nodes:  # the live delta fills in what Neo4j has not ingested yet
        by_id.setdefault(node.id, node)
    by_key = {(e.source, e.target, e.relation): e for e in edges}
    for edge in local.edges:
        by_key.setdefault((edge.source, edge.target, edge.relation), edge)
    return GraphResponse(nodes=list(by_id.values()), edges=list(by_key.values()))


@router.post("/ml/explain", response_model=MLExplainResponse, tags=["ml"])
async def ml_explain(
    req: MLExplainRequest,
    auth: AuthContext = Depends(require_auth),
    predict: Callable[[dict[str, Any]], MLExplainResponse] = Depends(get_ml_predict),
) -> MLExplainResponse:
    """Return a conformalised, SHAP-explained prediction for the given features."""
    await _safe_audit(
        "ml.explain", auth.username, payload={"features": sorted(req.features)}
    )
    return predict(req.features)


@router.get("/metrics", response_model=MetricsResponse, tags=["metrics"])
async def metrics(
    auth: AuthContext = Depends(require_auth),
    metrics_store: MetricsStore = Depends(get_metrics_store),
) -> MetricsResponse:
    """Return live efficiency figures — the value-spine of the Overview surface.

    RBAC relaxed from ``require_platform_admin`` to ``require_auth`` (Wave-2 portal
    reachability): the **Overview** surface is present in *every* role's portal
    (``admin``/``ai_team``/``devops``/``client`` — see ``web/src/lib/portal.ts``)
    and polls this endpoint via ``useMetrics``. Under the old platform-admin gate every
    non-admin portal 403'd on its landing page. These are **aggregate efficiency
    figures** (cache-hit rate, small-model share, cost-per-1k, measured savings) — not
    per-tenant spend, tenant listings or budget mutation, which stay admin-gated. Any
    authenticated principal may read the platform's own efficiency posture.

    ``actions_approved`` (cleared human gates) is folded in here — it needs an async
    store read, so it lives at the handler rather than in the sync snapshot. The read
    is a single ``COUNT`` and degrades to the honest ``0`` when the store is
    unavailable, never fabricating a figure.
    """
    snapshot = metrics_store.snapshot()
    try:
        snapshot.actions_approved = await count_approved()
    except Exception:  # noqa: BLE001 - the store is optional; degrade to an honest 0
        logger.debug("actions_approved count failed — honest 0.", exc_info=True)
    return snapshot


@router.get(
    "/platform/public-metrics",
    response_model=PublicMetricsResponse,
    tags=["platform"],
)
async def platform_public_metrics(
    metrics_store: MetricsStore = Depends(get_metrics_store),
) -> PublicMetricsResponse:
    """Return the pre-login efficiency figures for the public landing page.

    **Unauthenticated by design**, and therefore a deliberately narrow projection of
    :func:`metrics`: ratios and counts only. The absolute money figures, the
    effective routing map and everything per-tenant stay behind ``require_auth`` —
    see :class:`PublicMetricsResponse` for the reasoning and
    ``tests/api/test_public_surfaces.py`` for the test that keeps this surface from
    silently widening.

    ``actions_approved`` needs an async store read and degrades to an honest ``0``
    when the store is unavailable, exactly as the authenticated handler does. No
    field is ever fabricated: the landing page renders "not yet measured" for a
    null ``p95_latency_ms`` rather than inventing a number.
    """
    snapshot = metrics_store.snapshot()
    try:
        approved = await count_approved()
    except Exception:  # noqa: BLE001 - the store is optional; degrade to an honest 0
        logger.debug("public actions_approved count failed — honest 0.", exc_info=True)
        approved = 0
    return PublicMetricsResponse(
        cache_hit_rate=snapshot.cache_hit_rate,
        small_model_share=snapshot.small_model_share,
        total_calls=snapshot.total_calls,
        actions_approved=approved,
        p95_latency_ms=snapshot.p95_latency_ms,
    )


# Upper bound on how many audit rows one /audit call may return.
_AUDIT_LIMIT_MAX = 200


@router.get("/audit", response_model=AuditLogResponse, tags=["audit"])
async def audit(
    limit: int = 50,
    auth: AuthContext = Depends(require_admin_or_devops),
) -> AuditLogResponse:
    """Return the most recent audit-log rows, newest first (admin/devops, read-only).

    DevOps legitimately needs the audit trail (the DevOps portal's Audit tab), so this
    read is open to admin *or* devops. Tenant-scoped (H2): a platform-admin sees the
    whole trail; a tenant-admin (and a tenant-scoped devops) sees only rows attributed
    to their own tenant. ``limit`` is clamped to ``[1, 200]`` so a caller cannot request
    an unbounded scan of the trail.
    """
    capped = max(1, min(limit, _AUDIT_LIMIT_MAX))
    scoped = _scope_tenant(auth, None)
    rows = await list_recent_audit(capped, tenant_id=scoped)
    return AuditLogResponse(rows=rows)


# Upper bound on how many approval rows one /approvals call may return.
_APPROVALS_LIMIT_MAX = 200


@router.get("/approvals", response_model=ApprovalInboxResponse, tags=["agent"])
async def approvals_inbox(
    status: str = "pending",
    limit: int = 50,
    auth: AuthContext = Depends(require_admin),
) -> ApprovalInboxResponse:
    """Return the durable approvals inbox (admin only, read-only; §1.3).

    Tenant-scoped (C1): a platform-admin sees every tenant's pending gates; a
    tenant-admin sees only its own tenant's. Only ``status=pending`` is served (the
    actionable queue); ``limit`` is clamped to ``[1, 200]``. Rows come back
    soonest-SLA-deadline first.
    """
    capped = max(1, min(limit, _APPROVALS_LIMIT_MAX))
    scoped = _scope_tenant(auth, None)
    rows = await list_pending(tenant_id=scoped, limit=capped) if status == "pending" else []
    return ApprovalInboxResponse(rows=rows)


async def _enforce_approval_tenant(approval_id: str, auth: AuthContext) -> None:
    """Forbid deciding on another tenant's approval (C1; platform-admin exempt).

    Loads the durable row and — for a tenant-scoped caller — requires the approval's
    ``tenant_id`` to equal the caller's. A platform-admin may resolve any tenant's
    gate. An unknown ``approval_id`` is left to the idempotent decision path (which
    returns ``accepted=False``) so replay/no-op semantics are preserved.

    Raises:
        HTTPException: 403 when a tenant-admin targets an approval owned by another
            tenant.
    """
    if auth.fine_role == PLATFORM_ADMIN:
        return
    row = await get_approval(approval_id)
    if row is not None and row.tenant_id != auth.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-tenant approval access is not permitted.",
        )


@router.post(
    "/approvals/{approval_id}/decision",
    response_model=ApprovalDecisionResponse,
    tags=["agent"],
)
async def approvals_decision(
    approval_id: str,
    req: ApprovalDecisionRequest,
    auth: AuthContext = Depends(require_admin),
) -> ApprovalDecisionResponse:
    """Resolve a durable approval out-of-band and resume its run. Admin only.

    Idempotent: the optimistic ``PENDING → RESUMING/REJECTED`` transition means a
    replayed decision returns ``accepted=False`` and never double-resumes. A
    tenant-admin may only decide on its own tenant's gates (C1).
    """
    await _enforce_approval_tenant(approval_id, auth)
    result = await decide_approval(
        approval_id, req.decision, approver=auth.username
    )
    await _safe_audit(
        "approval.decision",
        auth.username,
        payload={
            "approval_id": approval_id,
            "decision": req.decision.value,
            "accepted": result.accepted,
            "status": result.status,
            "surface": "inbox",
        },
        approved_by=auth.username,
    )
    return result


@router.post("/approval", response_model=ApprovalResponse, tags=["agent"])
async def approval(
    req: ApprovalRequest, auth: AuthContext = Depends(require_admin)
) -> ApprovalResponse:
    """Resolve a paused, gated action (approve/reject). Admin only.

    The live in-run gate (the money-shot demo) and the async inbox share one resolve
    path via :func:`app.agent.decide_approval`, so a decision here wakes an open
    ``/query`` socket instantly while still landing durably. A tenant-admin may only
    resolve its own tenant's gates (C1).
    """
    await _enforce_approval_tenant(req.approval_id, auth)
    result = await decide_approval(
        req.approval_id, req.decision, approver=auth.username
    )
    await _safe_audit(
        "approval.decision",
        auth.username,
        payload={
            "approval_id": req.approval_id,
            "decision": req.decision.value,
            "accepted": result.accepted,
            "status": result.status,
            "surface": "live",
        },
        approved_by=auth.username,
    )
    return ApprovalResponse(approval_id=req.approval_id, accepted=result.accepted)


# ─────────────────────────────────────────────────────────────────────────────
# Admin governance surfaces (§3.3) — tenants / users / budgets / usage
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/admin/tenants", response_model=AdminTenantsResponse, tags=["admin"])
async def admin_tenants(
    auth: AuthContext = Depends(require_platform_admin),
) -> AdminTenantsResponse:
    """List every tenant (platform-admin only)."""
    return AdminTenantsResponse(rows=await list_tenants())


@router.post("/admin/tenants", response_model=TenantRow, status_code=201, tags=["admin"])
async def admin_create_tenant(
    req: TenantCreateRequest,
    auth: AuthContext = Depends(require_platform_admin),
) -> TenantRow:
    """Create a new client/tenant (platform-admin only).

    Tenant names are unique — a clash returns 409 rather than a 500. The action is
    audited so the trail records who onboarded each client.
    """
    try:
        row = await create_tenant(req.name.strip())
    except DuplicateTenantError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await _safe_audit(
        "admin.tenant.create", auth.username, payload={"tenant_id": row.id, "name": row.name}
    )
    return row


@router.get("/admin/users", response_model=AdminUsersResponse, tags=["admin"])
async def admin_users(
    tenant_id: int | None = None,
    auth: AuthContext = Depends(require_tenant_admin),
) -> AdminUsersResponse:
    """List users, scoped to the caller's tenant (platform-admin may target any)."""
    scoped = _scope_tenant(auth, tenant_id)
    return AdminUsersResponse(rows=await list_users(scoped))


@router.post("/admin/users", response_model=AdminUserRow, status_code=201, tags=["admin"])
async def admin_create_user(
    req: AdminUserCreateRequest,
    auth: AuthContext = Depends(require_tenant_admin),
) -> AdminUserRow:
    """Provision a new user with a role + hashed password (admin action).

    A platform-admin may create a user in any tenant (or a platform user with
    ``tenant_id=null``); a tenant-admin is pinned to its own tenant — a create that
    targets another tenant (or the platform scope) is a clean 403. A duplicate
    username returns 409. The plaintext password is Argon2-hashed in the data layer
    and never stored or logged. The action is audited.
    """
    tenant_id = req.tenant_id
    if auth.fine_role == TENANT_ADMIN:
        # A tenant-admin can only create users inside its own tenant.
        if tenant_id is None:
            tenant_id = auth.tenant_id
        elif tenant_id != auth.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="A tenant-admin may only create users in its own tenant.",
            )
    try:
        row = await create_user(
            req.username.strip(),
            role=req.role,
            tenant_id=tenant_id,
            email=req.email,
            password=req.password,
        )
    except DuplicateUserError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await _safe_audit(
        "admin.user.create",
        auth.username,
        payload={
            "user_id": row.id,
            "username": row.username,
            "role": req.role.value,
            "tenant_id": tenant_id,
        },
    )
    return row


@router.post(
    "/admin/users/{user_id}/role", response_model=AdminUserRow, tags=["admin"]
)
async def admin_set_user_role(
    user_id: int,
    req: UserRoleUpdateRequest,
    auth: AuthContext = Depends(require_tenant_admin),
) -> AdminUserRow:
    """Reassign a user's coarse RBAC role (admin action; §3.3).

    A platform-admin may reassign any user; a tenant-admin is pinned to its own
    tenant (a cross-tenant target is forbidden). A last-platform-admin lockout is
    refused so the platform can never be left with no global operator.
    """
    scope = _scope_tenant(auth, None)  # None for platform-admin; own tenant otherwise.
    # A tenant-admin may only touch a user inside its own tenant. Resolve-and-check the
    # target's tenant first (mirrors the user-scoped budget guard) so a cross-tenant
    # attempt is a clean 403, never a silent write.
    if scope is not None:
        target_tenant = await user_tenant_id(user_id)
        if target_tenant is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Unknown user."
            )
        if target_tenant != scope:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="A tenant-admin may only reassign users in its own tenant.",
            )
    try:
        row = await update_user_role(user_id, req.role, tenant_scope=scope)
    except LastPlatformAdminError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown user."
        )
    await _safe_audit(
        "admin.user.role_set",
        auth.username,
        payload={"user_id": user_id, "role": req.role.value, "tenant_id": scope},
    )
    return row


@router.get("/admin/budgets", response_model=AdminBudgetsResponse, tags=["admin"])
async def admin_budgets_list(
    scope_type: str | None = None,
    scope_id: int | None = None,
    auth: AuthContext = Depends(require_tenant_admin),
) -> AdminBudgetsResponse:
    """List budget caps, tenant-scoped (M1), optionally filtered by scope type/id.

    A platform-admin sees every tenant's caps; a tenant-admin sees only caps owned
    by its own tenant (the ``budgets.tenant_id`` column backs this over RLS).
    """
    scoped = _scope_tenant(auth, None)
    return AdminBudgetsResponse(
        rows=await list_budgets(scope_type, scope_id, tenant_id=scoped)
    )


async def _resolve_budget_tenant(req: BudgetUpsertRequest, auth: AuthContext) -> int | None:
    """Resolve (and authorise) the tenant that owns the budget being upserted (H3).

    A tenant-admin may only write caps that belong to its own tenant. For a
    ``tenant``-scoped cap the owning tenant is ``scope_id`` itself; for a ``user``-
    scoped cap it is the target user's tenant, which must be resolved and checked so
    a tenant-admin cannot cap a user in another tenant. Platform-admins may set any.

    Returns:
        The owning tenant id to stamp on the budget row (``None`` only for a
        platform-admin acting outside any tenant).

    Raises:
        HTTPException: 403 on any cross-tenant attempt; 404 when a user-scoped cap
            names an unknown user.
    """
    if req.scope_type == "tenant":
        if auth.fine_role == TENANT_ADMIN and req.scope_id != auth.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="A tenant-admin may only set budgets for its own tenant.",
            )
        return req.scope_id
    if req.scope_type == "user":
        target_tenant = await user_tenant_id(req.scope_id)
        if target_tenant is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Unknown user for a user-scoped budget.",
            )
        if auth.fine_role == TENANT_ADMIN and target_tenant != auth.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="A tenant-admin may only set budgets for users in its own tenant.",
            )
        return target_tenant
    return auth.tenant_id


@router.post("/admin/budgets", response_model=BudgetRow, tags=["admin"])
async def admin_budgets_upsert(
    req: BudgetUpsertRequest,
    auth: AuthContext = Depends(require_tenant_admin),
) -> BudgetRow:
    """Create or update a spend/rate cap for a scope+window (idempotent; §3.3).

    A tenant-admin may only set a cap owned by its *own* tenant — both **tenant**-
    scoped caps (H3 pre-existing) and **user**-scoped caps, whose target user's
    tenant is resolved and checked. Platform-admins may set any.
    """
    owning_tenant = await _resolve_budget_tenant(req, auth)
    try:
        row = await upsert_budget(
            scope_type=req.scope_type,
            scope_id=req.scope_id,
            window=req.window,
            token_cap=req.token_cap,
            usd_cap=req.usd_cap,
            rpm=req.rpm,
            tpm=req.tpm,
            tenant_id=owning_tenant,
        )
    except CrossTenantBudgetError as exc:
        # The data layer refuses to re-stamp a budget row owned by another tenant
        # (previously this silently took the row over). Surface it as an
        # authorization failure rather than letting it escape as a 500.
        await _safe_audit(
            "admin.budget.upsert.denied",
            auth.username,
            payload={"scope_type": req.scope_type, "scope_id": req.scope_id},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    await _safe_audit(
        "admin.budget.upsert",
        auth.username,
        payload={"scope_type": req.scope_type, "scope_id": req.scope_id},
    )
    return row


@router.get("/admin/usage", response_model=AdminUsageResponse, tags=["admin"])
async def admin_usage(
    tenant_id: int | None = None,
    window: str = "day",
    auth: AuthContext = Depends(require_tenant_admin),
) -> AdminUsageResponse:
    """Return the ledger-rolled usage for a tenant + window (tenant-scoped)."""
    scoped = _scope_tenant(auth, tenant_id)
    pt, ct, cost, by_model, series = await usage_rollup(scoped, window)
    return AdminUsageResponse(
        total_prompt_tokens=pt,
        total_completion_tokens=ct,
        total_cost_usd=cost,
        by_model=by_model,
        series=series,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Long-term memory read/admin surfaces (docs/architecture/memory-spec.md §D)
#
# Isolation is app-level (never RLS): every query filters ``subject_id`` (+ tenant).
# A caller may read their own subject; an admin may read any subject in their tenant.
# The DB/stores are optional — every handler degrades to an empty result (reads) or a
# clean 503 (erasure) when the store is unreachable, and never crashes the request.
# ─────────────────────────────────────────────────────────────────────────────

# Upper bound on how many memory rows one listing may return.
_MEMORY_LIMIT_MAX = 500


def _mem_iso(ts: object) -> str | None:
    """Render a (possibly naive) timestamp as an ISO 8601 UTC string, or ``None``."""
    from datetime import UTC, datetime

    if not isinstance(ts, datetime):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).isoformat()


def _own_subject(auth: AuthContext) -> str | None:
    """The memory subject this principal owns (its own ``user:<id>``), or ``None``."""
    from app.adapter.memory_spec import memory_subject_for

    return memory_subject_for(auth.user_id, auth.persona)


def _authorize_subject(auth: AuthContext, subject: str) -> int | None:
    """Authorise access to ``subject`` and return the tenant filter to apply.

    A plain user may only touch their own subject (``user:<id>``); an admin may touch
    any subject within their tenant scope (a tenant-admin is pinned to its own tenant, a
    platform-admin may reach any). The returned tenant id is ANDed into every query as
    the belt-and-suspenders isolator over app-level ``subject_id`` scoping.

    Raises:
        HTTPException: 403 when a non-admin targets a subject other than its own.
    """
    if auth.role is Role.ADMIN:
        return _scope_tenant(auth, None)
    own = _own_subject(auth)
    if own is None or subject != own:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may only access your own memory.",
        )
    return auth.tenant_id


@router.get("/memory/facts", response_model=MemoryFactsResponse, tags=["memory"])
async def memory_facts(
    subject: str,
    include_invalid: bool = False,
    auth: AuthContext = Depends(require_auth),
) -> MemoryFactsResponse:
    """Return the subject's semantic facts (valid, plus superseded when asked).

    Subject-scoped: a user reads only its own subject; an admin any subject in its
    tenant. Degrades to an empty list when the store is unavailable (lite/off mode).
    """
    tenant_id = _authorize_subject(auth, subject)
    try:
        from sqlalchemy import select

        from app.data.session import get_sessionmaker, set_tenant_scope
        from app.memory.stores import MemoryFact

        async with get_sessionmaker()() as session:
            await set_tenant_scope(session, tenant_id)
            stmt = select(MemoryFact).where(MemoryFact.subject_id == subject)
            if tenant_id is not None:
                stmt = stmt.where(MemoryFact.tenant_id == tenant_id)
            if not include_invalid:
                stmt = stmt.where(
                    MemoryFact.invalid_at.is_(None), MemoryFact.expired_at.is_(None)
                )
            stmt = stmt.order_by(MemoryFact.valid_at.desc(), MemoryFact.id.desc()).limit(
                _MEMORY_LIMIT_MAX
            )
            rows = (await session.execute(stmt)).scalars().all()
    except Exception:  # noqa: BLE001 - stores are optional; degrade to empty
        logger.debug("memory_facts read failed — degrading to empty.", exc_info=True)
        return MemoryFactsResponse(subject=subject, rows=[])
    return MemoryFactsResponse(
        subject=subject,
        rows=[
            MemoryFactRow(
                id=f.id,
                subject_id=f.subject_id,
                fact_type=f.fact_type,
                subject=f.subject,
                predicate=f.predicate,
                object=f.object,
                text=f.text,
                confidence=f.confidence,
                importance=f.importance,
                access_count=f.access_count,
                valid_at=_mem_iso(f.valid_at),
                invalid_at=_mem_iso(f.invalid_at),
                created_at=_mem_iso(f.created_at),
                expired_at=_mem_iso(f.expired_at),
                source_turn_ids=list(f.source_turn_ids or []),
                supersedes_id=f.supersedes_id,
                is_valid=f.invalid_at is None and f.expired_at is None,
            )
            for f in rows
        ],
    )


@router.get("/memory/profile", response_model=MemoryProfileResponse, tags=["memory"])
async def memory_profile(
    subject: str,
    auth: AuthContext = Depends(require_auth),
) -> MemoryProfileResponse:
    """Return the subject's structured profile ("human block") JSON, or an empty one."""
    tenant_id = _authorize_subject(auth, subject)
    try:
        from sqlalchemy import select

        from app.data.session import get_sessionmaker, set_tenant_scope
        from app.memory.stores import MemoryProfile

        async with get_sessionmaker()() as session:
            await set_tenant_scope(session, tenant_id)
            stmt = select(MemoryProfile).where(MemoryProfile.subject_id == subject)
            if tenant_id is not None:
                stmt = stmt.where(MemoryProfile.tenant_id == tenant_id)
            prof = (await session.execute(stmt)).scalars().first()
    except Exception:  # noqa: BLE001 - stores are optional; degrade to empty
        logger.debug("memory_profile read failed — degrading to empty.", exc_info=True)
        return MemoryProfileResponse(subject=subject, data={})
    if prof is None:
        return MemoryProfileResponse(subject=subject, data={})
    return MemoryProfileResponse(
        subject=subject, data=dict(prof.data or {}), updated_at=_mem_iso(prof.updated_at)
    )


@router.get("/memory/sessions", response_model=MemorySessionsResponse, tags=["memory"])
async def memory_sessions(
    subject: str,
    auth: AuthContext = Depends(require_auth),
) -> MemorySessionsResponse:
    """Return the subject's conversation threads (id, turn count, summary, last active)."""
    tenant_id = _authorize_subject(auth, subject)
    try:
        from sqlalchemy import select

        from app.data.session import get_sessionmaker, set_tenant_scope
        from app.memory.stores import MemorySession

        async with get_sessionmaker()() as session:
            await set_tenant_scope(session, tenant_id)
            stmt = select(MemorySession).where(MemorySession.subject_id == subject)
            if tenant_id is not None:
                stmt = stmt.where(MemorySession.tenant_id == tenant_id)
            stmt = stmt.order_by(MemorySession.last_active_at.desc()).limit(
                _MEMORY_LIMIT_MAX
            )
            rows = (await session.execute(stmt)).scalars().all()
    except Exception:  # noqa: BLE001 - stores are optional; degrade to empty
        logger.debug("memory_sessions read failed — degrading to empty.", exc_info=True)
        return MemorySessionsResponse(subject=subject, rows=[])
    return MemorySessionsResponse(
        subject=subject,
        rows=[
            MemorySessionRow(
                id=s.id,
                subject_id=s.subject_id,
                persona=s.persona,
                turn_count=s.turn_count,
                summary=s.summary,
                created_at=_mem_iso(s.created_at),
                last_active_at=_mem_iso(s.last_active_at),
            )
            for s in rows
        ],
    )


@router.get(
    "/memory/sessions/{session_id}/messages",
    response_model=MemoryMessagesResponse,
    tags=["memory"],
)
async def memory_session_messages(
    session_id: str,
    auth: AuthContext = Depends(require_auth),
) -> MemoryMessagesResponse:
    """Return one session's turns in order (subject-checked via the session's owner).

    The session's own ``subject_id`` is authorised — a user may only read a session it
    owns; an admin any session in its tenant — so no separate subject param is needed.
    """
    try:
        from sqlalchemy import select

        from app.data.session import get_sessionmaker, set_tenant_scope
        from app.memory.stores import MemoryMessage, MemorySession

        async with get_sessionmaker()() as session:
            sess = (
                await session.execute(
                    select(MemorySession).where(MemorySession.id == session_id)
                )
            ).scalars().first()
            if sess is None:
                # Nothing to leak; authorise the caller's own subject scope and 404.
                _authorize_subject(auth, _own_subject(auth) or "")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Unknown session."
                )
            tenant_id = _authorize_subject(auth, sess.subject_id)
            await set_tenant_scope(session, tenant_id)
            stmt = select(MemoryMessage).where(
                MemoryMessage.session_id == session_id,
                MemoryMessage.subject_id == sess.subject_id,
            )
            if tenant_id is not None:
                stmt = stmt.where(MemoryMessage.tenant_id == tenant_id)
            stmt = stmt.order_by(
                MemoryMessage.turn_index.asc(), MemoryMessage.id.asc()
            ).limit(_MEMORY_LIMIT_MAX)
            rows = (await session.execute(stmt)).scalars().all()
            subject = sess.subject_id
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - stores are optional; degrade to empty
        logger.debug("memory_session_messages read failed — empty.", exc_info=True)
        return MemoryMessagesResponse(session_id=session_id, subject="", rows=[])
    return MemoryMessagesResponse(
        session_id=session_id,
        subject=subject,
        rows=[
            MemoryMessageRow(
                id=m.id,
                session_id=m.session_id,
                turn_index=m.turn_index,
                role=m.role,
                origin=str(m.origin.value if hasattr(m.origin, "value") else m.origin),
                content=m.content,
                importance=m.importance,
                created_at=_mem_iso(m.created_at),
            )
            for m in rows
        ],
    )


@router.get("/memory/writes", response_model=MemoryWritesResponse, tags=["memory"])
async def memory_writes(
    subject: str,
    auth: AuthContext = Depends(require_auth),
) -> MemoryWritesResponse:
    """Return the subject's fact-write changelog (the "why the agent believes X" trail)."""
    tenant_id = _authorize_subject(auth, subject)
    try:
        from sqlalchemy import select

        from app.data.session import get_sessionmaker, set_tenant_scope
        from app.memory.stores import MemoryWriteLog

        async with get_sessionmaker()() as session:
            await set_tenant_scope(session, tenant_id)
            stmt = select(MemoryWriteLog).where(MemoryWriteLog.subject_id == subject)
            if tenant_id is not None:
                stmt = stmt.where(MemoryWriteLog.tenant_id == tenant_id)
            stmt = stmt.order_by(
                MemoryWriteLog.ts.desc(), MemoryWriteLog.id.desc()
            ).limit(_MEMORY_LIMIT_MAX)
            rows = (await session.execute(stmt)).scalars().all()
    except Exception:  # noqa: BLE001 - stores are optional; degrade to empty
        logger.debug("memory_writes read failed — degrading to empty.", exc_info=True)
        return MemoryWritesResponse(subject=subject, rows=[])
    return MemoryWritesResponse(
        subject=subject,
        rows=[
            MemoryWriteRow(
                id=w.id,
                op=str(w.op.value if hasattr(w.op, "value") else w.op),
                fact_id=w.fact_id,
                before=dict(w.before or {}),
                after=dict(w.after or {}),
                reason=w.reason,
                model=w.model,
                trace_id=w.trace_id,
                ts=_mem_iso(w.ts),
            )
            for w in rows
        ],
    )


@router.get("/memory/recall_debug", response_model=RecallDebugResponse, tags=["memory"])
async def memory_recall_debug(
    subject: str,
    query: str,
    auth: AuthContext = Depends(require_auth),
) -> RecallDebugResponse:
    """Run recall + working-memory assembly live and show what would be recalled.

    The glass-box view: ranked facts + episodic with their scores, the assembled
    working-memory block, and its token size — no per-run storage. Embeds the query when
    the gateway is reachable (real similarity), else falls back to recency-only recall.
    """
    tenant_id = _authorize_subject(auth, subject)
    try:
        from app.data.session import get_sessionmaker, set_tenant_scope
        from app.memory.config import MemoryConfig
        from app.memory.recall import recall
        from app.memory.working import assemble_working_memory

        config = MemoryConfig()
        query_vec: list[float] | None = None
        try:  # a real embedding makes recall similarity honest; optional at the edge
            from app.retrieval.gateway import default_embed

            vecs = await default_embed()([query])
            query_vec = list(vecs[0]) if vecs else None
        except Exception:  # noqa: BLE001 - no gateway → recency-only recall
            query_vec = None

        async with get_sessionmaker()() as session:
            await set_tenant_scope(session, tenant_id)
            # A recall_debug view is not bound to a single thread — use the subject's most
            # recent session so episodic/summary recall has a thread to read from.
            from sqlalchemy import select

            from app.memory.stores import MemorySession

            sess_stmt = select(MemorySession).where(
                MemorySession.subject_id == subject
            )
            if tenant_id is not None:
                sess_stmt = sess_stmt.where(MemorySession.tenant_id == tenant_id)
            sess_stmt = sess_stmt.order_by(MemorySession.last_active_at.desc())
            sess = (await session.execute(sess_stmt)).scalars().first()
            session_id = sess.id if sess is not None else ""

            bundle = await recall(
                session,
                subject_id=subject,
                session_id=session_id,
                persona=auth.persona,
                query=query,
                query_vec=query_vec,
                config=config,
                tenant_id=tenant_id,
            )
            assembled = await assemble_working_memory(
                session,
                subject_id=subject,
                session_id=session_id,
                persona=auth.persona,
                query=query,
                query_vec=query_vec,
                config=config,
                tenant_id=tenant_id,
            )
    except Exception:  # noqa: BLE001 - stores are optional; degrade to empty
        logger.debug("memory_recall_debug failed — degrading to empty.", exc_info=True)
        return RecallDebugResponse(subject=subject, query=query)

    from app.memory.scoring import RecallCandidate

    injected_facts = set(assembled.recalled_fact_ids)
    injected_msgs = set(assembled.recalled_message_ids)

    def _fact_item(c: RecallCandidate) -> RecallDebugItem:
        fid = getattr(c.payload, "id", None)
        return RecallDebugItem(
            key=c.key,
            text=c.text,
            score=c.relevance,
            importance=c.importance,
            age_days=c.age_days,
            injected=fid in injected_facts,
        )

    def _epi_item(c: RecallCandidate) -> RecallDebugItem:
        mid = getattr(c.payload, "id", None)
        return RecallDebugItem(
            key=c.key,
            text=c.text,
            score=c.relevance,
            importance=c.importance,
            age_days=c.age_days,
            injected=mid in injected_msgs,
        )

    return RecallDebugResponse(
        subject=subject,
        query=query,
        facts=[_fact_item(c) for c in bundle.facts],
        episodic=[_epi_item(c) for c in bundle.episodic],
        working_memory=assembled.text,
        tokens_used=assembled.tokens_used,
        recalled_fact_count=len(assembled.recalled_fact_ids),
        recalled_message_count=len(assembled.recalled_message_ids),
    )


@router.post("/memory/forget", response_model=MemoryForgetResponse, tags=["memory"])
async def memory_forget(
    subject: str,
    auth: AuthContext = Depends(require_auth),
) -> MemoryForgetResponse:
    """GDPR right-to-erasure: HARD-delete every memory row for ``subject`` (audited).

    This is the ONE place a hard delete is allowed (a compliance action, distinct from
    bitemporal invalidation). One audit row records the erasure and the row counts. A
    503 is returned when the store is unreachable — an erasure must never be faked.
    """
    tenant_id = _authorize_subject(auth, subject)
    try:
        from sqlalchemy import delete

        from app.data.session import get_sessionmaker, set_tenant_scope
        from app.memory.stores import (
            MemoryConsolidationJob,
            MemoryFact,
            MemoryMessage,
            MemoryProfile,
            MemorySession,
            MemoryWriteLog,
        )

        counts: dict[str, int] = {}
        async with get_sessionmaker()() as session:
            await set_tenant_scope(session, tenant_id)
            for key, model in (
                ("facts", MemoryFact),
                ("messages", MemoryMessage),
                ("sessions", MemorySession),
                ("profiles", MemoryProfile),
                ("writes", MemoryWriteLog),
                ("jobs", MemoryConsolidationJob),
            ):
                stmt = delete(model).where(model.subject_id == subject)
                if tenant_id is not None:
                    stmt = stmt.where(model.tenant_id == tenant_id)
                res = await session.execute(stmt)
                counts[key] = int(res.rowcount or 0)
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - erasure must be honest; never fake success
        logger.warning("memory_forget failed for %s", subject, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Memory store unavailable; erasure not performed.",
        ) from exc

    await _safe_audit(
        "memory.forget",
        auth.username,
        payload={"subject": subject, "deleted": counts, "scope": "subject"},
    )
    return MemoryForgetResponse(
        subject=subject,
        deleted_facts=counts["facts"],
        deleted_messages=counts["messages"],
        deleted_sessions=counts["sessions"],
        deleted_profiles=counts["profiles"],
        deleted_writes=counts["writes"],
        deleted_jobs=counts["jobs"],
    )


@router.delete(
    "/memory/facts/{fact_id}", response_model=MemoryFactDeleteResponse, tags=["memory"]
)
async def memory_delete_fact(
    fact_id: int,
    auth: AuthContext = Depends(require_auth),
) -> MemoryFactDeleteResponse:
    """GDPR right-to-erasure of a single fact: HARD-delete the row (audited).

    The fact's own ``subject_id`` is authorised before deletion (a user may only erase
    its own facts; an admin any fact in its tenant). A 503 is returned when the store is
    unreachable — an erasure must never be faked.
    """
    try:
        from sqlalchemy import delete, select

        from app.data.session import get_sessionmaker, set_tenant_scope
        from app.memory.stores import MemoryFact

        async with get_sessionmaker()() as session:
            fact = (
                await session.execute(
                    select(MemoryFact).where(MemoryFact.id == fact_id)
                )
            ).scalars().first()
            if fact is None:
                _authorize_subject(auth, _own_subject(auth) or "")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Unknown fact."
                )
            tenant_id = _authorize_subject(auth, fact.subject_id)
            await set_tenant_scope(session, tenant_id)
            subject = fact.subject_id
            stmt = delete(MemoryFact).where(MemoryFact.id == fact_id)
            if tenant_id is not None:
                stmt = stmt.where(MemoryFact.tenant_id == tenant_id)
            res = await session.execute(stmt)
            deleted = int(res.rowcount or 0) > 0
            await session.commit()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - erasure must be honest; never fake success
        logger.warning("memory_delete_fact failed for %s", fact_id, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Memory store unavailable; erasure not performed.",
        ) from exc

    await _safe_audit(
        "memory.forget",
        auth.username,
        payload={"subject": subject, "fact_id": fact_id, "scope": "fact"},
    )
    return MemoryFactDeleteResponse(fact_id=fact_id, deleted=deleted)


# ─────────────────────────────────────────────────────────────────────────────
# LLM-Ops closed loop (`/ops/*`) — prompt registry, trace-eval trend, and the
# diagnose → release → rollback control surface (docs/learn/40-pipelines.md).
#
# Reads require auth; every mutation (diagnose/release/rollback/decide) is admin-only
# and audited. All handlers degrade cleanly when the real stores are off: reads return
# empty, mutations return 503 — writing to the registry/eval tables needs the DB.
# ─────────────────────────────────────────────────────────────────────────────

# Upper bound on how many eval rows one /ops/evals call may return.
_OPS_EVALS_LIMIT_MAX = 500


def _stores_on() -> bool:
    """Return whether the real databases are enabled (the LLM-Ops loop needs them)."""
    return get_settings().stores_enabled


def _require_stores() -> None:
    """Raise a clean 503 when the stores are off (mutations cannot be faked)."""
    if not _stores_on():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The stores are disabled; the LLM-Ops registry is unavailable.",
        )


def _iso_ts(ts: object) -> str | None:
    """Render a (possibly naive) timestamp as an ISO 8601 UTC string, or ``None``."""
    from datetime import UTC, datetime

    if not isinstance(ts, datetime):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).isoformat()


@router.get("/ops/prompts", response_model=OpsPromptsResponse, tags=["ops"])
async def ops_prompts(
    prompt_key: str,
    auth: AuthContext = Depends(require_auth),
) -> OpsPromptsResponse:
    """List every versioned system prompt for ``prompt_key``, newest version first.

    Degrades to an empty list when the stores are off (lite/offline mode).
    """
    if not _stores_on():
        return OpsPromptsResponse(prompt_key=prompt_key, rows=[])
    from app.data.session import get_sessionmaker
    from app.ops import registry

    try:
        async with get_sessionmaker()() as session:
            versions = await registry.list_versions(session, prompt_key)
    except Exception:  # noqa: BLE001 - stores are optional; degrade to empty
        logger.debug("ops_prompts read failed — degrading to empty.", exc_info=True)
        return OpsPromptsResponse(prompt_key=prompt_key, rows=[])
    return OpsPromptsResponse(
        prompt_key=prompt_key,
        rows=[
            OpsPromptVersionRow(
                id=pv.id,
                prompt_key=pv.prompt_key,
                version=pv.version,
                status=pv.status.value if hasattr(pv.status, "value") else str(pv.status),
                created_by=pv.created_by,
                notes=pv.notes,
                created_at=_iso_ts(pv.created_at),
            )
            for pv in versions
        ],
    )


@router.get("/ops/prompts/active", response_model=OpsActivePromptResponse, tags=["ops"])
async def ops_prompts_active(
    prompt_key: str,
    auth: AuthContext = Depends(require_auth),
) -> OpsActivePromptResponse:
    """Return the single live version for ``prompt_key`` (DB), else the cached one.

    Falls back to the process-wide active cache (``registry.get_cached_active``) when the
    DB has no active row or is unreachable — the same synchronous seam the harness reads.
    """
    from app.ops import registry

    if _stores_on():
        from app.data.session import get_sessionmaker

        try:
            async with get_sessionmaker()() as session:
                active = await registry.get_active(session, prompt_key)
            if active is not None:
                return OpsActivePromptResponse(
                    prompt_key=prompt_key,
                    version=active.version,
                    status=active.status.value,
                    system_prompt=active.system_prompt,
                    config=dict(active.config or {}),
                    created_by=active.created_by,
                    notes=active.notes,
                    cached=False,
                )
        except Exception:  # noqa: BLE001 - fall through to the cache/empty path
            logger.debug("ops_prompts_active DB read failed.", exc_info=True)

    cached = registry.get_cached_active(prompt_key)
    if cached is not None:
        system_prompt, config, version = cached
        return OpsActivePromptResponse(
            prompt_key=prompt_key,
            version=version,
            status="active",
            system_prompt=system_prompt,
            config=dict(config or {}),
            cached=True,
        )
    return OpsActivePromptResponse(prompt_key=prompt_key)


@router.get("/ops/evals", response_model=OpsEvalsResponse, tags=["ops"])
async def ops_evals(
    prompt_key: str | None = None,
    run_id: str | None = None,
    limit: int = 50,
    auth: AuthContext = Depends(require_auth),
) -> OpsEvalsResponse:
    """Return recent persisted trace-eval rows (the eval trend / per-step scores).

    Trace-eval rows are keyed by ``run_id`` (one row per graded facet). Filter by
    ``run_id`` for a single run's breakdown; ``prompt_key`` narrows to rows whose detail
    carries it. ``limit`` is clamped to ``[1, 500]``. Degrades to empty when stores off.
    """
    if not _stores_on():
        return OpsEvalsResponse(rows=[])
    capped = max(1, min(limit, _OPS_EVALS_LIMIT_MAX))
    try:
        from sqlalchemy import select

        from app.data.models import EvalResult
        from app.data.session import get_sessionmaker

        async with get_sessionmaker()() as session:
            stmt = select(EvalResult)
            if run_id is not None:
                stmt = stmt.where(EvalResult.run_id == run_id)
            if prompt_key is not None:
                stmt = stmt.where(EvalResult.prompt_key == prompt_key)
            stmt = stmt.order_by(EvalResult.ts.desc(), EvalResult.id.desc()).limit(capped)
            rows = list((await session.execute(stmt)).scalars().all())
    except Exception:  # noqa: BLE001 - stores are optional; degrade to empty
        logger.debug("ops_evals read failed — degrading to empty.", exc_info=True)
        return OpsEvalsResponse(rows=[])
    return OpsEvalsResponse(
        rows=[
            OpsEvalRow(
                id=r.id,
                run_id=r.run_id,
                metric=r.metric,
                score=r.score,
                passed=r.passed,
                detail=dict(r.detail or {}),
                ts=_iso_ts(r.ts),
            )
            for r in rows
        ]
    )


@router.post("/ops/diagnose", response_model=OpsDiagnoseResponse, tags=["ops"])
async def ops_diagnose(
    req: OpsDiagnoseRequest,
    auth: AuthContext = Depends(require_admin_or_ai_team),
) -> OpsDiagnoseResponse:
    """Cluster recent failing evals for ``prompt_key`` and draft an improved prompt (admin/ai_team).

    Runs :func:`app.ops.diagnose.diagnose` with the live ``app.core.llm.complete``
    optimizer; the rewrite is written **only as a DRAFT** (never promoted). Returns the
    draft id + failure breakdown. 503 when the stores are off.
    """
    _require_stores()
    from app.core.llm import complete
    from app.data.session import get_sessionmaker
    from app.ops.diagnose import diagnose

    async with get_sessionmaker()() as session:
        result = await diagnose(
            session, prompt_key=req.prompt_key, complete=complete, limit=req.limit
        )
        await session.commit()
    await _safe_audit(
        "ops.diagnose",
        auth.username,
        payload={
            "prompt_key": req.prompt_key,
            "draft_version_id": result.draft_version_id,
            "failures_considered": result.failures_considered,
        },
    )
    return OpsDiagnoseResponse(
        draft_version_id=result.draft_version_id,
        failure_summary=result.failure_summary,
        failures_considered=result.failures_considered,
        metric_breakdown=result.metric_breakdown,
    )


@router.post("/ops/release", response_model=OpsReleaseResponse, tags=["ops"])
async def ops_release(
    req: OpsReleaseRequest,
    auth: AuthContext = Depends(require_admin_or_ai_team),
) -> OpsReleaseResponse:
    """Run the eval gate + tiered decision on a draft (admin/ai_team).

    Injects the REAL regression scorer (:func:`app.ops.gate.make_eval_fn`, which generates
    an answer under the candidate prompt and judges it) and the REAL durable
    ``approval_enqueue`` (:func:`app.ops.gate.enqueue_release_approval`, a
    ``prompt_release`` inbox row). A low-risk winning draft is promoted autonomously; a
    risky one is staged (a pending approval appears); a draft that fails the eval is
    rejected. 503 when the stores are off.
    """
    _require_stores()
    from app.core.llm import complete
    from app.data.session import get_sessionmaker
    from app.ops.gate import enqueue_release_approval, make_eval_fn
    from app.ops.release import release

    eval_fn = make_eval_fn(complete)
    tenant_id = auth.tenant_id

    async def approval_enqueue(*, prompt_key, draft_version_id, risk, reason) -> str:  # noqa: ANN001
        return await enqueue_release_approval(
            prompt_key=prompt_key,
            draft_version_id=draft_version_id,
            risk=risk,
            reason=reason,
            tenant_id=tenant_id,
        )

    try:
        async with get_sessionmaker()() as session:
            result = await release(
                session,
                draft_version_id=req.draft_version_id,
                eval_fn=eval_fn,
                approval_enqueue=approval_enqueue,
                autonomy=req.autonomy,
                margin=req.margin,
            )
            await session.commit()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    await _safe_audit(
        "ops.release",
        auth.username,
        payload={
            "draft_version_id": req.draft_version_id,
            "outcome": result.outcome,
            "risk": result.risk.level,
            "eval_score": result.eval_score,
            "baseline_score": result.baseline_score,
            "approval_id": result.approval_id,
        },
    )
    return OpsReleaseResponse(
        outcome=result.outcome,
        risk_level=result.risk.level,
        risk_reasons=result.risk.reasons,
        eval_score=result.eval_score,
        baseline_score=result.baseline_score,
        reason=result.reason,
        approval_id=result.approval_id,
    )


@router.post("/ops/rollback", response_model=OpsRollbackResponse, tags=["ops"])
async def ops_rollback(
    req: OpsRollbackRequest,
    auth: AuthContext = Depends(require_admin_or_ai_team),
) -> OpsRollbackResponse:
    """Revert ``prompt_key`` to its previous version — a one-call rollback (admin/ai_team).

    Reactivates the most-recent archived version and archives the current active. 503
    when the stores are off.
    """
    _require_stores()
    from app.data.session import get_sessionmaker
    from app.ops import registry

    async with get_sessionmaker()() as session:
        reverted = await registry.rollback(session, req.prompt_key)
        await session.commit()
    await _safe_audit(
        "ops.rollback",
        auth.username,
        payload={
            "prompt_key": req.prompt_key,
            "reverted": reverted is not None,
            "active_version": reverted.version if reverted is not None else None,
        },
    )
    return OpsRollbackResponse(
        prompt_key=req.prompt_key,
        reverted=reverted is not None,
        active_version=reverted.version if reverted is not None else None,
    )


@router.get(
    "/ops/releases/pending", response_model=OpsPendingReleasesResponse, tags=["ops"]
)
async def ops_releases_pending(
    limit: int = 50,
    auth: AuthContext = Depends(require_admin_or_ai_team),
) -> OpsPendingReleasesResponse:
    """Return the staged prompt-release approvals awaiting a human decision (admin/ai_team).

    Tenant-scoped (a platform-admin sees every tenant's staged releases; a tenant-admin
    only its own). Degrades to empty when the stores are off.
    """
    if not _stores_on():
        return OpsPendingReleasesResponse(rows=[])
    from app.ops.gate import list_pending_releases

    capped = max(1, min(limit, _APPROVALS_LIMIT_MAX))
    scoped = _scope_tenant(auth, None)
    try:
        rows = await list_pending_releases(limit=capped, tenant_id=scoped)
    except Exception:  # noqa: BLE001 - stores are optional; degrade to empty
        logger.debug("ops_releases_pending read failed — empty.", exc_info=True)
        return OpsPendingReleasesResponse(rows=[])
    return OpsPendingReleasesResponse(
        rows=[
            OpsReleaseApprovalRow(
                approval_id=r.approval_id,
                prompt_key=r.prompt_key,
                draft_version_id=r.draft_version_id,
                risk=r.risk,
                reason=r.reason,
                created_at=r.created_at,
            )
            for r in rows
        ]
    )


@router.post(
    "/ops/releases/{approval_id}/decide",
    response_model=OpsReleaseDecisionResponse,
    tags=["ops"],
)
async def ops_release_decide(
    approval_id: str,
    req: OpsReleaseDecisionRequest,
    auth: AuthContext = Depends(require_admin),
) -> OpsReleaseDecisionResponse:
    """Resolve a staged prompt release: promote on approve, archive on reject (admin).

    Calls :func:`app.ops.gate.decide_release`, which applies the decision to the draft via
    :func:`app.ops.release.apply_release_decision` and flips the durable ``prompt_release``
    row terminal — decoupled from the agent-run resume machinery. 503 when stores off;
    404 when the approval id is unknown.
    """
    _require_stores()
    await _enforce_approval_tenant(approval_id, auth)
    from app.ops.gate import decide_release

    decision = await decide_release(
        approval_id=approval_id, approved=req.approved, decided_by=auth.username
    )
    if decision is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown release approval."
        )
    await _safe_audit(
        "ops.release.decide",
        auth.username,
        payload={
            "approval_id": approval_id,
            "approved": req.approved,
            "outcome": decision.outcome,
            "prompt_key": decision.prompt_key,
        },
        approved_by=auth.username,
    )
    return OpsReleaseDecisionResponse(
        approval_id=approval_id,
        approved=req.approved,
        outcome=decision.outcome,
        prompt_key=decision.prompt_key,
        active_version=decision.active_version,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Platform read-surfaces (`/ml`, `/evals`, `/gateway`, `/harness`, `/governance`,
# `/security`, `/latency`, `/redteam`) — thin, RBAC-scoped, read-only projections
# of the domain-agnostic ``aegis.*`` accessors that back the platform dashboards
# (MLOps / LLMOps / evals / token-opt / harness / governance / security / latency /
# red-team). Every handler is side-effect free (``/redteam/run`` merely runs the
# offline attack battery) and honest about empty state (Phase-3 · Task 3).
# ─────────────────────────────────────────────────────────────────────────────

# Process-wide cache of the offline evals gate. The regression gate is a
# deterministic, network-free computation (~1s) over the seed corpus, so its result
# is stable for the process lifetime — memoised once so repeated dashboard polls do
# not re-run it. ``None`` until first computed.
_evals_report_cache: EvalsReportResponse | None = None


@router.get("/ml/model-card", response_model=ModelCard, tags=["ml"])
async def ml_model_card(
    auth: AuthContext = Depends(require_admin_or_ai_team),
) -> ModelCard:
    """Return the live model's honest, **measured** model card (admin/ai_team — MLOps).

    Reads :meth:`aegis.ml.TrustworthyModel.model_card` off the process-wide fitted
    spine (via the backend ``app.ml`` shim, which wires the real domain spec). Every
    field is read off the actual model — ensemble members + weights, encoded-matrix
    width, the MAPIE class backing the coverage guarantee, the stored split sizes —
    never hardcoded. ``data_source`` labels how the training frame was obtained, so a
    synthetic-fallback model is never mistaken for a real domain-trained one.
    """
    from app.ml import get_model

    return get_model().model_card()


@router.get("/evals/report", response_model=EvalsReportResponse, tags=["evals"])
async def evals_report(
    auth: AuthContext = Depends(require_admin_or_ai_team),
) -> EvalsReportResponse:
    """Return the offline regression-gate rollup (admin/ai_team — the evals surface).

    Runs :func:`aegis.evals.run_regression_gate` with **no LLM** — the deterministic,
    network-free DeepEval-pattern gate over the seed corpus — and projects
    :meth:`RegressionReport.as_dict`. These are real, reproducible numbers (not a live
    LLM-judge pass); ``source`` says so. The result is memoised process-wide (the gate
    is deterministic) so repeated dashboard polls are cheap.
    """
    global _evals_report_cache
    if _evals_report_cache is None:
        from aegis.evals import run_regression_gate

        report = await run_regression_gate()
        _evals_report_cache = EvalsReportResponse(**report.as_dict())
    return _evals_report_cache


@router.get("/ops/params", response_model=OpsParamsResponse, tags=["ops"])
async def ops_params(
    auth: AuthContext = Depends(require_admin_or_ai_team),
) -> OpsParamsResponse:
    """Return the tunable LLM-Ops self-improvement knobs (admin/ai_team — LLMOps).

    Mirrors :func:`aegis.ops.config.get_loop_params` — the effective loop params the
    release gate reads (eval margin, blast-radius fractions, safety-term list, config
    markers, tunable keys/bounds, auto-promote ceiling). Read-only; tuning is a
    separate, audited mutation.
    """
    from aegis.ops.config import get_loop_params

    return OpsParamsResponse(**get_loop_params().as_dict())


@router.get(
    "/gateway/optimization",
    response_model=GatewayOptimizationResponse,
    tags=["gateway"],
)
async def gateway_optimization(
    auth: AuthContext = Depends(require_auth),
) -> GatewayOptimizationResponse:
    """Return the token-optimization surface (any authenticated principal — token-opt).

    ``summary`` is :func:`aegis.gateway.optimization_summary` (measured per-role savings
    vs the frontier baseline); ``config`` is :func:`aegis.gateway.optimization_config`
    (the effective routing / fallback / baseline knobs). ``require_auth`` because these
    are aggregate efficiency figures, present on every portal's token-optimization view
    (matching the ``/metrics`` / ``/savings`` convention) — not per-tenant spend. Before
    any real call the summary figures are honest zeros / ``None`` (nothing fabricated).
    """
    from aegis.gateway import optimization_config, optimization_summary

    return GatewayOptimizationResponse(
        summary=optimization_summary(), config=optimization_config()
    )


@router.get("/harness/config", response_model=HarnessConfigResponse, tags=["agent"])
async def harness_config_route(
    auth: AuthContext = Depends(require_admin_or_ai_team),
) -> HarnessConfigResponse:
    """Return the agent-harness tweakable-config record (admin/ai_team — the harness).

    Mirrors :func:`aegis.agent.harness_config`: ``knobs`` is the ordered list of knob
    descriptors a UI renders an editable form from (key, type, effective value, default,
    doc, bounds); ``effective`` is the flat effective-values map the graph actually
    reads. Read-only.
    """
    from aegis.agent import harness_config

    return HarnessConfigResponse(**harness_config())


@router.get("/agent/topology", response_model=AgentTopologyResponse, tags=["agent"])
async def agent_topology_route(
    auth: AuthContext = Depends(require_auth),
) -> AgentTopologyResponse:
    """Return the agent graph's real node/edge topology (any authenticated caller).

    Exists so nothing has to *restate* the agent's flow to draw it. The console's
    orchestration map used to carry its own hardcoded DAG, which drifted: it showed
    nine nodes instead of the real fifteen and hung the human-approval branch off the
    ML step — while :mod:`aegis.agent.graph` gates on **tool risk** in ``gate`` and
    documents that ML never gates. :func:`aegis.agent.graph_topology` reads the shape
    off the compiled LangGraph instead, so this endpoint cannot disagree with what
    runs.

    Read-only and tenant-independent: the topology is a property of the wiring, not
    of any run, principal or tenant — hence plain ``require_auth`` rather than a
    role-scoped guard.
    """
    from aegis.agent import graph_topology

    return AgentTopologyResponse(**graph_topology())


@router.get(
    "/governance/dashboard",
    response_model=GovernanceDashboard,
    tags=["governance"],
)
async def governance_dashboard_route(
    tenant_id: int | None = None,
    window: str = "day",
    auth: AuthContext = Depends(require_tenant_admin),
) -> GovernanceDashboard:
    """Return the governance dashboard snapshot for the caller's tenant scope (admin).

    Assembles :func:`aegis.governance.governance_dashboard` — tenants, per-cap
    budget/spend/remaining, users, the usage rollup and the recent audit tail — every
    figure tenant-scoped. **RBAC-scoped (C1/H2):** a platform-admin may target any
    tenant (or the platform view); a tenant-admin is pinned to its own tenant, so an
    omitted ``tenant_id`` defaults to its own and a request for a *different* tenant is
    forbidden — a tenant's dashboard never leaks another tenant's rows. Degrades to an
    honest empty snapshot when the stores are unavailable (lite/offline mode).
    """
    from aegis.governance import governance_dashboard
    from aegis.governance.types import UsageSummary

    scoped = _scope_tenant(auth, tenant_id)
    try:
        return await governance_dashboard(scoped, window=window)
    except Exception:  # noqa: BLE001 - stores are optional; degrade to an honest empty
        logger.debug("governance_dashboard read failed — degrading to empty.", exc_info=True)
        return GovernanceDashboard(
            tenant_id=scoped,
            window=window,
            tenants=[],
            budgets=[],
            users=[],
            usage=UsageSummary(
                tenant_id=scoped,
                window=window,
                total_prompt_tokens=0,
                total_completion_tokens=0,
                total_tokens=0,
                total_cost_usd=0.0,
                calls=0,
                by_model=[],
                series=[],
            ),
            recent_audit=[],
        )


@router.get("/security/posture", response_model=SecurityPostureResponse, tags=["platform"])
async def security_posture_route(
    auth: AuthContext = Depends(require_admin_or_devops),
) -> SecurityPostureResponse:
    """Return the live threat → control security posture (admin/devops — the security view).

    ``entries`` is :func:`aegis.security.security_posture` (one entry per major OWSAP /
    agentic threat, each with a ``status`` derived from the live wiring at call time —
    ``enforced`` / ``partial`` / ``not_covered``, never a fudged green); ``signals`` is
    the :func:`aegis.security.read_signals` snapshot the statuses were derived from.
    Dependency-light and side-effect free — reading it never spends.
    """
    from aegis.security import read_signals, security_posture

    signals = read_signals()
    return SecurityPostureResponse(
        entries=security_posture(signals), signals=signals
    )


@router.get("/latency", response_model=LatencyResponse, tags=["metrics"])
async def latency(
    auth: AuthContext = Depends(require_admin_or_devops),
) -> LatencyResponse:
    """Return per-node + per-run latency percentiles (admin/devops — the latency view).

    Mirrors :meth:`aegis.observability.latency_summary().as_dict`. Every figure is from
    real samples in the per-process rolling window (fed by finished runs); when no runs
    have been recorded the summary is an honest *empty* state (``empty=True``, no
    per-node rows, ``None`` run percentiles) — never fabricated zeros. ``source`` /
    ``window_capacity`` document that the window is per-process and resets on restart.
    """
    from aegis.observability import latency_summary

    return LatencyResponse(**latency_summary().as_dict())


@router.post("/redteam/run", response_model=RedteamReportResponse, tags=["platform"])
async def redteam_run(
    auth: AuthContext = Depends(require_admin_or_devops),
) -> RedteamReportResponse:
    """Run the offline attack battery and return the real verdicts (admin/devops).

    Runs :func:`aegis.redteam.run_redteam` with **no completer** — the deterministic
    guardrail backstops only, fully offline and side-effect free (it spends nothing and
    writes nothing) — and projects :meth:`RedTeamReport.as_dict`: the pass verdict, the
    ``overall`` roll-up (real ``blockRate`` + false-positive rate), the thresholds,
    per-category reports, the leaked attacks and every attack's verdict. POST because it
    *runs* the battery; the numbers are the actual verdicts, never fabricated.
    """
    from aegis.redteam import run_redteam

    report = await run_redteam()
    await _safe_audit(
        "redteam.run",
        auth.username,
        payload={
            "attacks_total": report.attacks_total,
            "block_rate": round(report.block_rate, 4),
            "passed": report.passed,
        },
    )
    return RedteamReportResponse(**report.as_dict())


def _update_dashboards(
    event: StreamEvent,
    graph_store: GraphStore,
    metrics: MetricsStore,
    persona: str,
) -> None:
    """Fold a streamed event into the (persona-scoped) graph and metrics dashboards."""
    if event.type == "retrieval":
        graph_store.merge(
            persona,
            [n.model_dump() for n in event.touched_nodes],
            [e.model_dump() for e in event.touched_edges],
        )
        if event.touched_nodes:
            metrics.note_grounding(event.run_id)
    elif event.type == "run_finished":
        metrics.record_run(
            run_id=event.run_id,
            cache_hit=event.cache_hit,
            cost_usd=event.cost_usd,
            status=event.status,
        )
