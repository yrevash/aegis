"""The named-seat surface — who can do what, and who gave it to them (§7.8).

Tenant sub-roles were cut. A seat is a **named grant** built out of the settings
catalogue: :data:`aegis.settings.seats.SEAT_CAPABILITIES` are five revoke-only toggles
and ``seat.label`` is the name. The whole argument for that shape lives in
:mod:`aegis.settings.seats`; this module is only its HTTP surface, and it exists for
one reason the generic settings surface cannot cover.

**Why this is not just ``PUT /settings/{key}``.** That route writes at the caller's own
layers by design — its ``SettingWriteRequest`` docstring is explicit that *"``scope``
names a layer, never a target"*, so a user-scope write there stamps the caller's own
``user_id`` and nothing else. That is exactly right for a preference and exactly wrong
for a seat: a seat is something an administrator gives to **somebody else**. So the
target is a path parameter here, and every guard that implies is on the server:

* **The tenant is never the body's** (7.16 row 12). It comes from
  :meth:`~app.api.routes.AuthContext.tenant_scope` through :func:`_scope_tenant`, the
  same sealed authority every other scoped write uses. A platform admin must name the
  tenant they are acting in via the target user, and even then the row is stamped with
  the tenant the *user* belongs to, never one the request asserted.
* **The target must live in that tenant.** Resolved and compared before any write, so a
  cross-tenant attempt is a clean 403 rather than a row written into another tenant's
  scope — the same shape as ``admin_set_user_role``.
* **A platform-staff account is never a valid target.** Seats divide up authority a
  *tenant* holds. Writing one against an untenanted operator would be a tenant-scoped
  row narrowing a platform action, and :func:`aegis.settings.seats.seat_allows` would
  ignore it anyway; a write that could never take effect is refused rather than stored.

**Why a tenant admin cannot grant platform authority with this** (7.16 row 15). Not
because this module checks for it — because there is no value it could send that would.
Every capability key is ``TIGHTEN_ONLY`` with ``default=True`` and
``stricter=Strictness.LOWER``, so :func:`aegis.settings.resolver.write_setting` folds
any write against the enclosing scopes and the strictest value wins. ``True`` is the
platform's own default, so writing ``True`` is a no-op and writing ``False`` removes a
capability. There is no third value. The seat is then read **after** the coarse role
guard has already admitted the request (:func:`app.api.routes._require_seat`), so the
effective permission is ``coarse_role_permits AND seat_allows`` — an ``AND`` has no
branch that adds anything.

**Who granted it** is not a new mechanism either: ``settings.updated_by`` and
``updated_at`` already carry it, and the write is audited like every other.
"""

from __future__ import annotations

import logging
from typing import Any

from aegis.settings import (
    SettingError,
    UnknownSettingError,
    write_setting,
)
from aegis.settings.models import SettingScope
from aegis.settings.seats import (
    SEAT_CAPABILITIES,
    SEAT_LABEL_KEY,
    seat_of,
)
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes import (
    AuthContext,
    _safe_audit,
    _scope_tenant,
    require_tenant_admin,
)
from app.data.session import get_sessionmaker, set_tenant_scope

__all__ = [
    "SeatCapabilityRow",
    "SeatRow",
    "SeatWriteRequest",
    "SeatsResponse",
    "mount",
    "seats_router",
]

logger = logging.getLogger(__name__)

seats_router = APIRouter()

#: Every key a seat write may name. Closed, and derived from the catalogue rather than
#: restated, so a capability added to :data:`SEAT_CAPABILITIES` is writable here in the
#: same change and a key that is *not* part of a seat can never be written through this
#: route — which would otherwise be a second, unguarded way into the settings table.
_WRITABLE: frozenset[str] = frozenset({SEAT_LABEL_KEY} | {c.key for c in SEAT_CAPABILITIES})


class SeatCapabilityRow(BaseModel):
    """One capability of one seat, with the layer that decided it."""

    key: str = Field(description="The catalogue key.")
    title: str = Field(description="The short human name a screen renders.")
    allowed: bool = Field(description="Whether the seat currently permits it.")
    source: str = Field(
        description="platform | tenant | user — the layer whose write decided this."
    )
    gates: str = Field(description="Where the narrowing check that reads it lives.")


