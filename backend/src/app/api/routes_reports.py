"""Downloadable reports — the record, as a file that leaves the browser (§7.12).

Every governance surface in this product could be *looked at* and none of it could be
*taken away*. That is not a convenience gap. An auditor asks for the trail over a
quarter, a finance reviewer asks for caps against consumption, and the answer was a
screenshot or a `psql` session — so the evidence this platform spends its whole design
budget producing stopped at the tab it was rendered in.

Four exports, and each one is generated **from the accessor the screen already reads**:

``GET /reports/audit.csv``
    The audit trail, streamed from ``audit_log`` in keyset pages
    (:func:`aegis.reports.stream_audit_rows`), filtered exactly as the audit screen
    filters it.
``GET /reports/tenant.csv``
    The roster — :func:`aegis.governance.list_users`, the same rows
    ``GET /admin/users`` returns.
``GET /reports/budget.csv``
    Caps against consumption from :func:`aegis.governance.budget_status`, which is the
    **same ledger summation the gateway enforcer runs**, so the report and the cap that
    blocks a call cannot disagree.
``GET /reports/forecast.csv``
    The projection, with its caveats as columns rather than as a footnote: requested
    coverage beside *achieved* coverage, the interval method, and
    ``cumulative_bounds_are_calibrated`` on every row. A CSV that dropped those would
    turn a carefully honest surface into a misleading spreadsheet the moment it left
    the browser.

**Scope comes from the sealed :meth:`AuthContext.tenant_scope`, never from the URL.**
An export is the highest-leverage place in the product to leak a whole tenant at once,
so every handler resolves its filter through :func:`_scope_tenant`: a tenant admin
asking for another tenant's rows is refused with a 403, and an omitted ``tenant_id``
means *their own tenant*, never *all of them*.

**How a browser is given a file, and why it is not a blob.** These routes send the
bytes with ``Content-Disposition: attachment``; the browser writes them to disk while
they arrive. The obvious alternative — fetch the CSV with the bearer, build a
``Blob``, click a synthetic ``<a download>`` — buffers the whole export in the tab's
memory, defeats the streaming above, and is inert inside a sandboxed frame. But a
navigation cannot carry an ``Authorization`` header, so the console first mints a
**download ticket** (``POST /reports/tickets``): a 60-second JWT naming one report and
one principal. It is deliberately *not* a bearer — it carries no ``role`` claim, so
:func:`~aegis.governance.security.decode_access_token` refuses it and it authenticates
nothing else in the product. The scope is re-derived from the principal it names, so a
stolen ticket can export exactly what its owner could have exported anyway, for one
minute, and nothing more. ``curl`` with a normal bearer works on the same routes.

**Every export writes its own ``report.export`` audit row** carrying the filters and
the resolved scope, before a byte is streamed — an export of the audit trail that is
not itself audited is the first hole a procurement reviewer finds, and recording it
up front means a download the operator cancels halfway is still on the record.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Iterable, Mapping
from datetime import UTC, datetime, timedelta

import jwt
from aegis.forecast import BudgetBurndown, ForecastError, ForecastResult
from aegis.governance import budget_status, list_tenants, list_users
from aegis.governance.enforcement import list_budgets
from aegis.governance.models import AuditLog
from aegis.reports import (
    AUDIT_COLUMNS,
    BOM,
    CRLF,
    CSV_MEDIA_TYPE,
    ReportMeta,
    audit_cells,
    content_disposition,
    csv_row,
    preamble,
    report_filename,
    stream_audit_rows,
    trailer,
)
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes import (
    AuthContext,
    _bearer,
    _persona_for,
    _safe_audit,
    _scope_tenant,
    require_auth,
)
from app.api.schemas import Role
from app.config import get_settings
from app.core.security import PLATFORM_ADMIN, TENANT_ADMIN
from app.data.session import get_sessionmaker, set_tenant_scope

__all__ = [
    "ReportTicketRequest",
    "ReportTicketResponse",
    "mount",
    "reports_router",
    "require_report_download",
]

logger = logging.getLogger(__name__)

reports_router = APIRouter()

#: The four reports, id → human title. The id is the filename stem and the ticket's
#: subject, so it is a closed set: an unknown id is a 404 rather than a guess.
REPORTS: dict[str, str] = {
    "audit": "Audit trail",
    "tenant": "Tenant roster",
    "budget": "Budget caps and consumption",
    "forecast": "Spend forecast",
}

#: How long a download ticket lives. Long enough for a click to become a navigation,
#: short enough that a ticket in a proxy log or a browser history is spent.
TICKET_TTL_SECONDS = 60

#: The ticket's audience marker. Checked on redemption so a token minted for anything
#: else — now or later — cannot be presented here.
_TICKET_PURPOSE = "aegis.report.download"

#: What a principal who may not take this record away is told.
_NOT_A_REPORT_READER = (
    "This export is available to an administrator (and the audit trail also to the "
    "devops role). Your role may read its own surfaces in the console but not "
    "download the platform's records."
)

#: The one sentence every ledger-derived export has to carry. ``usage_ledger`` has no
#: outcome column: a call that was refused before the gateway leaves no row at all, and
#: a call that failed after the model answered is indistinguishable from one that
#: succeeded. Any "error rate" computed from these rows is 0% by construction, which is
#: why no export here contains one.
_LEDGER_OUTCOME_CAVEAT = (
    "The usage ledger records no outcome for a call: a request refused before the "
    "gateway writes no row, and a call that failed afterwards looks exactly like one "
    "that succeeded. These figures are therefore spend and volume only — no success "
    "rate or error rate can be derived from them."
)


# ─────────────────────────────────────────────────────────────────────────────
# Who may take a record away
# ─────────────────────────────────────────────────────────────────────────────


def _may_read(auth: AuthContext, report: str) -> bool:
    """Whether ``auth`` may download ``report``.

    Mirrors the guard on the surface each report is generated from, so an export can
    never be a way around the screen's own RBAC: the audit trail follows
    ``GET /audit`` (admin **or** devops — the DevOps portal has an Audit tab), and the
    roster, the caps and the forecast follow ``require_tenant_admin``.

    Args:
        auth: The authenticated principal.
        report: The report id.

    Returns:
        True when the principal's role admits this report.
    """
    if report == "audit":
        return auth.role in (Role.ADMIN, Role.DEVOPS)
    return auth.fine_role in (PLATFORM_ADMIN, TENANT_ADMIN)


def _known_report(report: str) -> str:
    """Return ``report`` if it is one of :data:`REPORTS`, else raise a 404.

    Args:
        report: The candidate id.

    Returns:
        The same id.

    Raises:
        HTTPException: 404 naming every report that does exist.
    """
    if report not in REPORTS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown report {report!r}. Available: {', '.join(sorted(REPORTS))}.",
        )
    return report


# ─────────────────────────────────────────────────────────────────────────────
# The download ticket
# ─────────────────────────────────────────────────────────────────────────────


def _mint_ticket(auth: AuthContext, report: str) -> str:
    """Mint a short-lived ticket authorising one download for one principal.

    The claim set is deliberately *not* an access token's. There is no ``role`` claim,
    which is the claim :func:`~aegis.governance.security.decode_access_token` requires,
    so this string is rejected by ``require_auth`` on every other route in the product
    — see ``tests/api/test_reports_export.py``.

    Args:
        auth: The principal the ticket acts as.
        report: The single report it may fetch.

    Returns:
        The encoded ticket.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "purpose": _TICKET_PURPOSE,
        "report": report,
        "username": auth.username,
        "fine_role": auth.fine_role,
        "coarse_role": auth.role.value,
        "tenant_id": auth.tenant_id,
        "user_id": auth.user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=TICKET_TTL_SECONDS)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _principal_from_ticket(ticket: str, report: str) -> AuthContext:
    """Resolve the principal a ticket names, or refuse it.

    Args:
        ticket: The encoded ticket from the query string.
        report: The report the request is actually for.

    Returns:
        The principal, rebuilt exactly as ``require_auth`` would have built it.

    Raises:
        HTTPException: 401 when the ticket is expired, tampered with, minted for a
            different purpose, or minted for a different report. A ticket for
            ``budget`` presented on ``/reports/audit.csv`` is a widening attempt, not
            a mistake worth being generous about.
    """
    settings = get_settings()
    try:
        claims = jwt.decode(
            ticket, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This download link has expired. Press the download button again.",
        ) from exc
    if claims.get("purpose") != _TICKET_PURPOSE or claims.get("report") != report:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This download link is not valid for this report.",
        )
    try:
        coarse = Role(str(claims.get("coarse_role")))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This download link is not valid for this report.",
        ) from exc
    tenant_id = claims.get("tenant_id")
    user_id = claims.get("user_id")
    if not isinstance(tenant_id, int | None) or not isinstance(user_id, int | None):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This download link is not valid for this report.",
        )
    return AuthContext(
        username=str(claims.get("username")),
        role=coarse,
        persona=_persona_for(coarse),
        fine_role=str(claims.get("fine_role")),
        tenant_id=tenant_id,
        user_id=user_id,
    )


