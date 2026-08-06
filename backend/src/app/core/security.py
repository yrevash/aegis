"""Password hashing and JWT access tokens for multi-tenant RBAC (§3.3).

This module is the security primitive layer the API auth surface builds on:

- **Password hashing** uses Argon2id (``argon2-cffi``) — the current password-hashing
  competition winner — via a process-wide :class:`~argon2.PasswordHasher`. Hashes are
  self-describing (algorithm + parameters + salt live in the encoded string), so no
  extra columns are needed beyond ``User.password_hash``.
- **Access tokens** are signed JWTs (``pyjwt``, HS256 by default) carrying the claims
  ``{sub=user_id, username, role (fine), coarse_role, tenant_id}``. The ``coarse_role``
  claim carries the true four-valued RBAC role so the API reads it directly rather than
  re-deriving it lossily. The signing secret and algorithm come
  from :func:`app.config.get_settings` — never hard-coded — so a real deployment sets
  ``JWT_SECRET`` in the environment.

Both dependencies are imported at module top (they are cheap, pure-Python-or-wheel
packages) and are part of the ``auth`` optional-dependency group.

Verified against: PyJWT 2.x (``jwt.encode`` / ``jwt.decode`` with ``algorithms=[...]``)
and argon2-cffi 23+ (``PasswordHasher.hash`` / ``.verify``), August 2026.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.api.schemas import Role
from app.config import get_settings

# One process-wide hasher (its parameters are captured in each produced hash).
_hasher = PasswordHasher()


# ─────────────────────────────────────────────────────────────────────────────
# Fine-grained principal role (derived from the frozen ``Role`` enum + tenant)
# ─────────────────────────────────────────────────────────────────────────────

# The coarse ``Role`` enum now carries four real values (admin / ai_team / devops /
# client; §3.3). Only ``admin`` needs a finer split — into a platform vs. tenant
# operator — which we still derive from tenancy so no extra column is required:
#   * ``platform_admin`` — ``Role.ADMIN`` with **no** tenant (global operator).
#   * ``tenant_admin``   — ``Role.ADMIN`` scoped to a single tenant.
# Every non-admin role is its own fine tier: the fine role is just the coarse role's
# string value (``"ai_team"`` / ``"devops"`` / ``"client"``). ``MEMBER`` is retained
# as the legacy fine-role alias for a plain, self-scoped member (historically
# ``"user"``); it still maps to the coarse ``CLIENT`` on decode.
PLATFORM_ADMIN = "platform_admin"
TENANT_ADMIN = "tenant_admin"
MEMBER = "user"


def principal_role(role: Role, tenant_id: int | None) -> str:
    """Return the fine-grained RBAC tier for a coarse role + tenant (§3.3).

    Args:
        role: The coarse data-layer role (``admin`` / ``ai_team`` / ``devops`` /
            ``client``).
        tenant_id: The tenant the principal belongs to, or ``None`` for a global
            (platform) operator.

    Returns:
        For ``Role.ADMIN``: :data:`PLATFORM_ADMIN` (no tenant) or :data:`TENANT_ADMIN`
        (scoped). For every other role: the role's own string value
        (``"ai_team"`` / ``"devops"`` / ``"client"``).
    """
    if role is Role.ADMIN:
        return TENANT_ADMIN if tenant_id is not None else PLATFORM_ADMIN
    return role.value


def coarse_role_from_fine(fine_role: str) -> str:
    """Collapse a fine-grained RBAC tier back to its coarse :class:`Role` value.

    This is the honest inverse of :func:`principal_role`, used to recover the true
    four-valued coarse role from a token that predates (or omits) the dedicated
    ``coarse_role`` claim:

    - ``platform_admin`` / ``tenant_admin`` → ``"admin"``.
    - ``ai_team`` / ``devops`` → themselves (they *are* coarse roles).
    - anything else, including the legacy ``"user"`` member tier → ``"client"``.

    Returns:
        The coarse role string (a valid :class:`Role` value).
    """
    if fine_role in (PLATFORM_ADMIN, TENANT_ADMIN):
        return Role.ADMIN.value
    if fine_role in (Role.AI_TEAM.value, Role.DEVOPS.value):
        return fine_role
    return Role.CLIENT.value


# ─────────────────────────────────────────────────────────────────────────────
# Password hashing (Argon2id)
# ─────────────────────────────────────────────────────────────────────────────


def hash_password(password: str) -> str:
    """Hash a plaintext password with Argon2id.

    Args:
        password: The plaintext password to hash.

    Returns:
        The self-describing Argon2 encoded hash (safe to store in
        ``User.password_hash``).
    """
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Verify a plaintext password against a stored Argon2 hash.

    Args:
        password: The candidate plaintext password.
        password_hash: The stored encoded hash, or ``None`` for a user with no
            password set (always fails closed).

    Returns:
        ``True`` iff the password matches the hash; ``False`` on any mismatch or a
        malformed/absent hash (never raises).
    """
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, Exception):  # noqa: BLE001
        return False


