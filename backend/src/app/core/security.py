"""Backend shim: password hashing + JWT tokens now live in ``aegis.governance``.

This module used to own Argon2id password hashing, HS256 JWT access tokens, and the
four-tier RBAC role derivation (``principal_role`` / ``coarse_role_from_fine`` +
``PLATFORM_ADMIN`` / ``TENANT_ADMIN`` / ``MEMBER``). That implementation has moved to the
standalone, host-agnostic ``aegis.governance.security`` (see ``/aegis``) so it can be
imported without pulling in this platform's settings.

This is the **strangler shim**: at import time it injects this app's JWT signing
configuration via :func:`aegis.governance.security.configure_security` — reading
``jwt_secret`` / ``jwt_algorithm`` / ``jwt_expire_minutes`` off ``app.config`` (the PROD
secret validation stays in ``app.config``) — then re-exports the public surface so every
existing call site (``app.api.routes`` auth + guards, tests) keeps working unchanged.
"""

from __future__ import annotations

from aegis.governance.security import (
    MEMBER,
    PLATFORM_ADMIN,
    TENANT_ADMIN,
    TokenClaims,
    coarse_role_from_fine,
    configure_security,
    create_access_token,
    decode_access_token,
    hash_password,
    principal_role,
    verify_password,
)

from app.config import get_settings

__all__ = [
    "MEMBER",
    "PLATFORM_ADMIN",
    "TENANT_ADMIN",
    "TokenClaims",
    "coarse_role_from_fine",
    "create_access_token",
    "decode_access_token",
    "hash_password",
    "principal_role",
    "verify_password",
]

# Wire this app's JWT signing config into the injected security module once, at import
# time — every ``create_access_token`` / ``decode_access_token`` call site then signs and
# validates with this deployment's secret. The dev-default / production secret validation
# stays in ``app.config`` (``Settings.ensure_secure_secrets``), consulted at startup.
_settings = get_settings()
configure_security(
    _settings.jwt_secret,
    _settings.jwt_algorithm,
    _settings.jwt_expire_minutes,
)
