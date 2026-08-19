"""Embedded analytics — Superset's charts, inside Aegis, narrowed by a WHERE clause.

The requirement was blunt: *show this inside Aegis, not by going to another portal*. So
there is no link to ``localhost:8088`` anywhere in this product. There are two ways a
chart reaches an Aegis page, and this router serves both:

``POST /analytics/boards/{board_id}/data``
    The **server-side data path**. Aegis builds the Superset query context itself, calls
    ``POST /api/v1/chart/data``, and hands back rows that Aegis's own chart components
    draw — in the Aegis chrome, in the Aegis light theme, no iframe involved. Built
    first, deliberately: the embed depends on this being right anyway, and if
    ``EMBEDDED_SUPERSET`` turns out to be one of the 6.1.0 wheel's broken paths, this is
    the whole feature rather than a consolation prize.
``POST /analytics/boards/{board_id}/embed-token``
    The **embed**. A short-lived Superset *guest token* the browser hands to Superset's
    embedded SDK. Aegis mints it server-side and never lets the Superset service
    credential near a browser — a Superset admin JWT in a tenant's browser is the whole
    BI instance, every tenant's rows included.

**The tenant filter is a WHERE clause the browser cannot remove.** Both paths derive it
from :meth:`AuthContext.tenant_scope` — the sealed authority — and from nothing the
request carried. A guest token's ``rls`` list is compiled into the SQL by Superset
itself, and the query context Aegis builds carries the same predicate independently. See
:mod:`aegis.analytics.rls`, which is that derivation in one small, heavily-tested file.
There is no request field on any model here that can influence it: the wire carries a
board id and a window key, and a board id names an entry in a server-side catalogue.

**Superset is optional and Aegis degrades.** Nothing in this module runs at import or at
boot. ``GET /analytics/status`` never raises: it reports what is configured, what
answered, and the sentence naming what to do about whatever is wrong. A deployment with
no Superset differs from one with it in exactly one way — this page explains itself, and
every other surface is untouched.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from aegis.analytics import (
    WINDOWS,
    AnalyticsService,
    BoardCatalogue,
    CatalogueError,
    SupersetClient,
    SupersetConfig,
    SupersetRejectedError,
    SupersetUnavailableError,
    load_catalogue,
)
from aegis.analytics.types import AnalyticsStatus, Board
from aegis.retrieval.types import UntenantedPrincipalError
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.routes import AuthContext, _safe_audit, require_auth
from app.config import get_settings

__all__ = [
    "AnalyticsBoardRow",
    "AnalyticsBoardsResponse",
    "AnalyticsDataRequest",
    "AnalyticsDataResponse",
    "AnalyticsEmbedRequest",
    "AnalyticsEmbedResponse",
    "AnalyticsStatusResponse",
    "analytics_router",
    "get_analytics_service",
    "mount",
    "require_analytics_reader",
]

logger = logging.getLogger(__name__)

analytics_router = APIRouter()

#: What an un-tenanted principal is told. Same refusal, same words, as every other
#: tenant-scoped read in the product: there is no honest chart for a principal whose
#: tenant is unknown, so nothing is drawn rather than everything.
_NO_TENANT = (
    "This account is not bound to a tenant, so there is no scope to chart. Ask an "
    "administrator to assign it to a tenant."
)


# ─────────────────────────────────────────────────────────────────────────────
# Composition
# ─────────────────────────────────────────────────────────────────────────────


def _config() -> SupersetConfig:
    """Read the Superset connection settings out of the app config."""
    settings = get_settings()
    return SupersetConfig(
        base_url=settings.superset_base_url,
        username=settings.superset_username,
        password=settings.superset_password,
        provider=settings.superset_provider,
        tenant_column=settings.superset_tenant_column,
        enabled=settings.superset_enabled,
        embed_enabled=settings.superset_embed_enabled,
        guest_token_ttl_seconds=settings.superset_guest_token_ttl_seconds,
    )


@lru_cache(maxsize=1)
def get_analytics_service() -> AnalyticsService:
    """Build the process-wide analytics service.

    A FastAPI dependency, not a module-level singleton read inside the handlers: the
    test suite must be able to substitute a Superset-shaped fake, and **no test in this
    repository may open a socket to a BI server that only exists on the operator's
    Windows box**. ``app.dependency_overrides[get_analytics_service]`` is how.

    Lazy and cached: nothing here runs until the first request to this router, so a
    deployment that never opens the analytics page never constructs an HTTP client,
    never reads a catalogue file and never notices Superset is absent.

    A **malformed** catalogue is reported through the status endpoint rather than
    raised at boot. Both halves of that are deliberate: a broken config file must not
    stop Aegis serving every other page, and it must not silently serve zero boards
    either — "no boards are configured" and "your catalogue file is wrong on line 4"
    are different facts, and the operator gets whichever one is true.
    """
    settings = get_settings()
    config = _config()
    try:
        boards = load_catalogue(settings.superset_boards)
        broken = ""
    except CatalogueError as exc:
        boards = ()
        broken = str(exc)
        logger.error("superset board catalogue is unusable: %s", exc)

    client: SupersetClient | None = None
    if config.configured():
        import httpx

        transport = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0),
            verify=settings.superset_ssl_verify,
        )
        client = SupersetClient(config, transport)

    return AnalyticsService(config, BoardCatalogue(boards), client, catalogue_error=broken)


# ─────────────────────────────────────────────────────────────────────────────
# Guard and scope
# ─────────────────────────────────────────────────────────────────────────────


def require_analytics_reader(auth: AuthContext = Depends(require_auth)) -> AuthContext:
    """Any authenticated principal may open the analytics section.

    *Which boards* they see is decided by the board's ``audience`` in the catalogue, and
    *which rows* by their sealed tenant scope — so this guard admits, and the two
    narrowings below refuse. Both are on the server. The nav hiding a section a role has
    no boards for is a courtesy, not the enforcement.
    """
    return auth


def _scope(auth: AuthContext) -> Any:  # noqa: ANN401 - aegis.retrieval.types.TenantScope
    """Return the sealed tenant authority for ``auth``'s reads, or refuse with a 403.

    The **only** source of the tenant filter that reaches Superset. Deliberately reads
    :meth:`AuthContext.tenant_scope` rather than ``auth.tenant_id``: the latter is a
    fact about the principal and ``None`` down two different paths, one of which is a
    platform operator and one of which is an account nobody assigned a tenant to.
    """
    try:
        return auth.tenant_scope()
    except UntenantedPrincipalError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=_NO_TENANT
        ) from exc


def _unavailable(exc: SupersetUnavailableError) -> HTTPException:
    """Turn an unavailable Superset into a 503 the page can render as an instruction."""
    detail = str(exc)
    if exc.action:
        detail = f"{detail} {exc.action}"
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


def _rejected(exc: SupersetRejectedError) -> HTTPException:
    """Turn a Superset refusal into a 502 that keeps Superset's own words.

    A 502 rather than passing Superset's status through: a 403 from Superset is not a
    statement about the Aegis principal's permissions, and forwarding it would tell a
    tenant admin to check their own access when the real answer is that the Superset
    guest role is missing a grant.
    """
    detail = str(exc)
    if exc.detail:
        detail = f"{detail} Superset said: {exc.detail}"
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)


# ─────────────────────────────────────────────────────────────────────────────
# Wire shapes
# ─────────────────────────────────────────────────────────────────────────────


class AnalyticsStatusResponse(BaseModel):
    """Whether this page can draw anything, and what to do when it cannot."""

    enabled: bool
    configured: bool
    reachable: bool
    embed_enabled: bool = Field(alias="embedEnabled")
    detail: str
    action: str = ""
    base_url: str = Field(default="", alias="baseUrl")
    boards: int = Field(default=0, description="Boards this caller may select.")

    model_config = ConfigDict(populate_by_name=True)


class AnalyticsBoardRow(BaseModel):
    """One board the caller may select. Carries no datasource and no credential."""

    id: str
    title: str
    summary: str
    kinds: list[str]
    window: str = Field(description="The window this board opens on.")
    x: str = Field(default="", description="The dimension column drawn on the x axis.")
    series: list[str] = Field(
        default_factory=list, description="The measure keys each row carries."
    )


class AnalyticsBoardsResponse(BaseModel):
    """The catalogue, narrowed to this caller's role."""

    boards: list[AnalyticsBoardRow]
    windows: dict[str, str] = Field(
        default_factory=dict, description="The selectable windows: key → label."
    )
    tenant_scoped: bool = Field(
        alias="tenantScoped",
        description="False only for a resolved platform-wide authority.",
    )

    model_config = ConfigDict(populate_by_name=True)


