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
    "BudgetRow",
    "GovernanceContext",
    "GovernanceLimits",
    "Role",
    "TenantRow",
    "TokenClaims",
    "UsageByModel",
    "UsageSeriesPoint",
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
        token_cap: Max tokens over the budget window; ``None`` means uncapped.
        usd_cap: Max USD spend over the budget window; ``None`` means uncapped.
        rpm: Max requests per minute; ``None`` means unlimited.
        tpm: Max tokens per minute; ``None`` means unlimited.
    """

    token_cap: int | None = None
    usd_cap: float | None = None
    rpm: int | None = None
    tpm: int | None = None


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
