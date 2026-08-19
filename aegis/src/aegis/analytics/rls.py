"""The tenant filter Superset cannot be talked out of — derived, never accepted.

This is the module the whole feature stands on, so it is the smallest one and it does
no I/O.

**The claim.** A Superset *guest token* carries an ``rls`` list, and Superset injects
every clause in it into the ``WHERE`` of every query run under that token. So the
tenant filter is not a UI convention, a query parameter, or a header the browser could
edit — it is a predicate compiled into the SQL by the server that owns the data. A
browser holding the token cannot remove the clause any more than it can remove the
``FROM``.

**The precondition.** That is only true if the clause is derived from an authority the
request could not influence. It is derived here from a
:data:`~aegis.retrieval.types.TenantScope` — the *sealed* value
:meth:`AuthContext.tenant_scope` resolves — and from nothing else. There is no
parameter on any function in this module that a request body could reach.

**The failure mode this shape removes.** The obvious spelling is
``clause = f"tenant_id = {tenant_id}"`` with ``tenant_id or ''`` somewhere upstream,
and it fails *open*: a principal with no tenant, or a tenant id that arrived from a
JWT claim as the string ``"3"``, or a ``bool``, all collapse into "no clause" — which
in RLS terms is "every tenant". Five real cross-tenant leaks in this project came from
exactly that collapse. So :func:`guest_token_rls` has three outcomes where the naive
version has two, and the third is an exception rather than a value, so it cannot be
passed onward and mistaken for the first.
"""

from __future__ import annotations

from aegis.analytics.types import is_safe_identifier
from aegis.retrieval.types import (
    ALL_TENANTS,
    AllTenants,
    TenantScope,
    UntenantedPrincipalError,
)

__all__ = [
    "GUEST_USERNAME_PLATFORM",
    "GUEST_USERNAME_PREFIX",
    "guest_token_rls",
    "guest_user",
    "resolved_scope",
    "tenant_from_guest_username",
]

#: Prefix of the Superset guest username Aegis mints for a tenant-scoped session.
#: Superset's ``DB_CONNECTION_MUTATOR`` is handed the username and nothing richer, so
#: the tenant has to travel *in the name* for the second isolation layer to exist at
#: all. See ``docs/operations/superset-embedded.md``.
GUEST_USERNAME_PREFIX = "aegis-tenant-"

#: The guest username for a resolved platform-wide authority. Deliberately not of the
#: ``aegis-tenant-`` shape, so :func:`tenant_from_guest_username` cannot mistake it for
#: a tenant and the mutator cannot set a GUC that would narrow a platform read.
GUEST_USERNAME_PLATFORM = "aegis-platform"


def resolved_scope(scope: TenantScope) -> int | AllTenants:
    """Return ``scope`` if it is a genuine resolved authority, else raise.

    The one gate every caller in this package goes through — the guest token, the guest
    username and the server-built query filter all narrow the same value the same way,
    so there is no second, laxer path to the same data.

    ``bool`` is rejected explicitly even though it is an ``int`` in Python: ``True``
    would otherwise resolve to tenant 1, which is a real tenant with real rows.

    Args:
        scope: The value claimed to be a resolved tenant authority.

    Returns:
        The same value, narrowed.

    Raises:
        UntenantedPrincipalError: If ``scope`` is anything other than
            :data:`~aegis.retrieval.types.ALL_TENANTS` or a genuine ``int``.
    """
    if scope is ALL_TENANTS:
        return ALL_TENANTS
    if isinstance(scope, bool) or not isinstance(scope, int):
        raise UntenantedPrincipalError(
            "Superset analytics needs a resolved tenant authority to build its row-level "
            f"filter, and got {scope!r}. There is no honest chart to draw for a principal "
            "whose tenant is unknown, so nothing is rendered rather than everything."
        )
    return scope


def guest_token_rls(
    scope: TenantScope, *, column: str = "tenant_id"
) -> tuple[dict[str, str], ...]:
    """Return the ``rls`` payload for a guest token minted under ``scope``.

    Args:
        scope: The sealed authority from :meth:`AuthContext.tenant_scope`. Not a
            request field, not a header, not a query parameter.
        column: The tenant column every scoped Superset dataset carries. From
            configuration; still validated as a bare SQL identifier.

    Returns:
        One clause narrowing every query to the caller's tenant, or an **empty**
        tuple for :data:`~aegis.retrieval.types.ALL_TENANTS` — the deliberate,
        resolved platform-wide read.

    Raises:
        UntenantedPrincipalError: If ``scope`` is not a resolved authority.
        ValueError: If ``column`` is not a bare SQL identifier.
    """
    if not is_safe_identifier(column):
        raise ValueError(
            f"the Superset tenant column is configured as {column!r}, which is not a bare "
            "SQL identifier. It is interpolated into a row-level-security clause, so it "
            "is refused rather than quoted."
        )
    resolved = resolved_scope(scope)
    if resolved is ALL_TENANTS:
        return ()
    return ({"clause": f"{column} = {int(resolved)}"},)


def guest_user(scope: TenantScope) -> dict[str, str]:
    """Return the ``user`` payload for a guest token minted under ``scope``.

    Superset's ``DB_CONNECTION_MUTATOR`` receives the *username* of the principal a
    query is running for, and nothing structured. So the tenant travels in the name,
    and the mutator turns it back into ``-c app.tenant_id=N`` on Superset's own
    Postgres connection — which is the only way Aegis's Postgres row-level-security
    policies apply to a connection Aegis did not open.

    Args:
        scope: The sealed authority from :meth:`AuthContext.tenant_scope`.

    Returns:
        Superset's guest-user dict.

    Raises:
        UntenantedPrincipalError: If ``scope`` is not a resolved authority.
    """
    resolved = resolved_scope(scope)
    if resolved is ALL_TENANTS:
        return {
            "username": GUEST_USERNAME_PLATFORM,
            "first_name": "Aegis",
            "last_name": "platform",
        }
    tenant = int(resolved)
    return {
        "username": f"{GUEST_USERNAME_PREFIX}{tenant}",
        "first_name": "Aegis",
        "last_name": f"tenant {tenant}",
    }


def tenant_from_guest_username(username: str) -> int | None:
    """Recover the tenant id from a guest username, or ``None``.

    The inverse of :func:`guest_user`, and the exact logic the Superset-side
    ``DB_CONNECTION_MUTATOR`` runs. It lives here — in the package, under test — rather
    than only in the documented ``superset_config.py``, because a parser that exists
    solely inside a config file on somebody's Windows box is a parser nothing checks.

    Args:
        username: The Superset username a query is running for.

    Returns:
        The tenant id, or ``None`` for any username that does not name one — including
        :data:`GUEST_USERNAME_PLATFORM`, Superset's own service account, and anything
        malformed. ``None`` means "set no GUC", which leaves Postgres RLS with no
        tenant and therefore denying rather than widening.
    """
    if not username.startswith(GUEST_USERNAME_PREFIX):
        return None
    suffix = username[len(GUEST_USERNAME_PREFIX) :]
    if not suffix.isdigit():
        return None
    return int(suffix)