class AnalyticsDataRequest(BaseModel):
    """Everything a caller may say about a chart read.

    One field, and it is a key from a fixed list. There is no datasource here, no
    column, no metric, no row limit and no tenant — every one of those is a server-side
    fact, because every one of them is a way to ask Superset about somebody else's rows.
    """

    window: str | None = Field(
        default=None, description=f"One of {sorted(WINDOWS)}, or null for the default."
    )

    model_config = ConfigDict(extra="forbid")


class AnalyticsDataResponse(BaseModel):
    """The rows behind one board, already narrowed to the caller's tenant."""

    board_id: str = Field(alias="boardId")
    title: str
    window: str
    columns: list[str]
    rows: list[dict[str, Any]]
    x: str = ""
    series: list[str] = Field(default_factory=list)
    tenant_scoped: bool = Field(alias="tenantScoped")

    model_config = ConfigDict(populate_by_name=True)


class AnalyticsEmbedRequest(BaseModel):
    """Nothing at all. The board is in the path and the tenant is in the session."""

    model_config = ConfigDict(extra="forbid")


class AnalyticsEmbedResponse(BaseModel):
    """A minted guest token and the dashboard it opens.

    ``token`` is the only Superset credential that ever reaches a browser. It is
    short-lived, it grants exactly one dashboard, and it carries the tenant's row-level
    filter — which Superset compiles into every query run under it.
    """

    board_id: str = Field(alias="boardId")
    token: str
    superset_domain: str = Field(alias="supersetDomain")
    uuid: str
    expires_in_seconds: int = Field(alias="expiresInSeconds")

    model_config = ConfigDict(populate_by_name=True)


