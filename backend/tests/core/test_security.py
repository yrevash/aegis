"""Unit tests for JWT access tokens, password hashing, and role derivation (§3.3)."""

from __future__ import annotations

import jwt
import pytest

from app.api.schemas import Role
from app.core.security import (
    MEMBER,
    PLATFORM_ADMIN,
    TENANT_ADMIN,
    coarse_role_from_fine,
    create_access_token,
    decode_access_token,
    hash_password,
    principal_role,
    verify_password,
)


def test_principal_role_admin_subtier_split_by_tenant():
    # Only ADMIN is split into a fine sub-tier by tenancy.
    assert principal_role(Role.ADMIN, None) == PLATFORM_ADMIN
    assert principal_role(Role.ADMIN, 5) == TENANT_ADMIN


def test_principal_role_non_admin_is_its_own_string():
    # Every non-admin role's fine tier is just its own coarse string (tenant-agnostic).
    assert principal_role(Role.AI_TEAM, None) == "ai_team"
    assert principal_role(Role.AI_TEAM, 5) == "ai_team"
    assert principal_role(Role.DEVOPS, 5) == "devops"
    assert principal_role(Role.CLIENT, 5) == "client"
    assert principal_role(Role.CLIENT, None) == "client"


def test_coarse_role_from_fine_is_the_inverse():
    # Admin sub-tiers collapse back to "admin".
    assert coarse_role_from_fine(PLATFORM_ADMIN) == "admin"
    assert coarse_role_from_fine(TENANT_ADMIN) == "admin"
    # Operational roles are already coarse.
    assert coarse_role_from_fine("ai_team") == "ai_team"
    assert coarse_role_from_fine("devops") == "devops"
    # The client tier and the legacy "user" member alias both map to "client".
    assert coarse_role_from_fine("client") == "client"
    assert coarse_role_from_fine(MEMBER) == "client"


def test_password_hash_and_verify():
    h = hash_password("s3cret")
    assert h != "s3cret"  # never stored in the clear
    assert verify_password("s3cret", h) is True
    assert verify_password("wrong", h) is False
    # A missing/None hash always fails closed, never raises.
    assert verify_password("s3cret", None) is False
    assert verify_password("s3cret", "not-a-hash") is False


def test_jwt_roundtrip_carries_claims():
    token = create_access_token(
        user_id=7, username="ada", role=TENANT_ADMIN, tenant_id=3
    )
    claims = decode_access_token(token)
    assert claims.user_id == 7
    assert claims.username == "ada"
    assert claims.role == TENANT_ADMIN
    assert claims.tenant_id == 3
    # The coarse role is carried honestly (derived here from the fine role).
    assert claims.coarse_role == "admin"


def test_jwt_carries_explicit_coarse_role_for_the_four_roles():
    # The dedicated coarse-role claim carries the true four-valued role directly, so the
    # API never has to re-derive it. ai_team/devops would be indistinguishable from a
    # lossy admin/user derivation — the explicit claim keeps them honest.
    for coarse in ("admin", "ai_team", "devops", "client"):
        token = create_access_token(
            user_id=1, username="u", role="anything", coarse_role=coarse, tenant_id=1
        )
        assert decode_access_token(token).coarse_role == coarse


def test_jwt_omits_sub_for_demo_principal():
    # A principal with no users-row id encodes with no `sub` and still decodes.
    token = create_access_token(
        user_id=None, username="admin", role=PLATFORM_ADMIN, tenant_id=None
    )
    claims = decode_access_token(token)
    assert claims.user_id is None
    assert claims.role == PLATFORM_ADMIN
    assert claims.coarse_role == "admin"
    assert claims.tenant_id is None


def test_jwt_rejects_tampered_token():
    token = create_access_token(
        user_id=1, username="x", role=MEMBER, tenant_id=1
    )
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(token + "tamper")