async def require_report_download(
    request: Request,
    ticket: str | None = Query(
        default=None,
        description=(
            "A short-lived ticket from POST /reports/tickets. Use this when the "
            "download is a browser navigation, which cannot carry a bearer header."
        ),
    ),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AuthContext:
    """Admit a principal allowed to download the report this request names.

    Two ways in, one outcome: an ``Authorization: Bearer`` header (an API client,
    ``curl``, a test) or a ``?ticket=`` minted seconds earlier for this exact report
    (a browser navigation). Either way the result is an
    :class:`~app.api.routes.AuthContext`, and every scoping decision downstream is
    made from that object rather than from anything else in the URL.

    Args:
        request: The incoming request; its path names the report.
        ticket: The download ticket, when the caller is a navigation.
        credentials: The bearer token, when the caller can set a header.

    Returns:
        The authenticated principal.

    Raises:
        HTTPException: 401 with neither credential, 403 when the role may not read
            this report.
    """
    report = _known_report(request.url.path.rsplit("/", 1)[-1].removesuffix(".csv"))
    auth = _principal_from_ticket(ticket, report) if ticket else require_auth(credentials)
    if not _may_read(auth, report):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=_NOT_A_REPORT_READER
        )
    return auth


class ReportTicketRequest(BaseModel):
    """Body for ``POST /reports/tickets`` — which report the ticket is for."""

    model_config = ConfigDict(extra="forbid")

    report: str = Field(description="One of: audit, tenant, budget, forecast.")


