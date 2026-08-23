"""The alert surface — the durable inbox, and the live stream that pushes into it.

Everything in Aegis was pull. A tenant learned that their hundred documents had finished
ingesting by opening the jobs screen; a gate waiting on an approver announced itself by
existing. This module is the read side of the fix: four routes, one contract, and a
single sealed answer to *who may see what*.

The four routes, and why the split
-----------------------------------

* ``GET /notifications`` — the backlog and the badge. What the bell renders on load, for
  a user who was asleep when their ingest finished.
* ``POST /notifications/{id}/read`` / ``POST /notifications/read-all`` — the two writes.
* ``GET /notifications/stream`` — SSE, and it carries **only what happens while
  connected**. It deliberately does not replay: the frontend already fetches the list,
  and a stream that replayed on connect would re-toast a week of alerts on every network
  blip while making "how far back is everything" a question nobody can answer correctly.

The stream is ``sse_starlette`` because ``POST /v1/query`` is (see
:mod:`app.api.routes`), not because a bell needs a second transport. There is no
WebSocket layer here and there should not be: nothing in this feature is bidirectional,
and a second protocol would need its own auth, its own proxy configuration and its own
reconnect semantics for no capability at all.

Scoping, which is the part that had to be right
------------------------------------------------

The scope is resolved from the bearer token — :func:`app.api.routes._require_scope`,
which is :meth:`app.api.routes.AuthContext.tenant_scope` with the 403 attached — and
never from a query parameter. There is no request field on any of these four routes that
names a tenant or a user, so there is nothing for a caller to forge.

The stream and the list share **one** predicate: :func:`app.data.notifications.scope_predicate`
for the SQL, :func:`app.data.notifications.visible_to` for the frames, kept adjacent in
one module with a test asserting they agree. A live stream that pushes what the list
correctly hides is the specific failure this arrangement exists to make impossible — and
it is the failure you get for free when the filter is written twice.

The Postgres ``tenant_isolation`` policy on ``notifications`` is the second lock, not the
first: this deployment runs the fail-**open** predicate, so the application-level
``WHERE`` above is what actually holds.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from aegis.retrieval.types import tenant_filter
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette import EventSourceResponse, ServerSentEvent

from app.api.routes import AuthContext, _require_scope, require_auth
from app.data.notifications import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    list_notifications,
    mark_all_read,
    mark_read,
    visible_to,
)

logger = logging.getLogger(__name__)

__all__ = [
    "MarkAllReadResponse",
    "MarkReadResponse",
    "NotificationRow",
    "NotificationsResponse",
    "mount",
    "notifications_router",
]

notifications_router = APIRouter()

#: How often the stream emits an SSE **comment** (``: ping - <timestamp>``) when nothing
#: is happening. Fifteen seconds because the idle timeouts that kill a quiet
#: ``text/event-stream`` in practice — nginx's ``proxy_read_timeout`` at 60s, several
#: cloud load balancers at 30-60s — are all comfortably above it, and because a comment
#: is invisible to ``EventSource``: the browser never sees a message, the proxy sees
#: traffic. Delivered by ``sse_starlette``'s own ``ping``, which is the house library's
#: mechanism rather than a hand-rolled timer racing the consumer for the socket.
PING_SECONDS = 15


class NotificationRow(BaseModel):
    """One durable alert, exactly as the bell renders it.

    Deliberately snake_case on the wire — this is the contract the frontend was built
    against in parallel, and an alias layer here would have made the two disagree on the
    one field a reader actually keys on.

    The row carries **no ``tenant_id`` and no ``user_id``**. Those are how the server
    decides who may see it (:func:`app.data.notifications.scope_predicate`); a client
    that could read them is a client that could be tempted to filter with them, and a
    filter in the browser is not a boundary.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Opaque row id; also the SSE frame's identity.")
    kind: str = Field(
        description=(
            "job.succeeded | job.failed | approval.awaiting | budget.exceeded | "
            "sla.auto_decided — a <subject>.<event> name, never a screen name."
        )
    )
    severity: str = Field(description="info | warning | critical.")
    title: str = Field(description="The short line, e.g. 'Ingest finished'.")
    body: str = Field(
        description="One sentence naming the thing, e.g. 'policy-4.pdf ingested — 12 chunks.'"
    )
    entity_ref: str | None = Field(
        default=None, description="What it is about: 'job:21', 'document:23'."
    )
    href: str | None = Field(
        default=None,
        description=(
            "Portal-relative target: '<section>' or '<section>?<param>=<id>', e.g. "
            "'jobs?document=25'. The reader resolves it against its own portal — one "
            "row is visible to several portals at once, so no '/app/<portal>' prefix "
            "is sent."
        ),
    )
    created_at: str = Field(description="ISO 8601 UTC.")
    read_at: str | None = Field(default=None, description="ISO 8601 UTC, or null.")


