"""Phase 0 contract tests: the GovernanceContext contextvar seam (§3.3).

Only the contract is frozen here (set / get / reset + the typed context); no code
enforces the context yet, so the default must be ``None`` and behaviour unchanged.
"""

from __future__ import annotations

from app.api.schemas import Role
from app.core import (
    GovernanceContext,
    GovernanceLimits,
    get_governance_context,
    reset_governance_context,
    set_governance_context,
)


def test_default_context_is_none():
    assert get_governance_context() is None


def test_set_get_reset_roundtrip():
    ctx = GovernanceContext(
        tenant_id=7,
        user_id=42,
        role=Role.ADMIN,
        limits=GovernanceLimits(token_cap=1000, usd_cap=5.0, rpm=60, tpm=40_000),
    )
    token = set_governance_context(ctx)
    try:
        current = get_governance_context()
        assert current is ctx
        assert current.tenant_id == 7
        assert current.user_id == 42
        assert current.role is Role.ADMIN
        assert current.limits.token_cap == 1000
        assert current.limits.tpm == 40_000
    finally:
        reset_governance_context(token)
    # After reset the slot is restored to its prior (empty) value.
    assert get_governance_context() is None


def test_limits_default_to_uncapped():
    limits = GovernanceLimits()
    assert limits.token_cap is None
    assert limits.usd_cap is None
    assert limits.rpm is None
    assert limits.tpm is None


def test_context_defaults_are_unscoped():
    ctx = GovernanceContext()
    assert ctx.tenant_id is None
    assert ctx.user_id is None
    assert ctx.role is None
    assert isinstance(ctx.limits, GovernanceLimits)