def _board_row(board: Board) -> AnalyticsBoardRow:
    """Project a catalogue entry onto the wire, dropping every server-side fact."""
    return AnalyticsBoardRow(
        id=board.id,
        title=board.title,
        summary=board.summary,
        kinds=sorted(board.kinds),
        window=board.default_window,
        x=board.groupby[0] if board.groupby else "",
        series=[metric.key for metric in board.metrics],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────


@analytics_router.get(
    "/analytics/status",
    response_model=AnalyticsStatusResponse,
    tags=["analytics"],
    summary="Whether embedded analytics can draw anything right now",
)
async def analytics_status(
    auth: AuthContext = Depends(require_analytics_reader),
    service: AnalyticsService = Depends(get_analytics_service),
) -> AnalyticsStatusResponse:
    """Report the analytics feature's honest state. Never fails because Superset is down.

    This is the call the page makes before it decides what to render, so it answers 200
    in every state, including "Superset is not running" — a page that 500s because a BI
    tool is not running is exactly the coupling this feature is not allowed to add.
    """
    resolved: AnalyticsStatus = await service.status()
    return AnalyticsStatusResponse(
        enabled=resolved.enabled,
        configured=resolved.configured,
        reachable=resolved.reachable,
        embedEnabled=resolved.embed_enabled,
        detail=resolved.detail,
        action=resolved.action,
        baseUrl=resolved.base_url,
        boards=len(service.boards_for(auth.fine_role)),
    )


@analytics_router.get(
    "/analytics/boards",
    response_model=AnalyticsBoardsResponse,
    tags=["analytics"],
    summary="The boards this role may select",
)
async def analytics_boards(
    auth: AuthContext = Depends(require_analytics_reader),
    service: AnalyticsService = Depends(get_analytics_service),
) -> AnalyticsBoardsResponse:
    """List the catalogue entries this principal's role is an audience for.

    A client must not reach an operator's dashboards, and that is decided by the board's
    ``audience`` — here, and identically in the two routes below, so hiding a board from
    this list is never the only thing stopping someone opening it.
    """
    scope = _scope(auth)
    boards = service.boards_for(auth.fine_role)
    from aegis.retrieval.types import ALL_TENANTS

    return AnalyticsBoardsResponse(
        boards=[_board_row(board) for board in boards],
        windows=dict(WINDOWS),
        tenantScoped=scope is not ALL_TENANTS,
    )


@analytics_router.post(
    "/analytics/boards/{board_id}/data",
    response_model=AnalyticsDataResponse,
    tags=["analytics"],
    summary="Read one board's rows, scoped to the caller's tenant",
)
async def analytics_board_data(
    board_id: str,
    payload: AnalyticsDataRequest,
    auth: AuthContext = Depends(require_analytics_reader),
    service: AnalyticsService = Depends(get_analytics_service),
) -> AnalyticsDataResponse:
    """Query Superset for one board and return rows Aegis's own charts draw.

    Two independent narrowings apply, both derived from the sealed scope: the guest
    token this call is authenticated with carries the tenant's RLS clause, and the query
    context Aegis built carries the same predicate as a filter. Neither is influenced by
    ``payload``, which can say one thing — which window.
    """
    scope = _scope(auth)
    board = service.board(board_id, fine_role=auth.fine_role)
    if board is None or not board.supports("chart"):
        # The same answer for "no such board" and "not yours", so a 404 cannot be used
        # to enumerate the boards a caller is not allowed to open.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No chart board by that name is available to this account.",
        )
    if payload.window is not None and payload.window not in WINDOWS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"'{payload.window}' is not a window this server offers. Choose one "
            f"of {sorted(WINDOWS)}.",
        )
    try:
        data = await service.board_data(board, scope, window=payload.window)
    except SupersetUnavailableError as exc:
        raise _unavailable(exc) from exc
    except SupersetRejectedError as exc:
        raise _rejected(exc) from exc

    return AnalyticsDataResponse(
        boardId=board.id,
        title=board.title,
        window=data.window,
        columns=list(data.columns),
        rows=list(data.rows),
        x=board.groupby[0] if board.groupby else "",
        series=[metric.key for metric in board.metrics],
        tenantScoped=data.tenant_scoped,
    )