class NotificationsResponse(BaseModel):
    """Body for ``GET /notifications`` — the page, and the badge.

    ``unread`` is counted over the caller's whole scope with no ``LIMIT``, not over
    ``rows``. A badge that saturated at the page size would under-report exactly when a
    tenant most needed it to be right.
    """

    model_config = ConfigDict(extra="forbid")

    rows: list[NotificationRow] = Field(default_factory=list)
    unread: int = Field(default=0, description="Unread notifications in the caller's scope.")


class MarkReadResponse(BaseModel):
    """Body for ``POST /notifications/{id}/read``."""

    model_config = ConfigDict(extra="forbid")

    id: str
    read: bool = True


class MarkAllReadResponse(BaseModel):
    """Body for ``POST /notifications/read-all``."""

    model_config = ConfigDict(extra="forbid")

    marked: int = Field(description="How many rows this call flipped; 0 is normal.")


def _scope_of(auth: AuthContext) -> tuple[int | None, int | None]:
    """Resolve ``(tenant_id, user_id)`` for a request, from the token and nothing else.

    ``tenant_filter`` turns the sealed :class:`~aegis.retrieval.types.TenantScope` into
    the value the data layer takes: the principal's own tenant, or ``None`` for the
    explicit ``ALL_TENANTS`` authority that only platform staff can hold. An untenanted,
    non-staff principal never reaches here — :func:`app.api.routes._require_scope` has
    already refused it with a 403.

    Args:
        auth: The authenticated principal.

    Returns:
        The tenant scope and the acting user id, in the order the data layer takes them.
    """
    return tenant_filter(_require_scope(auth)), auth.user_id


@notifications_router.get(
    "/notifications", response_model=NotificationsResponse, tags=["notifications"]
)
async def get_notifications(
    unread_only: bool = Query(
        default=False, description="Return only unread rows (the count is unaffected)."
    ),
    limit: int = Query(
        default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT, description="Maximum rows."
    ),
    auth: AuthContext = Depends(require_auth),
) -> NotificationsResponse:
    """Return the caller's notifications, newest first, with the unread total.

    ``require_auth`` rather than a role guard: an alert is addressed to a principal, and
    every role has work that finishes. The narrowing is the sealed scope, not the role.

    A tenant's rows are its own; a row targeted at one user is that user's alone. Both
    halves come from :func:`app.data.notifications.scope_predicate` — see the module
    docstring for why there is exactly one copy of it.
    """
    tenant_id, user_id = _scope_of(auth)
    rows, unread = await list_notifications(
        tenant_id=tenant_id, user_id=user_id, unread_only=unread_only, limit=limit
    )
    return NotificationsResponse(
        rows=[NotificationRow(**row) for row in rows], unread=unread
    )


@notifications_router.post(
    "/notifications/read-all", response_model=MarkAllReadResponse, tags=["notifications"]
)
async def read_all_notifications(
    auth: AuthContext = Depends(require_auth),
) -> MarkAllReadResponse:
    """Mark every unread notification in the caller's scope read.

    Declared **above** ``/notifications/{id}/read`` for readability only — the two
    cannot collide (three path segments against two), but a reader scanning this file
    should meet the literal path before the parameterised one.
    """
    tenant_id, user_id = _scope_of(auth)
    return MarkAllReadResponse(
        marked=await mark_all_read(tenant_id=tenant_id, user_id=user_id)
    )


