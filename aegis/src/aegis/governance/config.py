"""The governance knobs, surfaced as read-only *effective* config data.

Every knob the platform governs by — the JWT signing algorithm/TTL, the RBAC role
ladder, the budget-window spans, and the Postgres RLS posture — is injectable exactly
as it already is (via :func:`aegis.governance.security.configure_security` and the
enforcement/RLS modules). This module does not add a new config source; it *reads the
effective values* back out as a typed :class:`GovernanceConfig` for the dashboard/tests.

The signing secret itself is deliberately never surfaced — only whether the built-in
dev default is still in force (which a real deployment must not leave true).
"""

from __future__ import annotations

from aegis.governance import security
from aegis.governance.enforcement import _RATE_SECONDS, _WINDOW_SECONDS
from aegis.governance.models import BudgetWindow
from aegis.governance.rls import _RLS_TABLES
from aegis.governance.security import (
    DEFAULT_JWT_SECRET,
    MEMBER,
    PLATFORM_ADMIN,
    TENANT_ADMIN,
)
from aegis.governance.types import (
    BudgetDefaults,
    GovernanceConfig,
    JwtConfig,
    RlsConfig,
    Role,
    RoleTier,
)

__all__ = ["RBAC_LADDER", "effective_config", "role_rank"]


# The RBAC ladder as data — the fine tiers ranked by administrative privilege (higher
# == more). The two admin sub-tiers dominate the peer operational roles (``ai_team`` /
# ``devops`` share rank 2 — neither dominates the other), which dominate the self-scoped
# ``client``. Descriptive only: this mirrors the existing RBAC semantics and changes no
# guard behaviour. The legacy ``MEMBER`` ("user") fine alias is not a rung of its own —
# it collapses to ``client`` (see :func:`role_rank`).
RBAC_LADDER: tuple[RoleTier, ...] = (
    RoleTier(
        fine_role=PLATFORM_ADMIN,
        coarse_role=Role.ADMIN.value,
        rank=4,
        tenant_scoped=False,
        description="Global operator across every tenant (no tenant pin).",
    ),
    RoleTier(
        fine_role=TENANT_ADMIN,
        coarse_role=Role.ADMIN.value,
        rank=3,
        tenant_scoped=True,
        description="Administrator scoped to a single tenant.",
    ),
    RoleTier(
        fine_role=Role.AI_TEAM.value,
        coarse_role=Role.AI_TEAM.value,
        rank=2,
        tenant_scoped=True,
        description="AI/ML engineering operator (owns the LLM-Ops surfaces).",
    ),
    RoleTier(
        fine_role=Role.DEVOPS.value,
        coarse_role=Role.DEVOPS.value,
        rank=2,
        tenant_scoped=True,
        description="Platform/operations operator (owns the DevOps surfaces).",
    ),
    RoleTier(
        fine_role=Role.CLIENT.value,
        coarse_role=Role.CLIENT.value,
        rank=1,
        tenant_scoped=True,
        description="Business/end-user, always self-scoped to its own data.",
    ),
)

_RANK: dict[str, int] = {tier.fine_role: tier.rank for tier in RBAC_LADDER}


def role_rank(fine_role: str) -> int:
    """Return the administrative privilege rank of a fine RBAC tier (higher == more).

    The legacy ``MEMBER`` ("user") alias ranks as ``client``; an unknown tier ranks 0
    (below every real tier), failing closed for any ordering comparison.
    """
    if fine_role == MEMBER:
        return _RANK[Role.CLIENT.value]
    return _RANK.get(fine_role, 0)


def effective_config() -> GovernanceConfig:
    """Return the effective, injected governance configuration as typed data.

    Reads the live :class:`~aegis.governance.security.SecurityConfig` (so a host that
    called :func:`~aegis.governance.security.configure_security` sees its own values),
    the enforcement window spans, the RBAC ladder, and the RLS posture. The JWT secret
    is never included — only whether the dev default is still in force.
    """
    sec = security._config
    return GovernanceConfig(
        jwt=JwtConfig(
            algorithm=sec.jwt_algorithm,
            expire_minutes=sec.jwt_expire_minutes,
            secret_is_dev_default=sec.jwt_secret == DEFAULT_JWT_SECRET,
        ),
        role_ladder=list(RBAC_LADDER),
        budgets=BudgetDefaults(
            default_window=BudgetWindow.DAY.value,
            day_window_seconds=_WINDOW_SECONDS[BudgetWindow.DAY],
            month_window_seconds=_WINDOW_SECONDS[BudgetWindow.MONTH],
            rate_window_seconds=_RATE_SECONDS,
        ),
        rls=RlsConfig(
            enforced_on="postgresql",
            fail_closed=True,
            tables=list(_RLS_TABLES),
        ),
    )
