"""Governance types — roles, token claims, the per-request context, admin DTOs.

Dependency-free (pydantic + stdlib only): importing ``aegis.governance.types`` pulls
neither the ORM (``sqlalchemy``) nor the auth primitives (``pyjwt``/``argon2``), so the
API/wire contracts can be shared without dragging in the heavy governance machinery.

- :class:`Role` — the four-valued RBAC role (a governance concept; it lives here, not in
  any host's API schema module).
- :class:`TokenClaims` — the decoded, validated claims of an access token.
- :class:`GovernanceLimits` / :class:`GovernanceContext` — the effective caps and the
  tenant/user/role bound to the current request (threaded via contextvars in
  :mod:`aegis.governance.context`).
- The admin wire DTOs (:class:`TenantRow`, :class:`AdminUserRow`, :class:`BudgetRow`,
  :class:`UsageByModel`, :class:`UsageSeriesPoint`, :class:`AuditLogRow`) returned by the
  admin rollups in :mod:`aegis.governance.enforcement` / :mod:`aegis.governance.audit`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, Field

__all__ = [
    "AdminUserRow",
    "AuditLogRow",
    "BudgetDefaults",
    "BudgetRow",
    "BudgetStatusRow",
    "GovernanceConfig",
    "GovernanceContext",
    "GovernanceDashboard",
    "GovernanceLimits",
    "JwtConfig",
    "RlsConfig",
    "Role",
    "RoleTier",
    "TenantRow",
    "TokenClaims",
    "UsageByModel",
    "UsageSeriesPoint",
    "UsageSummary",
]


class Role(StrEnum):
    """Authenticated role; drives RBAC and which portal/surface is served.

    Four real personas back the platform's RBAC:

    - ``ADMIN`` — the operator/governance role. Split at the fine tier into
      ``platform_admin`` (no tenant, global) and ``tenant_admin`` (scoped) — see
      :func:`aegis.governance.security.principal_role`.
    - ``AI_TEAM`` — the AI/ML engineering role (owns the LLM-Ops surfaces).
    - ``DEVOPS`` — the platform/operations role.
    - ``CLIENT`` — the business/end-user role (the former coarse ``user``), always
      self-scoped to its own data.

    ``CLIENT`` is the direct successor of the retired ``user`` value: any principal
    that used to be a plain ``user`` is now a ``client``.
    """

    ADMIN = "admin"
    AI_TEAM = "ai_team"
    DEVOPS = "devops"
    CLIENT = "client"


# ─────────────────────────────────────────────────────────────────────────────
# Access-token claims
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TokenClaims:
    """The decoded, validated claims carried by an access token.

    Attributes:
        user_id: The authenticated user's id (JWT ``sub``); ``None`` for the
            back-compat demo principals that are not backed by a users row.
        username: The principal's username.
        role: The fine-grained RBAC tier (``platform_admin`` / ``tenant_admin`` /
            ``ai_team`` / ``devops`` / ``client``).
        tenant_id: The tenant the request is pinned to, or ``None`` (platform scope).
        coarse_role: The true four-valued coarse :class:`Role` string
            (``admin`` / ``ai_team`` / ``devops`` / ``client``), read directly
            instead of a lossy admin/user re-derivation.
    """

    user_id: int | None
    username: str
    role: str
    tenant_id: int | None
    coarse_role: str


# ─────────────────────────────────────────────────────────────────────────────
# Per-request governance context (effective caps + the bound principal)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GovernanceLimits:
    """Effective spend/rate caps for the current principal (nearest-binding).

    Attributes:
        token_cap: Max tokens over :attr:`window`; ``None`` means uncapped.
        usd_cap: Max USD spend over :attr:`window`; ``None`` means uncapped.
        rpm: Max requests per minute; ``None`` means unlimited.
        tpm: Max tokens per minute; ``None`` means unlimited.
        window: ``'day' | 'month'`` — which window ``token_cap``/``usd_cap`` run over.
            A cap without its window is not a quantity: ``$50`` a day and ``$50`` a
            month are different limits, and the resolver used to be able to return one
            while meaning the other. ``rpm``/``tpm`` are per-minute regardless and do
            not depend on it.
    """

    token_cap: int | None = None
    usd_cap: float | None = None
    rpm: int | None = None
    tpm: int | None = None
    window: str | None = None


@dataclass(frozen=True)
class GovernanceContext:
    """The tenant/user/role and limits bound to the current request.

    Attributes:
        tenant_id: The tenant the request is pinned to (``None`` == unscoped/system).
        user_id: The acting user, if resolved.
        role: The acting principal's RBAC role.
        limits: The effective, inward-enforced spend/rate caps.
    """

    tenant_id: int | None = None
    user_id: int | None = None
    role: Role | None = None
    limits: GovernanceLimits = field(default_factory=GovernanceLimits)


# ─────────────────────────────────────────────────────────────────────────────
# Admin wire DTOs — tenants / users / budgets / usage / audit
# ─────────────────────────────────────────────────────────────────────────────


class TenantRow(BaseModel):
    """One tenant in the platform-admin `GET /admin/tenants` listing."""

    id: int
    name: str
    status: str = Field(description="Lifecycle status: 'active' | 'suspended'.")
    created_at: str = Field(description="ISO 8601 UTC creation time.")


class AdminUserRow(BaseModel):
    """One user in the tenant-scoped `GET /admin/users` listing."""

    id: int
    username: str
    role: Role
    tenant_id: int | None = None
    email: str | None = None
    is_active: bool = True


class BudgetRow(BaseModel):
    """One hierarchical spend/rate cap row (`GET /admin/budgets`)."""

    id: int
    scope_type: str = Field(description="'tenant' | 'user'.")
    scope_id: int = Field(description="Id of the tenant or user the cap governs.")
    window: str = Field(description="'day' | 'month'.")
    token_cap: int | None = None
    usd_cap: float | None = None
    rpm: int | None = None
    tpm: int | None = None


class UsageByModel(BaseModel):
    """Per-model spend rollup for the usage dashboard."""

    model: str
    cost_usd: float
    tokens: int


class UsageSeriesPoint(BaseModel):
    """One time-bucketed spend point for the usage sparkline."""

    ts: str = Field(description="ISO 8601 UTC bucket start.")
    cost_usd: float


class AuditLogRow(BaseModel):
    """One row of the first-class audit trail."""

    id: int
    ts: str = Field(description="Record timestamp as an ISO 8601 UTC string.")
    action: str = Field(description="The action performed, e.g. 'tool:update_request_status'.")
    actor: str | None = Field(default=None, description="Principal that initiated the action.")
    model: str | None = Field(default=None, description="Model deployment id involved, if any.")
    trace_id: str | None = Field(default=None, description="OTel trace id correlating spans.")
    approved_by: str | None = Field(
        default=None, description="Human who approved the action at the HITL gate, if any."
    )
    outcome: str = Field(
        default="completed",
        description=(
            "'blocked' | 'completed', DERIVED from the action name by "
            "aegis.governance.audit.classify_outcome — there is no verdict column on "
            "the trail. Carried on the wire so the word a reader sees and the word the "
            "server filtered on are the same word."
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Governance dashboard DTOs — read-only figures computed from authoritative rows
#
# These are the *data* the later governance UI renders. Every figure is derived
# single-sourced from the durable rows (the :class:`Budget` caps + the
# :class:`UsageLedger`): the accessors in :mod:`aegis.governance.dashboard` reuse the
# very same ledger-summation the gateway enforcement uses, so a dashboard spend equals
# what enforcement sees to the token/cent.
# ─────────────────────────────────────────────────────────────────────────────


class BudgetStatusRow(BaseModel):
    """A budget cap joined with its live consumption over the cap's own window.

    ``tokens_used`` / ``cost_usd_used`` / ``calls`` are summed from the
    :class:`~aegis.governance.models.UsageLedger` for the cap's scope over its rolling
    window — the identical summation :func:`aegis.governance.enforce_governance` runs,
    so the dashboard and the enforcer never disagree. ``*_remaining`` is ``None`` when
    the corresponding cap is unset (uncapped) and floored at zero once the cap is
    breached (``used`` still reveals the overage).
    """

    budget: BudgetRow
    tokens_used: int = Field(description="Tokens consumed by this scope over the window.")
    cost_usd_used: float = Field(description="USD spend by this scope over the window.")
    calls: int = Field(description="Model calls by this scope over the window.")
    tokens_remaining: int | None = Field(
        default=None, description="token_cap − tokens_used (≥0), or None when uncapped."
    )
    usd_remaining: float | None = Field(
        default=None, description="usd_cap − cost_usd_used (≥0), or None when uncapped."
    )


class UsageSummary(BaseModel):
    """Rolled-up ledger spend for one tenant (or the whole platform) over a window."""

    tenant_id: int | None = Field(
        default=None, description="The tenant scoped to, or None for a platform rollup."
    )
    window: str = Field(description="'day' | 'month' — the rolling span aggregated over.")
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int = Field(description="prompt + completion tokens over the window.")
    total_cost_usd: float
    calls: int = Field(description="Number of ledger rows (model calls) in the window.")
    by_model: list[UsageByModel]
    series: list[UsageSeriesPoint]


class GovernanceDashboard(BaseModel):
    """The full governance dashboard snapshot for one tenant scope.

    All figures are tenant-scoped when ``tenant_id`` is set: a tenant's dashboard never
    contains another tenant's tenants/budgets/users/usage/audit rows.
    """

    tenant_id: int | None = Field(
        default=None, description="The tenant scoped to, or None for the platform view."
    )
    window: str = Field(description="'day' | 'month' — the usage rollup window.")
    tenants: list[TenantRow]
    budgets: list[BudgetStatusRow]
    users: list[AdminUserRow]
    usage: UsageSummary
    recent_audit: list[AuditLogRow]


# ─────────────────────────────────────────────────────────────────────────────
# Effective-config DTOs — the governance knobs surfaced as read-only data
# ─────────────────────────────────────────────────────────────────────────────


class RoleTier(BaseModel):
    """One rung of the RBAC ladder — a fine tier, its coarse role and its rank.

    ``rank`` orders tiers by administrative privilege (higher == more): the two admin
    sub-tiers dominate the peer operational roles (``ai_team`` / ``devops`` share a
    rank — neither dominates the other), which in turn dominate the self-scoped
    ``client``. This is descriptive config data; it changes no enforcement behaviour.
    """

    fine_role: str = Field(description="Fine tier, e.g. 'platform_admin' / 'tenant_admin'.")
    coarse_role: str = Field(description="The four-valued coarse Role string.")
    rank: int = Field(description="Administrative privilege rank (higher == more).")
    tenant_scoped: bool = Field(description="Whether the tier is pinned to a tenant.")
    description: str


class JwtConfig(BaseModel):
    """The effective JWT knobs (the signing secret itself is never surfaced)."""

    algorithm: str = Field(description="JWT signing algorithm, e.g. 'HS256'.")
    expire_minutes: int = Field(description="Default access-token lifetime, in minutes.")
    secret_is_dev_default: bool = Field(
        description="True when the built-in dev secret is still in force (never in prod)."
    )


class BudgetDefaults(BaseModel):
    """The budget-window knobs the enforcer and dashboard aggregate over."""

    default_window: str = Field(description="Default budget window ('day').")
    day_window_seconds: int
    month_window_seconds: int
    rate_window_seconds: int = Field(description="Rolling span for rpm/tpm rate caps.")


class RlsConfig(BaseModel):
    """The Postgres Row-Level Security posture (a documented no-op on SQLite)."""

    enforced_on: str = Field(description="Dialect RLS engages on ('postgresql').")
    fail_closed: bool = Field(description="An unset tenant GUC admits no rows.")
    tables: list[str] = Field(description="Tables carrying the tenant_isolation policy.")


class GovernanceConfig(BaseModel):
    """The effective, injectable governance configuration surfaced as data."""

    jwt: JwtConfig
    role_ladder: list[RoleTier]
    budgets: BudgetDefaults
    rls: RlsConfig
