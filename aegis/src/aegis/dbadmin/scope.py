"""Which rows this connection may see — derived from a sealed authority, never widened.

This is the smallest module in the package and the one the whole page stands on, for the
same reason :mod:`aegis.analytics.rls` is: the tenant filter has to come from an authority
the request could not influence, and "no authority" has to be an *exception* rather than a
value that can be passed onward and mistaken for "every tenant".

**The trap this module exists to close.** Aegis's own ``tenant_isolation`` policy predicate
(:data:`aegis.governance.rls._TENANT_ISOLATION_PREDICATE`) is deliberately null-tolerant::

    substring(current_setting('app.tenant_id', true) from '^[0-9]+$') IS NULL
      OR tenant_id = substring(...)::int

An unset or empty ``app.tenant_id`` therefore does **not** restrict. That is correct for
the request path — the login lookup happens before a tenant is known, and every
platform-admin surface reads across tenants — and it is exactly wrong for a page whose
job is to run arbitrary reads over every table in the database. A database console that
forgot to bind the GUC would return every tenant's rows and look completely healthy doing
it.

So the console does not rely on the policy for its narrowing. It binds **two** GUCs from
the value resolved here, and every statement it generates carries
:data:`aegis.dbadmin.catalogue.TENANT_PREDICATE`, which is *not* null-tolerant: nothing
bound means no rows, which is an obviously empty screen rather than a cross-tenant leak.
The RLS policy stays underneath as the second layer, doing what it does.

**The platform-wide read is an opt-out somebody performed, never one a connection drifted
into.** It needs :data:`ALL_TENANTS_GUC` set to the literal ``'on'`` — a GUC in its own
name that nothing else in Aegis writes. Reusing the empty ``app.tenant_id`` that
:func:`aegis.governance.rls.set_tenant_scope` writes on reset would have made "show
everything" the value a connection *returns to*.
"""

from __future__ import annotations

from dataclasses import dataclass

from aegis.retrieval.types import (
    ALL_TENANTS,
    TenantScope,
    UntenantedPrincipalError,
)

__all__ = [
    "ALL_TENANTS_GUC",
    "TENANT_GUC",
    "ScopeBinding",
    "binding_for",
    "narrow_to",
]

#: The GUC carrying the tenant a connection may read. The **same** one Aegis's own
#: ``tenant_isolation`` policy reads, deliberately: one value, so the policy underneath
#: and the predicate welded into the statement cannot be looking at two different tenants.
TENANT_GUC = "app.tenant_id"

#: The GUC that opts a connection out of the tenant filter. Its own name, written by
#: nothing else in Aegis, so "read every tenant" is a value somebody set on purpose.
ALL_TENANTS_GUC = "app.dbadmin_all_tenants"

#: The only value of :data:`ALL_TENANTS_GUC` that widens anything. Compared literally.
ALL_TENANTS_ON = "on"


@dataclass(frozen=True, slots=True)
class ScopeBinding:
    """The two GUC values one read runs under, and the sentence describing them.

    Attributes:
        tenant_id: The tenant to bind, or ``None`` for a resolved platform-wide read.
        all_tenants: Whether the platform-wide opt-out is being taken. Exactly one of
            these two states is meaningful, and :func:`binding_for` is the only thing
            that constructs them, so "both" and "neither" are not reachable.
    """

    tenant_id: int | None
    all_tenants: bool

    @property
    def tenant_value(self) -> str:
        """The string to write into :data:`TENANT_GUC`.

        The empty string for a platform-wide read, matching what
        :func:`aegis.governance.rls.set_tenant_scope` writes when it clears a scope —
        so the RLS layer underneath sees the state it already understands.
        """
        return "" if self.tenant_id is None else str(self.tenant_id)

    @property
    def all_tenants_value(self) -> str:
        """The string to write into :data:`ALL_TENANTS_GUC` — ``'on'`` or ``'off'``."""
        return ALL_TENANTS_ON if self.all_tenants else "off"

    def describe(self) -> str:
        """One phrase naming the authority, for the audit row and the screen."""
        if self.all_tenants:
            return "every tenant (platform-wide read)"
        return f"tenant {self.tenant_id}"


def binding_for(scope: TenantScope) -> ScopeBinding:
    """Resolve a sealed :class:`~aegis.retrieval.types.TenantScope` into GUC values.

    Three inputs map onto three distinct outputs, one of which is an exception. The
    naive spelling — ``tenant_id or None`` somewhere upstream — has two, and collapses
    "this principal has no tenant" into "do not restrict".

    ``bool`` is rejected explicitly even though it is an ``int`` in Python: ``True``
    would otherwise bind tenant 1, which is a real tenant with real rows.

    Args:
        scope: The authority from :meth:`AuthContext.tenant_scope`. Not a request field,
            not a header, not a query parameter.

    Returns:
        The binding to write onto the connection.

    Raises:
        UntenantedPrincipalError: If ``scope`` is neither
            :data:`~aegis.retrieval.types.ALL_TENANTS` nor a genuine ``int``.
    """
    if scope is ALL_TENANTS:
        return ScopeBinding(tenant_id=None, all_tenants=True)
    if isinstance(scope, bool) or not isinstance(scope, int):
        raise UntenantedPrincipalError(
            "The database console needs a resolved tenant authority before it will read "
            f"anything, and got {scope!r}. There is no honest set of rows to show a "
            "principal whose tenant is unknown, so nothing is read rather than everything."
        )
    return ScopeBinding(tenant_id=scope, all_tenants=False)


def narrow_to(scope: TenantScope, requested: int | None) -> TenantScope:
    """Apply an operator's tenant selector, refusing anything that would widen.

    The tenant-impersonation control (§7.9) is the most convincing thirty seconds of the
    isolation story available — bind a tenant, re-run the same read, watch the rows
    disappear — and it is also the most obvious place to hand a caller a bigger authority
    than it arrived with. So it can only ever *narrow*:

    * ``requested is None`` → the caller's own sealed authority, unchanged.
    * a platform-wide caller naming a tenant → that tenant. This is the impersonation.
    * a tenant-bound caller naming **its own** tenant → that tenant; a no-op, allowed so
      the control behaves the same on every portal.
    * a tenant-bound caller naming **another** tenant → refused.

    Args:
        scope: The caller's sealed authority.
        requested: The tenant id the operator selected, or ``None`` for no selection.

    Returns:
        The authority the read will run under.

    Raises:
        UntenantedPrincipalError: If ``scope`` is not a resolved authority, or if
            ``requested`` names a tenant this caller has no authority over.
    """
    binding_for(scope)  # resolve-or-raise, before anything is compared
    if requested is None:
        return scope
    if isinstance(requested, bool) or not isinstance(requested, int):
        raise UntenantedPrincipalError(
            f"The tenant selector must be a tenant id, and got {requested!r}."
        )
    if scope is ALL_TENANTS or scope == requested:
        return requested
    raise UntenantedPrincipalError(
        f"This sign-in is bound to tenant {scope} and asked to read tenant {requested}. "
        "The tenant selector can only narrow a read, never move it to another tenant."
    )