class SeatRow(BaseModel):
    """One user's seat: the name, and what it may do."""

    user_id: int = Field(alias="userId")
    username: str = ""
    tenant_id: int = Field(alias="tenantId")
    label: str = Field(default="", description="The seat's name, e.g. 'Support Lead'.")
    capabilities: list[SeatCapabilityRow] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class SeatsResponse(BaseModel):
    """Body for ``GET /admin/seats`` — every seat in the caller's tenant."""

    tenant_id: int = Field(alias="tenantId")
    rows: list[SeatRow] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class SeatWriteRequest(BaseModel):
    """Body for ``PUT /admin/seats/{user_id}``.

    Deliberately carries **no tenant and no user**: both are the server's to decide (the
    path names the user, the sealed scope names the tenant), and a body that could name
    either is 7.16 row 12 waiting to happen. ``extra="forbid"`` is what makes that a
    422 rather than a silently ignored field.
    """

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(
        default=None,
        description="The seat's name. Omit to leave it alone; '' to clear it.",
    )
    capabilities: dict[str, bool] = Field(
        default_factory=dict,
        description=(
            "Capability key to allowed. Only seat keys are accepted. `false` revokes; "
            "`true` restores this seat to whatever the enclosing scopes already allow "
            "and can never exceed it."
        ),
    )


def _seat_row(seat: Any, *, username: str) -> SeatRow:  # noqa: ANN401 - aegis Seat
    """Project a resolved :class:`aegis.settings.seats.Seat` onto the wire."""
    return SeatRow(
        user_id=seat.user_id,
        username=username,
        tenant_id=seat.tenant_id,
        label=seat.label,
        capabilities=[
            SeatCapabilityRow(
                key=cap.key,
                title=cap.title,
                allowed=seat.capabilities[cap.key],
                source=seat.sources[cap.key],
                gates=cap.gates,
            )
            for cap in SEAT_CAPABILITIES
        ],
    )


async def _tenant_users(tenant_id: int) -> list[Any]:
    """Return the users of one tenant — the population a seat can be granted to."""
    from app.data.governance import list_users

    return await list_users(tenant_id=tenant_id)


def _acting_tenant(auth: AuthContext) -> int | None:
    """Return the tenant this caller acts in: their own, or ``None`` for platform staff."""
    return _scope_tenant(auth, None)


@seats_router.get("/admin/seats", response_model=SeatsResponse, tags=["admin"])
async def list_seats(
    tenant_id: int | None = None,
    auth: AuthContext = Depends(require_tenant_admin),
) -> SeatsResponse:
    """Return every seat in one tenant — the "who can do what" table.

    A tenant admin reads their own tenant and cannot name another (``_scope_tenant``
    refuses with a 403). A platform admin has no tenant of their own, so they must name
    one: seats are a tenant construct and "every seat everywhere" is not a table anyone
    can act on.

    Raises:
        HTTPException: 400 when platform staff name no tenant, 403 on a cross-tenant
            request, 503 when the settings store cannot be read.
    """
    scope = _acting_tenant(auth)
    target = scope if scope is not None else tenant_id
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Seats belong to a tenant. Name the tenant to read: a platform operator "
                "holds no seat of their own, and there is no combined view across "
                "tenants that anybody could act on."
            ),
        )
    if scope is not None and tenant_id is not None and tenant_id != scope:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-tenant access is not permitted.",
        )
    users = await _tenant_users(target)
    try:
        async with get_sessionmaker()() as session:
            await set_tenant_scope(session, target)
            rows = [
                _seat_row(
                    await seat_of(session, tenant_id=target, user_id=user.id),
                    username=user.username,
                )
                for user in users
            ]
    except SQLAlchemyError as exc:
        logger.error("Seat listing failed for tenant %s.", target, exc_info=True)  # noqa: TRY400
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The settings store is unreachable, so no seat can be reported. Nothing "
                "is returned rather than a permission table that might be wrong."
            ),
        ) from exc
    return SeatsResponse(tenant_id=target, rows=rows)