# ─────────────────────────────────────────────────────────────────────────────
# JWT access tokens
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
        coarse_role: The true four-valued coarse :class:`~app.api.schemas.Role`
            string (``admin`` / ``ai_team`` / ``devops`` / ``client``), read directly
            instead of a lossy admin/user re-derivation.
    """

    user_id: int | None
    username: str
    role: str
    tenant_id: int | None
    coarse_role: str


def create_access_token(
    *,
    user_id: int | None,
    username: str,
    role: str,
    tenant_id: int | None,
    coarse_role: str | None = None,
    expires_minutes: int | None = None,
) -> str:
    """Mint a signed JWT access token carrying the principal's claims.

    Args:
        user_id: The user's id (encoded as ``sub``).
        username: The principal's username.
        role: The fine-grained RBAC tier (``platform_admin`` / ``tenant_admin`` /
            ``ai_team`` / ``devops`` / ``client``).
        tenant_id: The tenant the principal is pinned to (``None`` == platform).
        coarse_role: The true four-valued coarse :class:`Role` string carried as a
            dedicated claim so the coarse role survives round-tripping without a lossy
            re-derivation. Defaults to :func:`coarse_role_from_fine` of ``role`` when
            not supplied (keeps older call-sites that pass only the fine role honest).
        expires_minutes: Token lifetime; defaults to ``settings.jwt_expire_minutes``.

    Returns:
        The encoded JWT string.
    """
    settings = get_settings()
    ttl = settings.jwt_expire_minutes if expires_minutes is None else expires_minutes
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "username": username,
        "role": role,
        "coarse_role": coarse_role if coarse_role is not None else coarse_role_from_fine(role),
        "tenant_id": tenant_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl)).timestamp()),
    }
    # ``sub`` must be a string per RFC 7519 / PyJWT 2.13; omit it for the back-compat
    # demo principals that are not backed by a users row (no numeric id).
    if user_id is not None:
        payload["sub"] = str(user_id)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> TokenClaims:
    """Decode and validate a JWT access token into typed claims.

    Args:
        token: The encoded JWT string from the ``Authorization`` header.

    Returns:
        The validated :class:`TokenClaims`.

    Raises:
        jwt.InvalidTokenError: If the signature is invalid, the token is expired,
            or a required claim is missing/malformed.
    """
    settings = get_settings()
    data = jwt.decode(
        token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
    )
    sub = data.get("sub")
    username = data.get("username")
    role = data.get("role")
    if not username or not role:
        raise jwt.InvalidTokenError("Token missing required claims.")
    role = str(role)
    # Prefer the dedicated coarse-role claim; fall back to an honest re-derivation for
    # tokens minted before it existed (keeps old bearers valid).
    coarse = data.get("coarse_role")
    coarse = str(coarse) if coarse is not None else coarse_role_from_fine(role)
    return TokenClaims(
        user_id=int(sub) if sub is not None else None,
        username=str(username),
        role=role,
        tenant_id=data.get("tenant_id"),
        coarse_role=coarse,
    )