class ReportTicketResponse(BaseModel):
    """A minted ticket and how long the caller has to use it."""

    model_config = ConfigDict(populate_by_name=True)

    ticket: str = Field(description="Append as ?ticket= to the report's CSV route.")
    report: str = Field(description="The single report this ticket authorises.")
    expires_in: int = Field(
        alias="expiresIn", description="Seconds until the ticket stops working."
    )


@reports_router.post(
    "/reports/tickets", response_model=ReportTicketResponse, tags=["reports"]
)
async def mint_report_ticket(
    req: ReportTicketRequest,
    auth: AuthContext = Depends(require_auth),
) -> ReportTicketResponse:
    """Mint a 60-second ticket so a browser navigation can fetch one report.

    The RBAC decision is made **here**, on the same :func:`_may_read` rule the download
    route re-applies when the ticket is redeemed. Minting is not itself an export and
    writes no ``report.export`` row: nothing has left the platform until the file does.
    """
    report = _known_report(req.report)
    if not _may_read(auth, report):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=_NOT_A_REPORT_READER
        )
    return ReportTicketResponse(
        ticket=_mint_ticket(auth, report),
        report=report,
        expires_in=TICKET_TTL_SECONDS,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Shared plumbing: scope labels, the preamble, the response
# ─────────────────────────────────────────────────────────────────────────────


def _scope_label(tenant_id: int | None) -> str:
    """Describe the scope of an export in the words the file will carry.

    Args:
        tenant_id: The resolved filter, ``None`` for a platform-wide read.

    Returns:
        A sentence, not a number: a bare ``None`` in a spreadsheet cell is exactly the
        ambiguity this export exists to remove.
    """
    if tenant_id is None:
        return "All tenants (platform scope)"
    return f"Tenant {tenant_id} only — no other tenant's rows are in this file"


def _scope_slug(tenant_id: int | None) -> str:
    """Return the filename fragment naming the scope.

    Args:
        tenant_id: The resolved filter.

    Returns:
        ``platform`` or ``tenant-<id>``.
    """
    return "platform" if tenant_id is None else f"tenant-{tenant_id}"


def _window_label(since: datetime | None, until: datetime | None) -> str:
    """Describe the time span an export covers.

    Args:
        since: Inclusive lower bound, or ``None``.
        until: Inclusive upper bound, or ``None``.

    Returns:
        A human span, or the honest ``All time`` when neither bound was given.
    """
    if since is None and until is None:
        return "All time — no date filter was applied"
    start = since.isoformat() if since else "the first recorded row"
    end = until.isoformat() if until else "now"
    return f"{start} to {end} (UTC, inclusive)"


def _instant(name: str, raw: str | None) -> datetime | None:
    """Parse an ISO 8601 query parameter, reading a naive value as UTC.

    Args:
        name: The parameter's name, for the error message.
        raw: The raw value.

    Returns:
        The parsed instant, or ``None`` when absent.

    Raises:
        HTTPException: 400 when the value is not ISO 8601.
    """
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"`{name}` must be an ISO 8601 timestamp; got {raw!r}.",
        ) from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _meta(
    report: str,
    *,
    auth: AuthContext,
    tenant_id: int | None,
    window: str,
    source: str,
    generated_at: datetime,
    filters: Mapping[str, str],
    caveats: tuple[str, ...],
) -> ReportMeta:
    """Build the self-describing header block for one export.

    Args:
        report: The report id.
        auth: Who is taking it.
        tenant_id: The resolved scope filter.
        window: The span covered, already in words.
        source: The table or accessor the rows came from.
        generated_at: When this export ran.
        filters: The query parameters that narrowed it.
        caveats: What a reader must know before quoting a number.

    Returns:
        The metadata written above the table.
    """
    return ReportMeta(
        report=report,
        title=REPORTS[report],
        scope=_scope_label(tenant_id),
        window=window,
        source=source,
        generated_at=generated_at,
        exported_by=auth.username,
        exported_by_role=auth.fine_role,
        filters=filters,
        caveats=caveats,
    )