@seats_router.put("/admin/seats/{user_id}", response_model=SeatRow, tags=["admin"])
async def put_seat(
    user_id: int,
    req: SeatWriteRequest,
    auth: AuthContext = Depends(require_tenant_admin),
) -> SeatRow:
    """Name a seat and set what it may do, for one user in the caller's tenant.

    Every refusal that matters is :func:`aegis.settings.resolver.write_setting`'s, not
    this route's: it already refuses a role that may not write the key, a scope beyond
    that role's reach, a value of the wrong type, and — the one that carries §7.16
    row 15 — a ``TIGHTEN_ONLY`` write weaker than the enclosing scope. Re-deciding any
    of that here would be a second policy that can disagree with the first.

    What *is* decided here is the target, because the target is not a value: the tenant
    comes from the sealed scope and the user must already live in it.

    Raises:
        HTTPException: 400 when platform staff act with no tenant resolved, 403 on a
            cross-tenant or platform-staff target, 404 for an unknown user, 409 for a
            write the tighten-only fold refuses, 422 for an illegal value or an unknown
            capability key, 503 when the store is unreachable.
    """
    from app.api.routes_console import _settings_http_error
    from app.data.governance import user_tenant_id

    unknown = sorted(set(req.capabilities) - _WRITABLE)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"{unknown} are not seat capabilities. A seat is the closed set "
                f"{sorted(_WRITABLE)}; writing anything else through this route would "
                "be a second, unguarded way into the settings table."
            ),
        )

    target_tenant = await user_tenant_id(user_id)
    if target_tenant is None:
        # Covers both "no such user" and "a platform-staff account". They are the same
        # answer on purpose: distinguishing them tells a tenant admin which user ids
        # exist outside their tenant.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such user in this tenant.",
        )
    scope = _acting_tenant(auth)
    if scope is not None and target_tenant != scope:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A tenant administrator may only seat users in its own tenant.",
        )

    writes: list[tuple[str, Any]] = []
    if req.label is not None:
        writes.append((SEAT_LABEL_KEY, req.label))
    writes.extend(sorted(req.capabilities.items()))
    if not writes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Nothing to write: name the seat, set a capability, or both.",
        )

    try:
        async with get_sessionmaker()() as session:
            await set_tenant_scope(session, target_tenant)
            for key, value in writes:
                await write_setting(
                    session,
                    key,
                    value,
                    scope=SettingScope.USER,
                    actor_role=auth.fine_role,
                    tenant_id=target_tenant,
                    user_id=user_id,
                    actor_user_id=auth.user_id,
                    updated_by=auth.username,
                )
            seat = await seat_of(session, tenant_id=target_tenant, user_id=user_id)
            await session.commit()
    except UnknownSettingError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except SettingError as exc:
        raise _settings_http_error(exc) from exc
    except SQLAlchemyError as exc:
        logger.error("Seat write failed for user %s.", user_id, exc_info=True)  # noqa: TRY400
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The settings store is unreachable, so the seat was not changed.",
        ) from exc

    await _safe_audit(
        "admin.seat.set",
        auth,
        payload={
            "user_id": user_id,
            "tenant_id": target_tenant,
            "label": seat.label,
            "capabilities": seat.capabilities,
        },
        tenant_id=target_tenant,
    )
    users = await _tenant_users(target_tenant)
    username = next((u.username for u in users if u.id == user_id), "")
    return _seat_row(seat, username=username)


def mount(target: APIRouter) -> None:
    """Attach this module's routes to ``target``, skipping any already present.

    Idempotent for the same reason :func:`app.api.routes_redteam.mount` is: mounting
    twice puts a second, shadowed copy of every handler in the served table, which is
    invisible at runtime and confusing exactly where the route-coverage test reads.
    """
    existing = {
        (route.path, frozenset(getattr(route, "methods", ()) or ()))
        for route in target.routes
    }
    for route in seats_router.routes:
        key = (route.path, frozenset(getattr(route, "methods", ()) or ()))
        if key not in existing:
            target.routes.append(route)
