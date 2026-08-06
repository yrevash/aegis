"""Unit tests for JWT access tokens, password hashing, and role derivation (§3.3)."""

from __future__ import annotations

import jwt
import pytest

from app.api.schemas import Role
from app.core.security import (
    MEMBER,
    PLATFORM_ADMIN,
    TENANT_ADMIN,
    create_access_token,
    decode_access_token,
    hash_password,
    principal_role,
    verify_password,
)


def test_principal_role_three_tier():
    # admin with no tenant is the global platform operator.
    assert principal_role(Role.ADMIN, None) == PLATFORM_ADMIN
    # admin scoped to a tenant is a tenant-admin.
    assert principal_role(Role.ADMIN, 5) == TENANT_ADMIN
    # a plain user is always a member.
    assert principal_role(Role.USER, 5) == MEMBER
    assert principal_role(Role.USER, None) == MEMBER


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


def test_jwt_omits_sub_for_demo_principal():
    # A principal with no users-row id encodes with no `sub` and still decodes.
    token = create_access_token(
        user_id=None, username="admin", role=PLATFORM_ADMIN, tenant_id=None
    )
    claims = decode_access_token(token)
    assert claims.user_id is None
    assert claims.role == PLATFORM_ADMIN
    assert claims.tenant_id is None


def test_jwt_rejects_tampered_token():
    token = create_access_token(
        user_id=1, username="x", role=MEMBER, tenant_id=1
    )
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(token + "tamper")