@analytics_router.post(
    "/analytics/boards/{board_id}/embed-token",
    response_model=AnalyticsEmbedResponse,
    tags=["analytics"],
    summary="Mint a short-lived, tenant-scoped Superset guest token",
)
async def analytics_embed_token(
    board_id: str,
    payload: AnalyticsEmbedRequest,
    auth: AuthContext = Depends(require_analytics_reader),
    service: AnalyticsService = Depends(get_analytics_service),
) -> AnalyticsEmbedResponse:
    """Mint the guest token the browser hands to Superset's embedded SDK.

    Minting a credential that leaves the process is an audited action: the row records
    who asked, for which board, and — the fact that matters in an incident — which
    tenant the token was scoped to.
    """
    scope = _scope(auth)
    board = service.board(board_id, fine_role=auth.fine_role)
    if board is None or not board.supports("dashboard"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No embeddable board by that name is available to this account.",
        )
    try:
        grant = await service.embed_grant(board, scope)
    except SupersetUnavailableError as exc:
        raise _unavailable(exc) from exc
    except SupersetRejectedError as exc:
        raise _rejected(exc) from exc

    await _safe_audit(
        "analytics.embed_token",
        auth,
        payload={
            "board_id": board.id,
            "dashboard_uuid": board.embedded_uuid,
            "scope": "all_tenants" if auth.tenant_id is None else auth.tenant_id,
        },
    )
    return AnalyticsEmbedResponse(
        boardId=grant.board_id,
        token=grant.token,
        supersetDomain=grant.supersetDomain,
        uuid=grant.uuid,
        expiresInSeconds=grant.expires_in_seconds,
    )


def mount(target: APIRouter) -> None:
    """Attach this module's routes to ``target`` as real ``APIRoute`` objects.

    Idempotent, like :func:`app.api.routes_redteam.mount` and for the same reason: this
    module is mounted from the composition root, and mounting twice would put a second,
    shadowed copy of every handler in the served table — invisible at runtime and
    confusing in exactly the place the route-coverage test reads.

    Args:
        target: The application's main router, extended in place.
    """
    present = {
        (route.path, frozenset(getattr(route, "methods", ()) or ()))
        for route in target.routes
    }
    target.routes.extend(
        route
        for route in analytics_router.routes
        if (route.path, frozenset(getattr(route, "methods", ()) or ())) not in present
    )