@notifications_router.post(
    "/notifications/{notification_id}/read",
    response_model=MarkReadResponse,
    tags=["notifications"],
)
async def read_notification(
    notification_id: str,
    auth: AuthContext = Depends(require_auth),
) -> MarkReadResponse:
    """Mark one notification read.

    **404, never 403, for a row outside the caller's scope.** A 403 would confirm that
    another tenant's notification id is real, which is a working oracle for enumerating
    them; "no such notification" is both true from this caller's point of view and
    useless to an attacker. The scope terms live in the ``UPDATE``'s own ``WHERE`` (see
    :func:`app.data.notifications.mark_read`), so the wrong tenant's id matches nothing
    rather than being loaded and then refused.

    Raises:
        HTTPException: 404 when no such notification exists in the caller's scope.
    """
    tenant_id, user_id = _scope_of(auth)
    if not await mark_read(notification_id, tenant_id=tenant_id, user_id=user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such notification."
        )
    return MarkReadResponse(id=notification_id, read=True)


@notifications_router.get("/notifications/stream", tags=["notifications"])
async def stream_notifications(
    auth: AuthContext = Depends(require_auth),
) -> EventSourceResponse:
    """Push notifications to this principal as they are written, over SSE.

    What arrives, and what does not
    -------------------------------

    Only what is published **while this connection is held**. There is no replay on
    connect: the backlog is ``GET /notifications``, which the frontend calls separately,
    and a stream that also replayed would double every alert on the load path and
    re-toast history on every reconnect.

    Each frame is ``event: notification`` with one :class:`NotificationRow` as its
    ``data``. Between frames the stream emits an SSE **comment** every
    :data:`PING_SECONDS` seconds, so an idle connection keeps producing bytes and the
    proxies that close a quiet ``text/event-stream`` (nginx at 60s by default) do not.
    A comment is not a message: ``EventSource`` never surfaces it, so the heartbeat costs
    the frontend nothing.

    One opening ``event: ready`` frame reports which transport is behind this stream —
    ``{"mode": "redis"}`` when notifications cross process boundaries, ``in-process``
    when Redis was unreachable and this connection can only hear what this interpreter
    published. That is deliberately on the wire rather than only in a log: "the alert
    never arrived" and "the alert arrived in another process" are the same symptom, and
    an operator holding the stream open can now tell them apart.

    The scope is sealed before the generator is built
    -------------------------------------------------

    ``tenant_id`` and ``user_id`` are resolved from the bearer token *here*, in the
    handler, and closed over. Resolving them inside the loop would re-read a principal
    whose token may since have been reissued with a different tenant; sealing them at
    connect makes the stream's authority exactly the authority the connection was opened
    with, and :func:`app.data.notifications.visible_to` applies it to every frame.
    """
    from app.notifications import get_bus  # noqa: PLC0415 - lazy: keeps the bus out of import

    tenant_id, user_id = _scope_of(auth)
    bus = get_bus()

    async def event_source() -> AsyncIterator[ServerSentEvent]:
        async with bus.subscribe() as queue:
            yield ServerSentEvent(
                event="ready", data=json.dumps({"mode": bus.mode})
            )
            while True:
                payload = await queue.get()
                try:
                    envelope = json.loads(payload)
                except ValueError:
                    # A malformed frame is somebody else's bug, not this stream's death.
                    logger.warning("Discarded a malformed notification envelope.")
                    continue
                if not visible_to(envelope, tenant_id, user_id):
                    continue
                yield ServerSentEvent(
                    event="notification", data=json.dumps(envelope.get("row", {}))
                )

    return EventSourceResponse(event_source(), ping=PING_SECONDS)


def mount(target: APIRouter) -> None:
    """Attach this module's routes to ``target``, skipping any already present.

    Idempotent for the same reason :func:`app.api.routes_seats.mount` is: mounting twice
    puts a second, shadowed copy of every handler in the served table, which is invisible
    at runtime and confusing exactly where the route-coverage test reads.
    """
    existing = {
        (route.path, frozenset(getattr(route, "methods", ()) or ()))
        for route in target.routes
    }
    for route in notifications_router.routes:
        key = (route.path, frozenset(getattr(route, "methods", ()) or ()))
        if key not in existing:
            target.routes.append(route)
