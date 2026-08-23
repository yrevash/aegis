"""The durable alert inbox's data layer — write once, scope everywhere, push after.

Everything in this platform used to be **pull**: a tenant learned that their hundred
documents had finished ingesting by opening the jobs screen and looking. This module is
the write and read side of the fix. :class:`app.data.models.Notification` is the row;
:mod:`app.notifications` is the fan-out; :mod:`app.api.routes_notifications` is the HTTP
surface. Nothing else may write this table.

Two rules hold this module together, and both are load-bearing.

**Durable first, pushed second.** :func:`emit` inserts and commits, and only then hands
the envelope to the bus. If the commit wrote no row — because the ``dedupe_key`` already
existed, i.e. this is a replay — nothing is published at all. There is no path here that
pushes a frame for a notification that is not in Postgres, so "I was disconnected" and
"I refreshed" give the same answer, and a Temporal replay that re-runs a committed
activity produces neither a second row nor a second toast.

**One predicate, used by every reader.** :func:`scope_predicate` is the single spelling
of *what a principal may see*, and the list, the unread count, both mark-read writes and
the SSE filter in :mod:`app.api.routes_notifications` all go through it. That is
deliberate: the failure this subsystem could most easily ship is a stream that leaks
what the list correctly hides, and two copies of a predicate is exactly how that
happens. The scope itself is resolved from the bearer token
(``AuthContext.tenant_scope()`` via ``_require_scope``) and never from a query
parameter.

**``href`` is portal-relative, and the reader resolves it.** The column keeps its name
and its place in the wire contract, but not its old meaning. It used to be an absolute
path — ``/app/tenant_admin/jobs`` — and that was a link only one of five portals could
follow. It is the *scope* that makes it so: a tenant-scoped row is visible to that
tenant's ``tenant_admin``, ``ai_team`` and ``client`` alike, and platform staff
(``admin``, ``devops``) see every tenant's, so the emitter has four readers it named the
wrong portal for and one it named the right one for by luck. The console's route guard
does not error on a section a session may not enter; it redirects home, silently, which
is how a bell that "worked" led four readers out of five back to their own dashboard.

So the value written here is ``<section>`` or ``<section>?<param>=<id>`` — the screen
that shows the entity and the entity itself, with no ``/app/<portal>`` prefix:

* ``jobs?document=25`` — that document's ingest, on whichever portal mounts ``jobs``.
* ``approvals?approval=<id>`` — that gate.
* ``governance`` — a screen with no entity; the cap is the tenant's own.

The emitter knows which screen shows the thing and which thing it is. It cannot know
who will read the row, so it writes neither a portal nor a guess at one. The browser
resolves ``/app/{viewer's portal}/{target}`` against the section catalogue it already
owns (``web/src/lib/notificationTarget.ts`` over ``web/src/lib/portal.ts``), and where
the viewer's portal does not mount that section it renders the alert without a link
rather than one that bounces. A value beginning with ``/app/`` is still understood —
rows written before this change are read by stripping the stale portal segment — so
nothing already in the table became unreadable.

The app-level predicate is not belt-and-braces here, it is the belt. The
``notifications`` table is registered in
:data:`aegis.governance.rls._TENANT_SCOPED_TABLES` and gets a ``tenant_isolation``
policy at boot, but this deployment runs the **fail-open** flavour of that predicate
(``RLS_FAIL_CLOSED`` ships false — see :data:`aegis.governance.rls._TENANT_ISOLATION_PREDICATE`),
so a path that bound no scope would be filtered by nothing. Every read below binds the
scope *and* carries the ``WHERE``.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Notification, NotificationSeverity
from .session import get_sessionmaker, set_tenant_scope

logger = logging.getLogger(__name__)

__all__ = [
    "emit",
    "list_notifications",
    "mark_all_read",
    "mark_read",
    "notify_budget_exceeded",
    "notify_extraction_detected",
    "scope_predicate",
    "to_wire",
    "visible_to",
]

#: The cap on ``GET /v1/notifications?limit=``. A bell menu that asks for ten thousand
#: rows is a mistake, not a use case, and an unbounded limit on a tenant-scoped table is
#: how one client turns a shared database into everyone's outage.
MAX_LIMIT = 200

#: The default page size, and the number the frontend gets when it asks for nothing.
DEFAULT_LIMIT = 50


def _now() -> datetime:
    """Return the current UTC instant as an aware datetime.

    Every timestamp column here is ``timestamptz`` (via ``aegis.data.UtcDateTime``), so
    a naive value is rejected by asyncpg at bind time rather than silently reinterpreted
    — the failure that used to kill the SLA sweeper once per cycle.
    """
    return datetime.now(UTC)


def _iso(ts: datetime | None) -> str | None:
    """Render a timestamp as an unambiguous ISO 8601 UTC string, or ``None``.

    A value read back from ``func.now()`` can arrive naive on some drivers; it is
    treated as UTC, which is the meaning this codebase assigns to a stored naive
    timestamp everywhere it reads one.
    """
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).isoformat()


def to_wire(row: Notification) -> dict[str, Any]:
    """Project a :class:`Notification` onto the wire contract the frontend consumes.

    **This is the only projection**, and both transports use it: the REST list and the
    SSE frame are built here, so a field that exists on one and not the other is not a
    state the code can reach. It deliberately omits ``tenant_id``, ``user_id`` and
    ``dedupe_key`` — those are how the server decides who may see the row, and a client
    that could read them would be a client that could be tempted to filter on them.

    Args:
        row: The ORM row.

    Returns:
        ``{id, kind, severity, title, body, entity_ref, href, created_at, read_at}``.
    """
    return {
        "id": row.id,
        "kind": row.kind,
        "severity": row.severity,
        "title": row.title,
        "body": row.body,
        "entity_ref": row.entity_ref,
        "href": row.href,
        "created_at": _iso(row.created_at) or _iso(_now()),
        "read_at": _iso(row.read_at),
    }


def scope_predicate(
    tenant_id: int | None, user_id: int | None
) -> list[ColumnElement[bool]]:
    """Return the ``WHERE`` terms that decide what this principal may see. One copy.

    The rule, in two halves:

    * **The tenant half.** ``tenant_id`` comes from
      :meth:`app.api.routes.AuthContext.tenant_scope` through ``_require_scope``, never
      from the request body or a query parameter. ``None`` means the caller already
      established platform-wide authority (``ALL_TENANTS``) — it is not reachable by
      omission, because ``_scope_tenant`` cannot produce it without that authority — so
      no tenant term is added. Any other value pins the read to that tenant.
    * **The user half, which always applies.** ``user_id IS NULL OR user_id = :me``: a
      row with no user is the tenant's (an ingest finished, a gate is waiting), and a
      row with one is that person's alone. It is applied even for a platform-wide read,
      because "may see every tenant" is not "may read another individual's inbox".

    Passing ``user_id=None`` for a principal with no user row therefore yields *only*
    the tenant-wide rows, which is the correct conservative answer rather than an error.

    Args:
        tenant_id: The tenant to pin to, or ``None`` for an established platform read.
        user_id: The acting ``users.id``, or ``None``.

    Returns:
        The ``WHERE`` terms, to splat into a ``select`` or ``update``.
    """
    terms: list[ColumnElement[bool]] = []
    if tenant_id is not None:
        terms.append(Notification.tenant_id == tenant_id)
    terms.append(
        or_(Notification.user_id.is_(None), Notification.user_id == user_id)
    )
    return terms


def visible_to(
    envelope: dict[str, Any], tenant_id: int | None, user_id: int | None
) -> bool:
    """Say whether one published envelope may reach this principal's SSE stream.

    The in-memory twin of :func:`scope_predicate`, and it exists because the stream
    filters envelopes rather than querying rows. The two are kept adjacent, in one file,
    with one test asserting they agree — a stream that leaks what the list hides is the
    single worst outcome available to this subsystem, and it is exactly what "the SQL
    was fixed and the filter was not" produces.

    Args:
        envelope: ``{"tenant_id": ..., "user_id": ..., "row": {...}}`` as published.
        tenant_id: The subscriber's resolved tenant, ``None`` for platform-wide.
        user_id: The subscriber's ``users.id``, or ``None``.

    Returns:
        ``True`` when this principal is entitled to the frame.
    """
    if tenant_id is not None and envelope.get("tenant_id") != tenant_id:
        return False
    target = envelope.get("user_id")
    return target is None or target == user_id


async def emit(
    *,
    tenant_id: int | None,
    kind: str,
    title: str,
    body: str,
    severity: str = NotificationSeverity.INFO,
    user_id: int | None = None,
    entity_ref: str | None = None,
    href: str | None = None,
    dedupe_key: str | None = None,
) -> dict[str, Any] | None:
    """Record one alert durably, then push it — never raising, whatever goes wrong.

    This is the whole emitter surface, and every call site in the platform is one line
    of it. It is deliberately impossible for this function to fail the thing it is
    reporting on: the body is wrapped, the failure is logged with the alert it could not
    deliver, and the caller continues. An ingest that finished successfully must not be
    recorded as failed because a notification insert hit a lock, and a gate must not go
    un-enqueued because Redis was restarting.

    **It opens its own session rather than joining the caller's.** That is the property
    that makes the paragraph above true rather than aspirational: a failed INSERT on a
    shared session poisons the caller's transaction, so ``finish_ingest`` would roll back
    the very close-out it had just committed to. The cost of the separate transaction is
    a narrow window — if the caller's transaction rolls back *after* this returns, the
    alert describes something that did not happen. That window is one ``COMMIT`` wide at
    every current call site (the emit is the last statement before the commit), and the
    trade is deliberate: a spurious alert is a wrong toast, a poisoned transaction is a
    lost ingest.

    Idempotency is the database's, not the caller's
    -----------------------------------------------

    ``dedupe_key`` is inserted ``ON CONFLICT DO NOTHING`` against the unique index on
    that column, and the insert ``RETURNING`` no row is how a duplicate is detected. So
    a Temporal activity that commits, dies, and replays in a fresh worker writes one row
    and publishes one frame — even though the call sites also carry their own guards,
    because a guard in one caller protects only that caller.

    Args:
        tenant_id: The owning tenant. ``None`` is a platform-level notice that no tenant
            can read (the standard NULL-is-invisible predicate applies).
        kind: A :class:`~app.data.models.NotificationKind` value.
        title: The short line a bell menu renders, e.g. ``"Ingest finished"``.
        body: One sentence naming the thing, e.g. ``"policy-4.pdf ingested — 12 chunks."``
        severity: A :class:`~app.data.models.NotificationSeverity` value.
        user_id: Target one user inside the tenant; ``None`` targets everyone in it.
        entity_ref: What it is about — ``"job:21"``, ``"document:23"``.
        href: **Portal-relative** target — ``<section>`` or ``<section>?<param>=<id>``,
            e.g. ``jobs?document=25``. Never an absolute ``/app/<portal>/…`` path: the
            emitter cannot know which of the five portals will read the row. See the
            module docstring.
        dedupe_key: The idempotency key. ``None`` means "report this every time".

    Returns:
        The wire row that was written and published, or ``None`` when nothing was —
        either because it was a duplicate, or because the write failed. The two are
        distinguishable in the log, not in the return value: no caller acts on either.
    """
    row_id = uuid.uuid4().hex
    created = _now()
    values = {
        "id": row_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "kind": str(kind),
        "severity": str(severity),
        "title": title,
        "body": body,
        "entity_ref": entity_ref,
        "href": href,
        "created_at": created,
        "read_at": None,
        "dedupe_key": dedupe_key,
    }
    try:
        async with get_sessionmaker()() as session:
            # Bind the scope even though this is a write: under the fail-closed posture
            # an unbound INSERT is refused by the policy's WITH CHECK, and a notification
            # subsystem that only works while RLS is fail-open would be a trap laid for
            # whoever flips the flag.
            await set_tenant_scope(session, tenant_id)
            written = await _insert_once(session, values)
            await session.commit()
    except Exception:  # noqa: BLE001 - see the docstring: an alert may never fail its subject
        logger.warning(
            "Notification NOT recorded (kind=%s tenant=%s entity=%s): %s. The event it "
            "describes is unaffected — this is the alert failing, not the work.",
            kind,
            tenant_id,
            entity_ref,
            title,
            exc_info=True,
        )
        return None
    if not written:
        logger.debug(
            "Notification deduplicated on key %r (kind=%s tenant=%s); no row and no "
            "frame — this is a replay.",
            dedupe_key,
            kind,
            tenant_id,
        )
        return None

    wire = {
        "id": row_id,
        "kind": str(kind),
        "severity": str(severity),
        "title": title,
        "body": body,
        "entity_ref": entity_ref,
        "href": href,
        "created_at": _iso(created),
        "read_at": None,
    }
    # Pushed only now, and never inside the transaction above: a frame published for a
    # row that then rolled back is an alert nobody can find afterwards.
    try:
        from app.notifications import get_bus  # noqa: PLC0415 - lazy: keeps this module infra-free

        await get_bus().publish(
            {"tenant_id": tenant_id, "user_id": user_id, "row": wire}
        )
    except Exception:  # noqa: BLE001 - the row is committed; delivery is best-effort
        logger.warning(
            "Notification %s was recorded but could not be published; it will be read "
            "on the next GET /v1/notifications rather than arriving live.",
            row_id,
            exc_info=True,
        )
    return wire


#: Where a tenant goes to see the cap that stopped their work, and who can raise it.
#: Portal-relative, per the ``href`` contract in this module's docstring. It carries no
#: entity because the cap is the tenant's own — the screen *is* the thing.
_GOVERNANCE_TARGET = "governance"


async def notify_budget_exceeded(
    *, tenant_id: int | None, reason: str, because: str, user_id: int | None = None
) -> None:
    """Tell a tenant that a budget cap refused work — at most once an hour.

    One helper rather than a line at each site, because both call sites
    (:func:`app.api.routes._require_budget`, the pre-flight gate, and
    :func:`app.jobs.activities.run_stage`'s mid-stage refusal) must agree on the *rate*,
    and that is the only interesting decision here.

    **Why the hourly bucket.** A cap does not stop refusing once it has refused: a tenant
    over their USD ceiling is refused on every request, and an emitter without a rate
    limit would write one row per refused request. Ten seconds of a retrying client would
    bury every other alert in the bell and cost more storage than the work it refused.
    So the dedupe key carries the hour, and the unique index does the rate-limiting in
    SQL: the first refusal in an hour writes a row and pushes a frame, and the rest
    conflict and return silently. An hour is chosen because that is the granularity at
    which somebody can act on it — the fix is an administrator raising the cap or the
    window rolling over, and neither happens in minutes.

    The alert targets the **tenant**, not the acting user, even when a user is known.
    A cap that stops work stops it for everyone under it, and the person who happened to
    make the request that hit the ceiling is rarely the person who can raise it.

    Args:
        tenant_id: The tenant whose cap bound.
        reason: The cap's own message — which limit, and what it was measured against.
        because: One clause naming the work that was refused, so the alert says what was
            lost and not only that something was.
        user_id: The acting user, recorded in the sentence and *not* used to narrow the
            audience. See above.
    """
    hour = _now().strftime("%Y-%m-%dT%H")
    await emit(
        tenant_id=tenant_id,
        kind="budget.exceeded",
        severity=NotificationSeverity.CRITICAL,
        title="Budget cap reached",
        body=f"{reason} Work refused: {because}.",
        entity_ref=f"tenant:{tenant_id}",
        href=_GOVERNANCE_TARGET,
        dedupe_key=f"budget.exceeded:{tenant_id}:{hour}",
    )
    if user_id is not None:
        logger.info(
            "Budget refusal notified to tenant %s (triggered by user %s): %s",
            tenant_id,
            user_id,
            because,
        )


#: The screen that shows the security posture and the audit trail — where somebody goes
#: after reading an extraction alert.
_SECURITY_TARGET = "governance"


async def notify_extraction_detected(
    *,
    tenant_id: int | None,
    principal_id: str,
    reason: str,
    user_id: int | None = None,
) -> None:
    """Tell a tenant that one of their principals was refused for query enumeration.

    The alert half of MITRE ATLAS AML.T0024's extraction control
    (:class:`aegis.security.ExtractionMonitor`). The refusal already reached the caller
    as a 429; this reaches the **administrator**, who is the only person who can decide
    whether that principal is an attacker, a compromised account, or an integration
    somebody wired up on Friday. A detector nobody can see is not a control.

    Rate-limited exactly the way :func:`notify_budget_exceeded` is, and for the same
    reason: a sweeping principal is refused on every subsequent query, so an unlimited
    emitter would write one row per refused query and bury the bell under the alert it
    was trying to raise. The dedupe key carries the tenant, the principal and the hour,
    and the unique index does the rate-limiting in SQL — so a principal sweeping for
    twenty minutes produces one alert, and a *different* principal in the same tenant
    still produces its own.

    ``severity`` is ``warning`` and not ``critical``. ``critical`` in this platform means
    "this stopped work" — a budget cap that halted a tenant's whole pipeline. This
    stopped one principal, on a heuristic, and it decays by itself; calling that critical
    would train the reader to discount the level that actually matters.

    Args:
        tenant_id: The tenant the principal belongs to. The alert is scoped to them and
            to nobody else — a query pattern is one tenant's operational detail.
        principal_id: The principal that was refused, named so the alert is actionable.
        reason: The monitor's own sentence — what it observed, over what window.
        user_id: The acting user id where known. Recorded in the log line and **not**
            used to narrow the audience: the person who can act on this is an
            administrator, not the principal that was refused.
    """
    hour = _now().strftime("%Y-%m-%dT%H")
    await emit(
        tenant_id=tenant_id,
        kind="security.extraction_detected",
        severity=NotificationSeverity.WARNING,
        title="Query enumeration refused",
        body=f"Principal {principal_id!r} was refused. {reason}",
        entity_ref=f"principal:{principal_id}",
        href=_SECURITY_TARGET,
        dedupe_key=f"security.extraction:{tenant_id}:{principal_id}:{hour}",
    )
    logger.warning(
        "Extraction pattern refused for tenant %s principal %s (user %s): %s",
        tenant_id,
        principal_id,
        user_id,
        reason,
    )


async def _insert_once(session: AsyncSession, values: dict[str, Any]) -> bool:
    """Insert one notification, refusing a duplicate ``dedupe_key`` in SQL.

    Args:
        session: The scoped session to write on.
        values: The full column mapping.

    Returns:
        ``True`` when a row was written, ``False`` when the unique index refused it.
    """
    if session.get_bind().dialect.name == "postgresql":
        stmt = (
            pg_insert(Notification)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
            .returning(Notification.id)
        )
        return (await session.execute(stmt)).scalar_one_or_none() is not None
    # Any other dialect (a host embedding this on SQLite): the unique index still
    # exists, so a duplicate raises IntegrityError and the caller's broad handler turns
    # it into "not recorded". Correct, one log line noisier, and not a path this
    # deployment takes — the backend suite and production are both PostgreSQL.
    session.add(Notification(**values))
    await session.flush()
    return True


async def list_notifications(
    *,
    tenant_id: int | None,
    user_id: int | None,
    unread_only: bool = False,
    limit: int = DEFAULT_LIMIT,
) -> tuple[list[dict[str, Any]], int]:
    """Return this principal's notifications, newest first, plus the unread count.

    The count is **not** ``len(rows)``. It is a separate ``COUNT`` over the same scope
    with no ``LIMIT``, because the bell's badge answers "how many are waiting" and the
    list answers "what are the most recent fifty" — a badge that silently saturated at
    the page size would under-report exactly when it mattered.

    Args:
        tenant_id: The resolved tenant scope, or ``None`` for an established
            platform-wide read.
        user_id: The acting ``users.id``, or ``None``.
        unread_only: Restrict the rows (never the count) to unread ones.
        limit: Maximum rows; clamped to ``[1, MAX_LIMIT]``.

    Returns:
        ``(rows, unread)`` — the wire rows and the unread total in scope.
    """
    capped = max(1, min(int(limit), MAX_LIMIT))
    terms = scope_predicate(tenant_id, user_id)
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, tenant_id)
        stmt = (
            select(Notification)
            .where(*terms)
            .order_by(Notification.created_at.desc(), Notification.id.asc())
            .limit(capped)
        )
        if unread_only:
            stmt = stmt.where(Notification.read_at.is_(None))
        rows = (await session.execute(stmt)).scalars().all()
        unread = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(Notification)
                    .where(*terms, Notification.read_at.is_(None))
                )
            ).scalar_one()
        )
    return [to_wire(row) for row in rows], unread


async def mark_read(
    notification_id: str, *, tenant_id: int | None, user_id: int | None
) -> bool:
    """Mark one notification read, if this principal may see it at all.

    The scope terms are in the ``UPDATE``'s own ``WHERE`` rather than checked first in
    Python: a read-then-write would decide visibility against a row it had already
    loaded, which is the shape that lets a cross-tenant id be *read* on the way to being
    refused. Here the wrong tenant's id simply matches nothing.

    Idempotent — marking an already-read row succeeds and does not re-stamp ``read_at``,
    so "when did they first see it" survives a double click.

    Args:
        notification_id: The row to mark.
        tenant_id: The resolved tenant scope.
        user_id: The acting ``users.id``.

    Returns:
        ``True`` when the row exists inside this principal's scope (whether or not this
        call was the one that flipped it), ``False`` when it does not — which the route
        turns into a 404 and never into a 403, because saying "forbidden" would confirm
        that another tenant's id is real.
    """
    terms = scope_predicate(tenant_id, user_id)
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, tenant_id)
        await session.execute(
            update(Notification)
            .where(
                Notification.id == notification_id,
                Notification.read_at.is_(None),
                *terms,
            )
            .values(read_at=_now())
        )
        visible = (
            await session.execute(
                select(Notification.id).where(
                    Notification.id == notification_id, *terms
                )
            )
        ).scalar_one_or_none()
        await session.commit()
    return visible is not None


async def mark_all_read(*, tenant_id: int | None, user_id: int | None) -> int:
    """Mark every unread notification in this principal's scope read.

    Args:
        tenant_id: The resolved tenant scope.
        user_id: The acting ``users.id``.

    Returns:
        How many rows this call flipped. Zero is a normal answer (an already-clear
        inbox), not an error.
    """
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, tenant_id)
        result = await session.execute(
            update(Notification)
            .where(
                Notification.read_at.is_(None),
                *scope_predicate(tenant_id, user_id),
            )
            .values(read_at=_now())
        )
        await session.commit()
        return int(result.rowcount or 0)