def _streamed(
    report: str,
    *,
    tenant_id: int | None,
    generated_at: datetime,
    body: AsyncIterator[str],
) -> StreamingResponse:
    """Wrap a line generator as a downloadable CSV response.

    Args:
        report: The report id (names the file).
        tenant_id: The resolved scope (names the file).
        generated_at: When the export ran (names the file).
        body: The already-composed lines, including the preamble.

    Returns:
        The streaming response, with the header that makes a browser save it.
    """
    filename = report_filename(
        report, scope=_scope_slug(tenant_id), generated_at=generated_at
    )
    return StreamingResponse(
        body,
        media_type=CSV_MEDIA_TYPE,
        headers={
            "Content-Disposition": content_disposition(filename),
            # The row count is unknown until the last row is read, so no length can be
            # promised; naming the encoding stops a proxy compressing it into one.
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _open(meta: ReportMeta, columns: Iterable[str]) -> Iterable[str]:
    """Yield the opening bytes of every export: BOM, preamble, header row.

    Args:
        meta: What this export is.
        columns: The table's column names.

    Returns:
        The opening lines, in order.
    """
    return (BOM, *preamble(meta), csv_row(columns))


async def _record_export(
    report: str,
    auth: AuthContext,
    *,
    tenant_id: int | None,
    filters: Mapping[str, str],
    delivery: str,
) -> None:
    """Write the ``report.export`` audit row for one download.

    Args:
        report: Which record was taken.
        auth: Who took it.
        tenant_id: The scope it was taken over.
        filters: The parameters that narrowed it.
        delivery: ``ticket`` (a browser navigation) or ``bearer`` (an API client).
    """
    await _safe_audit(
        "report.export",
        auth,
        payload={
            "report": report,
            "scope_tenant_id": tenant_id,
            "filters": dict(filters),
            "delivery": delivery,
        },
        tenant_id=tenant_id,
    )


def _delivery(ticket: str | None) -> str:
    """Name how this download was authorised, for the audit row.

    Args:
        ticket: The ticket parameter, if one was used.

    Returns:
        ``ticket`` or ``bearer``.
    """
    return "ticket" if ticket else "bearer"


def _failed(exc: Exception, report: str) -> str:
    """Return the row that closes a stream which died mid-body.

    Once the first byte is written the status code is spent, so a failure cannot be a
    500 any more. It can still be *visible*: the file ends with an explicit incomplete
    marker instead of the ``End of export`` trailer, which is the difference between a
    reader knowing the export is partial and a reader quoting a truncated file.

    Args:
        exc: What went wrong.
        report: Which export it went wrong in.

    Returns:
        The final CSV row.
    """
    logger.error("The %s export failed mid-stream.", report, exc_info=exc)  # noqa: TRY400
    return csv_row(
        [
            "Export incomplete",
            "The server failed while streaming this file. It is missing rows; do "
            "not use it as evidence. Try the download again.",
        ]
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /reports/audit.csv
# ─────────────────────────────────────────────────────────────────────────────


async def _audit_body(
    meta: ReportMeta,
    *,
    tenant_id: int | None,
    since: datetime | None,
    until: datetime | None,
    actor: str | None,
    action_prefix: str | None,
) -> AsyncIterator[str]:
    """Stream the audit table, a keyset page at a time.

    Args:
        meta: The preamble to write above it.
        tenant_id: The resolved scope filter.
        since: Inclusive lower bound on ``ts``.
        until: Inclusive upper bound on ``ts``.
        actor: Exact actor filter.
        action_prefix: Action prefix filter.

    Yields:
        The whole file, line by line.
    """
    for line in _open(meta, AUDIT_COLUMNS):
        yield line
    written = 0
    try:
        async with get_sessionmaker()() as session:
            await set_tenant_scope(session, tenant_id)
            async for row in stream_audit_rows(
                session,
                tenant_id=tenant_id,
                since=since,
                until=until,
                actor=actor,
                action_prefix=action_prefix,
            ):
                written += 1
                yield csv_row(audit_cells(row))
    except SQLAlchemyError as exc:
        yield _failed(exc, "audit")
        return
    for line in trailer(written):
        yield line


@reports_router.get("/reports/audit.csv", tags=["reports"])
async def audit_csv(
    since: str | None = Query(default=None, description="ISO 8601 lower bound on ts."),
    until: str | None = Query(default=None, description="ISO 8601 upper bound on ts."),
    actor: str | None = Query(default=None, description="Exact actor to filter to."),
    action_prefix: str | None = Query(
        default=None, alias="actionPrefix", description="Action prefix, e.g. 'memory.'"
    ),
    ticket: str | None = Query(default=None, description="See require_report_download."),
    auth: AuthContext = Depends(require_report_download),
) -> StreamingResponse:
    """Stream the audit trail as CSV, scoped and filtered exactly as the screen is.

    No ``limit``: the screen clamps to 200 rows because a screen must, and an export
    must not — a quarter of evidence with the oldest rows missing is worse than no
    export at all. The rows arrive in keyset pages, so neither this process nor the
    database materialises the whole trail.
    """
    tenant_id = _scope_tenant(auth, None)
    lower, upper = _instant("since", since), _instant("until", until)
    filters = {
        name: value
        for name, value in (
            ("since", since),
            ("until", until),
            ("actor", actor),
            ("action prefix", action_prefix),
        )
        if value
    }
    generated_at = datetime.now(UTC)
    await _record_export(
        "audit", auth, tenant_id=tenant_id, filters=filters, delivery=_delivery(ticket)
    )
    meta = _meta(
        "audit",
        auth=auth,
        tenant_id=tenant_id,
        window=_window_label(lower, upper),
        source="audit_log (the same rows and ordering GET /audit renders)",
        generated_at=generated_at,
        filters=filters,
        caveats=(
            "The structured `payload` column is not exported: it is free-form JSON "
            "written by every call site in the product, and it is not shown on the "
            "audit screen either. Read it through GET /audit or the database page.",
            "This export is itself an audited action — look for the report.export row "
            "at the top of the trail.",
        ),
    )
    return _streamed(
        "audit",
        tenant_id=tenant_id,
        generated_at=generated_at,
        body=_audit_body(
            meta,
            tenant_id=tenant_id,
            since=lower,
            until=upper,
            actor=actor,
            action_prefix=action_prefix,
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /reports/tenant.csv
# ─────────────────────────────────────────────────────────────────────────────

#: The roster's columns. ``last_login_utc`` is derived, not stored — see
#: :func:`_last_logins`.
_TENANT_COLUMNS: tuple[str, ...] = (
    "tenant_id",
    "tenant_name",
    "user_id",
    "username",
    "role",
    "email",
    "is_active",
    "last_login_utc",
)


async def _last_logins(usernames: list[str], tenant_id: int | None) -> dict[str, str]:
    """Return the most recent observed sign-in per username, from the audit trail.

    **There is no ``users.last_login`` column.** The roster asked for one, and rather
    than leave the column blank or invent a plausible date, this derives it from the
    ``auth.login`` rows ``POST /auth/login`` already writes — a real observation, with
    a real limit: a sign-in that happened before this trail existed, or one whose row
    has aged out, is not visible here. The preamble says exactly that, and a user who
    has never been observed signing in gets an empty cell rather than a zero date.

    Args:
        usernames: The roster's actors, so the scan is bounded by the roster.
        tenant_id: The resolved scope filter.

    Returns:
        username → ISO 8601 UTC timestamp of the newest observed login.
    """
    if not usernames:
        return {}
    stmt = (
        select(AuditLog.actor, func.max(AuditLog.ts))
        .where(AuditLog.action == "auth.login", AuditLog.actor.in_(usernames))
        .group_by(AuditLog.actor)
    )
    if tenant_id is not None:
        stmt = stmt.where(AuditLog.tenant_id == tenant_id)
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, tenant_id)
        rows = (await session.execute(stmt)).all()
    return {
        str(actor): ts.replace(tzinfo=UTC).isoformat()
        if ts.tzinfo is None
        else ts.astimezone(UTC).isoformat()
        for actor, ts in rows
        if actor is not None and ts is not None
    }


@reports_router.get("/reports/tenant.csv", tags=["reports"])
async def tenant_csv(
    ticket: str | None = Query(default=None, description="See require_report_download."),
    auth: AuthContext = Depends(require_report_download),
) -> StreamingResponse:
    """Stream the roster — users, roles and the last sign-in the trail can evidence."""
    tenant_id = _scope_tenant(auth, None)
    generated_at = datetime.now(UTC)
    await _record_export(
        "tenant", auth, tenant_id=tenant_id, filters={}, delivery=_delivery(ticket)
    )
    users = await list_users(tenant_id)
    names = {
        row.id: row.name
        for row in (await list_tenants() if auth.is_platform_staff() else [])
    }
    if tenant_id is not None:
        names = {tid: name for tid, name in names.items() if tid == tenant_id}
    logins = await _last_logins([u.username for u in users], tenant_id)
    meta = _meta(
        "tenant",
        auth=auth,
        tenant_id=tenant_id,
        window="Current state as of the generation time — a roster is not a series",
        source="users (the same rows GET /admin/users renders)",
        generated_at=generated_at,
        filters={},
        caveats=(
            "`last_login_utc` is derived from the audit trail's auth.login rows, "
            "because the users table has no last-login column. An empty cell means "
            "no sign-in has been observed in the retained trail, not that the account "
            "has never been used.",
            "Password hashes are not exported, and no column here exposes one.",
        ),
    )

    async def body() -> AsyncIterator[str]:
        for line in _open(meta, _TENANT_COLUMNS):
            yield line
        for user in users:
            yield csv_row(
                [
                    user.tenant_id,
                    names.get(user.tenant_id) if user.tenant_id is not None else "",
                    user.id,
                    user.username,
                    user.role.value,
                    user.email,
                    "yes" if user.is_active else "no",
                    logins.get(user.username, ""),
                ]
            )
        for line in trailer(len(users)):
            yield line

    return _streamed(
        "tenant", tenant_id=tenant_id, generated_at=generated_at, body=body()
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /reports/budget.csv
# ─────────────────────────────────────────────────────────────────────────────

_BUDGET_COLUMNS: tuple[str, ...] = (
    "budget_id",
    "scope_type",
    "scope_id",
    "window",
    "token_cap",
    "tokens_used",
    "tokens_remaining",
    "usd_cap",
    "usd_spent",
    "usd_remaining",
    "calls_in_window",
    "rpm_cap",
    "tpm_cap",
)


@reports_router.get("/reports/budget.csv", tags=["reports"])
async def budget_csv(
    ticket: str | None = Query(default=None, description="See require_report_download."),
    auth: AuthContext = Depends(require_report_download),
) -> StreamingResponse:
    """Stream every governing cap beside the spend the enforcer measures against it.

    The rows come from :func:`aegis.governance.budget_status`, which sums the ledger
    with the identical query :func:`aegis.governance.enforce_governance` runs at the
    gateway. That is the whole point of using it rather than a query of this module's
    own: a report that disagreed with the cap that blocks a call would be worse than
    no report, because somebody would act on it.
    """
    tenant_id = _scope_tenant(auth, None)
    generated_at = datetime.now(UTC)
    await _record_export(
        "budget", auth, tenant_id=tenant_id, filters={}, delivery=_delivery(ticket)
    )
    rows = await budget_status(tenant_id)
    meta = _meta(
        "budget",
        auth=auth,
        tenant_id=tenant_id,
        window="Each row's own rolling window — the `window` column says which",
        source="budgets joined with usage_ledger via aegis.governance.budget_status",
        generated_at=generated_at,
        filters={},
        caveats=(
            "Spend is summed over each cap's own rolling window, with the same query "
            "the gateway enforcer runs, so a figure here is the figure a call is "
            "blocked against.",
            "An empty cap column means that dimension is uncapped, not zero.",
            _LEDGER_OUTCOME_CAVEAT,
        ),
    )

    async def body() -> AsyncIterator[str]:
        for line in _open(meta, _BUDGET_COLUMNS):
            yield line
        for row in rows:
            yield csv_row(
                [
                    row.budget.id,
                    row.budget.scope_type,
                    row.budget.scope_id,
                    row.budget.window,
                    row.budget.token_cap,
                    row.tokens_used,
                    row.tokens_remaining,
                    row.budget.usd_cap,
                    round(row.cost_usd_used, 6),
                    None if row.usd_remaining is None else round(row.usd_remaining, 6),
                    row.calls,
                    row.budget.rpm,
                    row.budget.tpm,
                ]
            )
        for line in trailer(len(rows)):
            yield line

    return _streamed(
        "budget", tenant_id=tenant_id, generated_at=generated_at, body=body()
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /reports/forecast.csv
# ─────────────────────────────────────────────────────────────────────────────

#: The forecast's columns. Everything from ``interval_method`` rightwards repeats on
#: every row on purpose: these are the caveats, and a caveat that lives in a footnote
#: is a caveat that is gone the moment somebody sorts or filters the sheet.
_FORECAST_COLUMNS: tuple[str, ...] = (
    "step",
    "ts_utc",
    "point",
    "lo",
    "hi",
    "interval_method",
    "requested_coverage",
    "achieved_coverage",
    "coverage_meets_request",
    "model",
    "history_points",
    "data_source",
    "cumulative",
    "cumulative_lo",
    "cumulative_hi",
    "cumulative_bounds_are_calibrated",
    "cap_usd",
)


def _forecast_rows(
    result: ForecastResult, burndown: BudgetBurndown | None
) -> Iterable[tuple[object, ...]]:
    """Project a forecast onto :data:`_FORECAST_COLUMNS`, one row per horizon step.

    Args:
        result: The fitted forecast with its measured backtest.
        burndown: The cap projection, when this metric has one.

    Yields:
        The cells for each step.
    """
    by_step = {p.step: p for p in (burndown.points if burndown else [])}
    backtest = result.backtest
    for point in result.points:
        cumulative = by_step.get(point.step)
        yield (
            point.step,
            point.ts.isoformat(),
            round(point.point, 6),
            round(point.lo, 6),
            round(point.hi, 6),
            result.interval_method,
            backtest.requested_coverage,
            backtest.empirical_coverage,
            "yes" if backtest.coverage_meets_request else "no",
            result.model,
            result.history_points,
            result.data_source,
            None if cumulative is None else round(cumulative.cumulative, 6),
            None if cumulative is None else round(cumulative.cumulative_lo, 6),
            None if cumulative is None else round(cumulative.cumulative_hi, 6),
            ""
            if burndown is None
            else ("yes" if burndown.cumulative_bounds_are_calibrated else "no"),
            None if burndown is None else burndown.limit_usd,
        )


@reports_router.get("/reports/forecast.csv", tags=["reports"])
async def forecast_csv(
    tenant_id: int | None = Query(
        default=None,
        description="Platform staff may target one tenant; a tenant admin may not.",
    ),
    metric: str = Query(default="spend", description="'spend' or 'calls'."),
    horizon: int = Query(default=14, ge=1, le=60, description="Steps to project."),
    window: str = Query(default="month", description="Budget window: 'day' or 'month'."),
    ticket: str | None = Query(default=None, description="See require_report_download."),
    auth: AuthContext = Depends(require_report_download),
) -> StreamingResponse:
    """Stream the projection with its caveats as columns, or with its refusal in full.

    When the series is too short to forecast honestly the file is still produced — and
    says why, with the arithmetic (``have`` / ``need``) intact. A downloaded empty
    table would read as "no spend"; a downloaded refusal reads as what it is.
    """
    if metric not in {"spend", "calls"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="metric must be 'spend' or 'calls'.",
        )
    if window not in {"day", "month"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="window must be 'day' or 'month'.",
        )
    scoped = _scope_tenant(auth, tenant_id)
    generated_at = datetime.now(UTC)
    filters = {"metric": metric, "horizon": str(horizon), "budget window": window}
    await _record_export(
        "forecast", auth, tenant_id=scoped, filters=filters, delivery=_delivery(ticket)
    )

    from app.forecast.service import ledger_burndown, ledger_forecast

    result: ForecastResult | None = None
    burndown: BudgetBurndown | None = None
    refusal: str | None = None
    try:
        if metric == "spend":
            limit_usd: float | None = None
            if scoped is not None:
                caps = await list_budgets("tenant", scoped, tenant_id=scoped)
                usd = [c.usd_cap for c in caps if c.window == window and c.usd_cap is not None]
                limit_usd = min(usd) if usd else None
            result, burndown = await ledger_burndown(
                tenant_id=scoped, window=window, limit_usd=limit_usd, horizon=horizon
            )
        else:
            result = await ledger_forecast(
                tenant_id=scoped, metric="calls", horizon=horizon
            )
    except ForecastError as exc:
        refusal = str(exc)
    except ImportError as exc:
        refusal = str(exc)

    caveats = [
        "`requested_coverage` is the level asked for; `achieved_coverage` is the "
        "fraction of held-out actuals that actually fell inside the interval on "
        "rolling-origin backtest windows. Only the second is evidence.",
        _LEDGER_OUTCOME_CAVEAT,
    ]
    if burndown is not None:
        caveats.insert(
            1,
            "`cumulative_bounds_are_calibrated` is no: the per-step bounds are "
            "calibrated one step at a time, and summing marginal quantiles does not "
            "produce a calibrated interval on a running total. Read the cumulative "
            "band as an envelope, not as a guarantee.",
        )
    meta = _meta(
        "forecast",
        auth=auth,
        tenant_id=scoped,
        window=f"{horizon} steps beyond the last observation",
        source="usage_ledger · univariate · statsforecast (aegis.forecast)",
        generated_at=generated_at,
        filters=filters,
        caveats=tuple(caveats),
    )

    async def body() -> AsyncIterator[str]:
        for line in _open(meta, _FORECAST_COLUMNS):
            yield line
        if result is None:
            yield csv_row(["No forecast was produced", refusal or "reason unavailable"])
            yield CRLF
            yield csv_row(["End of export", "0 data rows"])
            return
        written = 0
        for cells in _forecast_rows(result, burndown):
            written += 1
            yield csv_row(cells)
        for line in trailer(written):
            yield line

    return _streamed(
        "forecast", tenant_id=scoped, generated_at=generated_at, body=body()
    )


def mount(target: APIRouter) -> None:
    """Attach this module's routes to ``target`` as real ``APIRoute`` objects.

    Idempotent, exactly as :func:`app.api.routes_redteam.mount` is and for the same
    reason: this module is mounted from the composition root while
    :mod:`app.api.routes` is being edited elsewhere, and a second shadowed copy of a
    handler is invisible at runtime and confusing in the route-coverage analysis.

    Args:
        target: The application's main router, extended in place.
    """
    present = {
        (route.path, frozenset(getattr(route, "methods", ()) or ()))
        for route in target.routes
    }
    target.routes.extend(
        route
        for route in reports_router.routes
        if (route.path, frozenset(getattr(route, "methods", ()) or ())) not in present
    )
