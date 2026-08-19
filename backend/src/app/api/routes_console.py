"""The four surfaces the unified console needs and the API did not have (phase 6 §5).

``GET /models``, the ``/sessions`` family, ``POST /attachments`` and ``GET /me/budget``.
They live here rather than as a 3,700th line of :mod:`app.api.routes` for the reason
:mod:`app.api.ingest_log` already established: that file is the one every phase has to
touch, so a new surface gets its own module and :func:`mount` merges its routes into the
served table with a single line at the bottom of it. The merge is deliberately
``routes.extend`` rather than ``include_router`` — see the ingest-log module docstring
for why the lazy placeholder FastAPI 0.141 appends would make these endpoints invisible
to ``tests/api/test_route_coverage.py``.

**None of these is a new subsystem, and that is the point.**

* ``GET /models`` is a *projection* over :func:`aegis.gateway.routing.routing_table` and
  the cost table beside it. The composer's model dropdown must show what the gateway
  would actually do, so it reads the gateway's own answer rather than a parallel list
  that could disagree with it.
* ``GET /me/budget`` reads :func:`aegis.governance.dashboard.budget_status` — the same
  rows, summed by the same query, that :func:`aegis.governance.enforce_governance`
  compares a call against. A pill that read anything else would eventually disagree with
  the enforcer, and the first person to notice would be on a jury. It exists because a
  ``client``-role user could not see their own budget **anywhere**: every other budget
  read is behind ``require_tenant_admin``.
* ``POST /attachments`` runs :func:`app.vision.analyse`'s ordered rails — payload
  hygiene → image-injection screen → image PII → the vision model → the text output
  rails — and hands the composer a screened descriptor. It stores nothing: the
  attachment lives for the run, and a durable ``attachments`` table is backlog rather
  than a table quietly invented here.
* The ``/sessions`` family is the only new *state*, and its id is the same string the
  memory tier uses for ``memory_session.id``, so the transcript and the recall cannot
  disagree about what a conversation is.

**Scoping.** Every route resolves the caller through the sealed
:data:`~aegis.retrieval.types.TenantScope` / :func:`~aegis.retrieval.types.tenant_filter`
pair, so the unrestricted ``None`` filter is reachable only from the explicit
:data:`~aegis.retrieval.types.ALL_TENANTS` authority and never from a principal that
merely lacks a tenant. A chat is additionally scoped to its **owner**: sharing a tenant
with someone does not entitle you to read their conversation, so a principal carrying no
``user_id`` is refused rather than allowed to match every row.

The two exceptions are stated rather than assumed: ``GET /models`` and
``POST /attachments`` read and write no tenant's rows at all — one returns the
platform's own configuration, the other stores nothing — so there is no predicate for a
scope to be, and demanding one would refuse an un-tenanted platform operator for no
isolation benefit. Every route that *does* touch a row resolves one.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from aegis.agent.deps import risk_at_least
from aegis.gateway.routing import (
    billing_unit,
    is_small_model,
    model_for,
    routing_table,
    unit_cost,
)
from aegis.governance.types import BudgetStatusRow
from aegis.retrieval.types import UntenantedPrincipalError, tenant_filter
from aegis.settings import resolve, resolve_all, write_setting
from aegis.settings.models import SettingScope
from aegis.settings.resolver import (
    SettingError,
    SettingValueError,
    SettingWeakerThanFloorError,
)
from aegis.settings.spec import (
    SETTING_SPECS,
    UnknownSettingError,
    setting_controls,
    spec_for,
)
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from app.agent.deps import AgentDeps
from app.api.routes import AuthContext, get_agent_deps, require_auth
from app.api.schemas import VisionAnalyseRequest
from app.core.models import ModelRole
from app.data.chat import (
    create_chat_session,
    delete_chat_session,
    list_chat_messages,
    list_chat_sessions,
    rename_chat_session,
)
from app.data.session import get_sessionmaker, set_tenant_scope

__all__ = [
    "AttachmentResponse",
    "ChatMessageRow",
    "ChatMessagesResponse",
    "ChatSessionCreateRequest",
    "ChatSessionRow",
    "ChatSessionsResponse",
    "ModelRow",
    "ModelsResponse",
    "MyBudgetResponse",
    "SettingRow",
    "SettingWriteRequest",
    "SettingsResponse",
    "ToolRosterResponse",
    "ToolRow",
    "console_router",
    "mount",
]

logger = logging.getLogger(__name__)

console_router = APIRouter()

#: Upper bound on how many sessions one ``GET /sessions`` call may return, and on how
#: many turns one transcript read may. Clamped at the boundary rather than trusted from
#: the query string: an unbounded ``limit`` on a scoped scan is a denial-of-service knob
#: handed to whoever holds a token.
_SESSIONS_LIMIT_MAX = 200
_MESSAGES_LIMIT_MAX = 500

#: What a principal with no user identity is told. A chat belongs to a person; a token
#: that names none owns no chats, and the request is refused rather than run against a
#: predicate that would have matched everybody's.
_NO_OWNER = (
    "This token carries no user identity, so it owns no conversations. Sign in with a "
    "provisioned account."
)


def _scope(auth: AuthContext) -> int | None:
    """Return the tenant filter for ``auth``, or refuse with a 403.

    Mirrors :func:`app.api.routes._require_scope` through the public types rather than
    importing a private helper: the result is
    :func:`~aegis.retrieval.types.tenant_filter` of a **resolved** authority, so a
    ``None`` here can only mean :data:`~aegis.retrieval.types.ALL_TENANTS`.
    """
    try:
        return tenant_filter(auth.tenant_scope())
    except UntenantedPrincipalError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This account is not bound to a tenant, so there is no scope to read. "
                "Ask an administrator to assign it to a tenant."
            ),
        ) from exc


def _owner(auth: AuthContext) -> int:
    """Return the caller's user id, or refuse with a 403."""
    if auth.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=_NO_OWNER
        )
    return auth.user_id


# ─────────────────────────────────────────────────────────────────────────────
# GET /models — a projection over the gateway's routing table
# ─────────────────────────────────────────────────────────────────────────────


class ModelRow(BaseModel):
    """One role of the effective routing table, with the price beside it."""

    role: str = Field(description="The gateway role, e.g. 'generation' | 'cheap'.")
    model: str = Field(description="The deployment id this role currently routes to.")
    billing_unit: str = Field(
        description="What the input rate is charged per: tokens | audio_minutes | images."
    )
    input_cost_usd: float = Field(
        description="USD for ONE input unit (1k prompt tokens, a minute, an image)."
    )
    output_cost_usd_per_1k: float = Field(
        description="USD per 1k completion tokens; 0 for roles that emit no text."
    )
    small: bool = Field(
        description="Whether this deployment counts as a small/cheap model."
    )


class ModelsResponse(BaseModel):
    """Body for `GET /models` — the effective role → deployment map, priced.

    Read straight off :func:`aegis.gateway.routing.routing_table` and the cost table it
    ships with, so the composer's dropdown shows what the gateway would really do. It is
    **not** a menu the client may pick from unchecked: the allowed set and every cap are
    server-side decisions, and this endpoint only reflects them.
    """

    rows: list[ModelRow]
    default_role: str = Field(
        description="The role a plain answer runs on ('generation')."
    )


@console_router.get("/models", response_model=ModelsResponse, tags=["console"])
async def list_models(auth: AuthContext = Depends(require_auth)) -> ModelsResponse:
    """Return the effective role → deployment routing table with its unit costs.

    Any authenticated caller: this is the platform's own configuration, not per-tenant
    spend. The figures come from :func:`aegis.gateway.routing.unit_cost`, which is the
    same function the gateway falls back to when a provider returns no cost for a custom
    deployment id — so the price shown in the composer is the price that gets charged.

    ``input_cost_usd`` is deliberately "one input unit" rather than "per 1k tokens":
    :func:`aegis.gateway.routing.billing_unit` says a voice role bills per audio minute
    and a vision role per image, and a column headed "per 1k tokens" would have been
    quietly wrong for two of the six rows.
    """
    table = routing_table()
    rows = []
    for role in ModelRole:
        model = table.get(role.value, model_for(role))
        rows.append(
            ModelRow(
                role=role.value,
                model=model,
                billing_unit=billing_unit(role).value,
                # One input unit, whichever unit this role bills in: ``unit_cost``
                # counts only the one that applies, so passing all three is exact.
                input_cost_usd=unit_cost(
                    role, prompt_tokens=1000, audio_seconds=60.0, images=1
                ),
                output_cost_usd_per_1k=unit_cost(role, completion_tokens=1000),
                small=is_small_model(model),
            )
        )
    return ModelsResponse(rows=rows, default_role=ModelRole.GENERATION.value)


# ─────────────────────────────────────────────────────────────────────────────
# /sessions — the chat transcript's thread records
# ─────────────────────────────────────────────────────────────────────────────


class ChatSessionRow(BaseModel):
    """One conversation in the session rail."""

    id: str = Field(description="Also the `memory_session.id` for this conversation.")
    title: str
    created_at: str | None = Field(default=None, description="ISO 8601 UTC.")
    last_active_at: str | None = Field(default=None, description="ISO 8601 UTC.")


class ChatSessionsResponse(BaseModel):
    """Body for `GET /sessions` — the caller's own conversations."""

    rows: list[ChatSessionRow]


class ChatSessionCreateRequest(BaseModel):
    """Body for `POST /sessions` — start a conversation (title optional)."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="New chat", max_length=255)


class ChatSessionPatchRequest(BaseModel):
    """Body for `PATCH /sessions/{id}` — retitle a conversation."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(max_length=255)


class ChatMessageRow(BaseModel):
    """One turn of a conversation, as the thread renders it."""

    turn_index: int
    role: str = Field(description="'user' | 'assistant'.")
    content: str
    run_id: str | None = Field(
        default=None, description="The run that produced an assistant turn."
    )
    created_at: str | None = Field(default=None, description="ISO 8601 UTC.")


class ChatMessagesResponse(BaseModel):
    """Body for `GET /sessions/{id}/messages` — one conversation's turns, in order."""

    session_id: str
    rows: list[ChatMessageRow]


def _iso(ts: object) -> str | None:
    """Render a timestamp as an ISO 8601 string, or ``None``."""
    return None if ts is None else str(ts)


def _session_row(row: object) -> ChatSessionRow:
    """Project a :class:`~app.data.models.ChatSession` onto the wire row."""
    return ChatSessionRow(
        id=row.id,  # type: ignore[attr-defined]
        title=row.title,  # type: ignore[attr-defined]
        created_at=_iso(row.created_at),  # type: ignore[attr-defined]
        last_active_at=_iso(row.last_active_at),  # type: ignore[attr-defined]
    )


@console_router.get("/sessions", response_model=ChatSessionsResponse, tags=["console"])
async def list_sessions(
    limit: int = 50, auth: AuthContext = Depends(require_auth)
) -> ChatSessionsResponse:
    """Return the caller's own conversations, most recently active first."""
    rows = await list_chat_sessions(
        tenant_id=_scope(auth),
        user_id=_owner(auth),
        limit=max(1, min(limit, _SESSIONS_LIMIT_MAX)),
    )
    return ChatSessionsResponse(rows=[_session_row(r) for r in rows])


@console_router.post(
    "/sessions",
    response_model=ChatSessionRow,
    status_code=status.HTTP_201_CREATED,
    tags=["console"],
)
async def create_session(
    req: ChatSessionCreateRequest,
    auth: AuthContext = Depends(require_auth),
) -> ChatSessionRow:
    """Start a conversation and return it, id included.

    The **server** mints the id rather than accepting one from the client, because the
    same string becomes ``memory_session.id``: a client-chosen id is a client-chosen
    memory partition key, and guessing somebody else's would be the whole isolation
    boundary handed away for a convenience. The row's owner and tenant are stamped from
    the token, never from the body.
    """
    row = await create_chat_session(
        uuid.uuid4().hex,
        tenant_id=_scope(auth),
        user_id=_owner(auth),
        title=req.title.strip() or "New chat",
    )
    return _session_row(row)


@console_router.patch(
    "/sessions/{session_id}", response_model=ChatSessionRow, tags=["console"]
)
async def patch_session(
    session_id: str,
    req: ChatSessionPatchRequest,
    auth: AuthContext = Depends(require_auth),
) -> ChatSessionRow:
    """Retitle one of the caller's conversations.

    Raises:
        HTTPException: 404 when the session is not the caller's — the same answer as
            for one that does not exist, so the id space cannot be enumerated.
    """
    row = await rename_chat_session(
        session_id,
        req.title.strip() or "New chat",
        tenant_id=_scope(auth),
        user_id=_owner(auth),
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No conversation {session_id} is visible to this caller.",
        )
    return _session_row(row)


class DeletedResponse(BaseModel):
    """Body for `DELETE /sessions/{id}` — what was removed."""

    id: str
    deleted: bool


@console_router.delete(
    "/sessions/{session_id}", response_model=DeletedResponse, tags=["console"]
)
async def delete_session(
    session_id: str, auth: AuthContext = Depends(require_auth)
) -> DeletedResponse:
    """Delete one of the caller's conversations and its turns.

    The memory tier's own rows are **not** touched. Deleting a chat is "remove this
    thread from my rail"; erasing what the agent learned is ``POST /memory/forget``,
    which is a different, audited decision with its own confirmation flow. Conflating
    them would make a tidy-up gesture silently destroy durable facts.

    Raises:
        HTTPException: 404 when the session is not the caller's.
    """
    if not await delete_chat_session(
        session_id, tenant_id=_scope(auth), user_id=_owner(auth)
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No conversation {session_id} is visible to this caller.",
        )
    return DeletedResponse(id=session_id, deleted=True)


@console_router.get(
    "/sessions/{session_id}/messages",
    response_model=ChatMessagesResponse,
    tags=["console"],
)
async def session_messages(
    session_id: str,
    limit: int = 200,
    auth: AuthContext = Depends(require_auth),
) -> ChatMessagesResponse:
    """Return one conversation's turns in order — the transcript a reload restores.

    Raises:
        HTTPException: 404 when the session is not the caller's.
    """
    rows = await list_chat_messages(
        session_id,
        tenant_id=_scope(auth),
        user_id=_owner(auth),
        limit=max(1, min(limit, _MESSAGES_LIMIT_MAX)),
    )
    if rows is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No conversation {session_id} is visible to this caller.",
        )
    return ChatMessagesResponse(
        session_id=session_id,
        rows=[
            ChatMessageRow(
                turn_index=r.turn_index,
                role=r.role,
                content=r.content,
                run_id=r.run_id,
                created_at=_iso(r.created_at),
            )
            for r in rows
        ],
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /attachments — an image, screened, for the length of one run
# ─────────────────────────────────────────────────────────────────────────────


class AttachmentResponse(BaseModel):
    """Body for `POST /attachments` — a screened attachment the run may cite.

    ``blocked`` is a **200**, not an error: a refused attachment is the product of the
    injection screen working, and the console shows the verdict as a guardrail chip
    before the answer. ``id`` is a per-run handle, not a storage key — nothing is
    persisted, so the handle is meaningless once the run ends.
    """

    id: str = Field(description="Ephemeral handle for this attachment within the run.")
    filename: str | None = None
    mime_type: str | None = Field(
        default=None,
        description=(
            "The SNIFFED content type — derived from the magic bytes, never the "
            "attacker-controlled declaration. None when hygiene could not run."
        ),
    )
    blocked: bool = Field(description="Whether a rail refused the attachment.")
    summary: str = Field(description="The model's reading of the image, or '' if blocked.")
    coverage: str = Field(description="One line: which controls ran, and which did not.")


@console_router.post(
    "/attachments", response_model=AttachmentResponse, tags=["console"]
)
async def create_attachment(
    req: VisionAnalyseRequest,
    auth: AuthContext = Depends(require_auth),
) -> AttachmentResponse:
    """Screen one composer attachment and return the handle the run carries.

    Deliberately a thin projection over ``POST /vision/analyse``'s pipeline rather than
    a second implementation of it: the ordered rails (payload hygiene → image-injection
    screen → image PII → the vision model → the text output rails) are the product, and
    two code paths onto them is two places for one of them to be skipped. What this
    endpoint adds is the *composer's* shape — a handle, the verified MIME type and one
    verdict — instead of the full audit record the vision screen renders.

    **No storage.** The attachment lives for the run; a durable ``attachments`` table is
    backlog, and inventing one here would mean a retention policy nobody has decided.

    **Why this route alone resolves no tenant scope.** It reads no tenant's rows and
    writes none, so there is no predicate for a scope to be. Requiring one would refuse
    an un-tenanted platform operator an image screen for no isolation benefit — the same
    reasoning ``POST /vision/analyse`` already runs on. What it *does* need is the
    governance binding below, because it spends: two paid ``ModelRole.VISION`` calls
    that must land against the caller's caps and their ledger.

    Raises:
        HTTPException: 400 when the base64 payload is not decodable.
    """
    from app.api.routes import _resolve_governance
    from app.core.governance import reset_governance_context, set_governance_context
    from app.vision import analyse

    # Bind the caller's caps for the two paid VISION calls this makes, exactly as
    # ``/vision/analyse`` does: without it an authenticated caller could loop images
    # for spend no cap limited and no ledger row recorded.
    governance = await _resolve_governance(auth)
    token = set_governance_context(governance)
    try:
        analysis = await analyse(
            req.image_base64,
            req.question,
            mime_type=req.mime_type,
            filename=req.filename,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    finally:
        reset_governance_context(token)

    return AttachmentResponse(
        id=uuid.uuid4().hex,
        filename=req.filename,
        mime_type=None if analysis.image is None else analysis.image.sniffed_mime,
        blocked=analysis.blocked,
        # ``answer`` is already empty on a blocked run — the model never ran — so this
        # restates the invariant rather than relying on it.
        summary="" if analysis.blocked else analysis.answer,
        coverage=analysis.coverage(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /me/budget — the caller's own caps, from the enforcer's own rows
# ─────────────────────────────────────────────────────────────────────────────


class MyBudgetResponse(BaseModel):
    """Body for `GET /me/budget` — what this principal may spend, and has.

    ``rows`` is :class:`~aegis.governance.types.BudgetStatusRow` verbatim, so the pill
    and the enforcer read the identical numbers. ``measured`` is ``False`` when no cap
    governs the caller at all — the console then renders "not yet measured" rather than
    a plausible zero, which is the distinction the whole surface is judged on.
    """

    tenant_id: int | None = None
    user_id: int | None = None
    rows: list[BudgetStatusRow]
    measured: bool = Field(
        description="Whether any cap governs this principal. False ⇒ draw no figure."
    )
    cost_usd_used: float = Field(
        default=0.0, description="Spend against the nearest-binding cap's window."
    )
    usd_cap: float | None = Field(
        default=None, description="The nearest-binding USD cap, or None when uncapped."
    )
    usd_remaining: float | None = Field(
        default=None, description="cap − used (≥0), or None when uncapped."
    )


@console_router.get("/me/budget", response_model=MyBudgetResponse, tags=["console"])
async def my_budget(auth: AuthContext = Depends(require_auth)) -> MyBudgetResponse:
    """Return the caller's **own** effective caps and live spend.

    This is the only budget read a ``client``-role user can make: ``/admin/budgets`` and
    ``/governance/dashboard`` are both behind ``require_tenant_admin``, so until this
    endpoint existed the role the product exists for could not see what it was allowed
    to spend — while the enforcer refused its runs on exactly that number.

    The rows come from :func:`aegis.governance.dashboard.budget_status`, whose ledger
    summation *is* the one :func:`aegis.governance.enforce_governance` runs. The
    headline picks the **user** cap when one exists and falls back to the tenant's,
    which is the same nearest-binding order the enforcer applies.
    """
    from aegis.governance import budget_status

    tenant_id = _scope(auth)
    try:
        rows = await budget_status(tenant_id)
    except Exception:  # noqa: BLE001 - the store is optional; degrade to honest empty
        logger.debug("budget_status read failed — reporting unmeasured.", exc_info=True)
        rows = []

    mine = [
        row
        for row in rows
        if (row.budget.scope_type == "tenant" and row.budget.scope_id == auth.tenant_id)
        or (row.budget.scope_type == "user" and row.budget.scope_id == auth.user_id)
    ]
    # Nearest-binding first: a user cap clamps a tenant cap, never the other way round.
    nearest = next(
        (r for r in mine if r.budget.scope_type == "user"),
        next((r for r in mine if r.budget.scope_type == "tenant"), None),
    )
    return MyBudgetResponse(
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
        rows=mine,
        measured=nearest is not None,
        cost_usd_used=0.0 if nearest is None else nearest.cost_usd_used,
        usd_cap=None if nearest is None else nearest.budget.usd_cap,
        usd_remaining=None if nearest is None else nearest.usd_remaining,
    )


# ─────────────────────────────────────────────────────────────────────────────
# /settings — the catalogue's HTTP surface, and where each value came from
# ─────────────────────────────────────────────────────────────────────────────


class SettingRow(BaseModel):
    """One resolved control: its effective value, and **which scope decided it**.

    ``source`` is the reason this endpoint exists. A control that renders a value
    without saying whether it is the platform's default, the tenant's choice or the
    person's own is a control nobody can reason about — "Team (your setting)" and "Team
    (your tenant's default)" look identical on screen and mean opposite things the
    moment somebody wants to change one.

    ``control`` is :func:`aegis.settings.spec.setting_controls`' own descriptor,
    forwarded verbatim rather than re-typed field by field: the catalogue already
    declares the type, the default, the choices, the bounds and the help text, and a
    second projection here is the drift this whole package is built to prevent.

    **``control.effective`` is not decoration.** ``False`` means nothing in the system
    reads this key yet, and ``control.inert_reason`` says what would change that. Six
    keys once saved, wrote an audit row and badged themselves "Your setting" while
    changing nothing whatsoever; four now bind, and the two that still do not
    (``agent.model``, ``agent.mode``) say so here. A screen that renders an
    ``effective=False`` control as though a write to it took effect re-creates the
    defect on the client side of a wire that is now telling the truth.
    """

    key: str
    value: Any = Field(description="The effective value after the merge rule is applied.")
    source: str = Field(description="Which scope decided it: platform | tenant | user.")
    control: dict[str, Any] = Field(
        description="The catalogue's UI descriptor: type, control, default, bounds, "
        "choices, merge rule, writable_by/readable_by and the help text."
    )
    writable: bool = Field(
        description="Whether this caller's fine role may write the key at all. Which "
        "SCOPE their write may reach is the resolver's decision, made at write time."
    )


class SettingsResponse(BaseModel):
    """Body for `GET /settings` — every control this caller may read, resolved."""

    tenant_id: int | None = None
    user_id: int | None = None
    rows: list[SettingRow]


class SettingWriteRequest(BaseModel):
    """Body for `PUT /settings/{key}` — the value, and which of the caller's own layers.

    ``scope`` names a **layer**, never a target: the tenant and user ids a row is
    stamped with come from the token and are never accepted from the body, so "write at
    tenant scope" can only ever mean *this* caller's tenant. Whether the caller may
    reach that layer at all is :func:`aegis.settings.resolver.write_setting`'s decision,
    not this model's.
    """

    model_config = ConfigDict(extra="forbid")

    value: Any = Field(description="The value to write; validated against the spec.")
    scope: str = Field(
        default=SettingScope.USER.value,
        description="platform | tenant | user. Defaults to the caller's own preference.",
    )


def _setting_row(key: str, resolved: tuple[Any, str], *, fine_role: str) -> SettingRow:
    """Project one resolved key onto the wire row."""
    value, source = resolved
    spec = spec_for(key)
    return SettingRow(
        key=key,
        value=value,
        source=source,
        control=setting_controls([spec])[0],
        writable=fine_role in spec.writable_by,
    )


def _settings_http_error(exc: SettingError) -> HTTPException:
    """Translate the resolver's refusals into their HTTP codes, reason intact.

    Every one of these is refused *in the resolver*, deliberately: a disabled control in
    a form is a hint and a ``curl`` is not. This function only chooses the number — it
    never decides anything the resolver has not already decided.
    """
    if isinstance(exc, SettingWeakerThanFloorError):
        # 409, not 422: the value is legal for the key and would have been accepted at a
        # different scope. What refuses it is the stricter value already in force.
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.reason)
    if isinstance(exc, SettingValueError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.reason
        )
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.reason)


def _unknown_setting(exc: UnknownSettingError) -> HTTPException:
    """A key that is not in the catalogue is a 404 — there is no such control."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _stores_off() -> bool:
    """Whether this deployment runs with no durable store at all."""
    from app.config import get_settings

    return not get_settings().stores_enabled


@console_router.get("/settings", response_model=SettingsResponse, tags=["console"])
async def list_settings(auth: AuthContext = Depends(require_auth)) -> SettingsResponse:
    """Return every control this caller may read, resolved, each with its source.

    One query for the whole catalogue rather than N round trips — and keys the caller
    may not read are **omitted** rather than refused, because this is a screen and one
    unreadable key should not blank the page.

    In databaseless (``STORES=off``) mode there is no ``settings`` table and therefore no
    written row at any scope, so the catalogue's compiled-in defaults genuinely *are*
    what is in force and are reported with ``source="platform"``. That is a different
    claim from "the store is down", which is a 503 below — reporting a default as the
    effective value while a tenant's stored tightening sits unread would be exactly the
    lie the ``source`` field exists to prevent.

    Raises:
        HTTPException: 403 for an un-tenanted principal; 503 when the store is
            configured but unreadable.
    """
    tenant_id = _scope(auth)
    if _stores_off():
        return SettingsResponse(
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            rows=[
                _setting_row(
                    spec.key,
                    (spec.default, SettingScope.PLATFORM.value),
                    fine_role=auth.fine_role,
                )
                for spec in SETTING_SPECS
                if auth.fine_role in spec.readable_by
            ],
        )
    try:
        async with get_sessionmaker()() as session:
            await set_tenant_scope(session, tenant_id)
            resolved = await resolve_all(
                session,
                tenant_id=tenant_id,
                user_id=auth.user_id,
                actor_role=auth.fine_role,
            )
    except SQLAlchemyError as exc:
        logger.error("Settings read failed for tenant %s.", tenant_id, exc_info=True)  # noqa: TRY400
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The settings store is unreachable, so no value can be reported as "
            "in force. Retry once the database is back.",
        ) from exc
    return SettingsResponse(
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
        rows=[
            _setting_row(key, value, fine_role=auth.fine_role)
            for key, value in sorted(resolved.items())
        ],
    )


@console_router.get("/settings/{key}", response_model=SettingRow, tags=["console"])
async def get_setting(key: str, auth: AuthContext = Depends(require_auth)) -> SettingRow:
    """Return one control's effective value and the scope that decided it.

    Raises:
        HTTPException: 404 for a key that is not in the catalogue, 403 when the caller
            may not read it or is un-tenanted, 503 when the store is unreadable.
    """
    tenant_id = _scope(auth)
    try:
        spec = spec_for(key)
    except UnknownSettingError as exc:
        raise _unknown_setting(exc) from exc
    if _stores_off():
        if auth.fine_role not in spec.readable_by:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role {auth.fine_role!r} may not read {key!r}",
            )
        return _setting_row(
            key, (spec.default, SettingScope.PLATFORM.value), fine_role=auth.fine_role
        )
    try:
        async with get_sessionmaker()() as session:
            await set_tenant_scope(session, tenant_id)
            resolved = await resolve(
                session,
                key,
                tenant_id=tenant_id,
                user_id=auth.user_id,
                actor_role=auth.fine_role,
            )
    except SettingError as exc:
        raise _settings_http_error(exc) from exc
    except SQLAlchemyError as exc:
        logger.error("Settings read failed for %s.", key, exc_info=True)  # noqa: TRY400
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The settings store is unreachable, so no value can be reported as "
            "in force. Retry once the database is back.",
        ) from exc
    return _setting_row(key, resolved, fine_role=auth.fine_role)


@console_router.put("/settings/{key}", response_model=SettingRow, tags=["console"])
async def put_setting(
    key: str,
    req: SettingWriteRequest,
    auth: AuthContext = Depends(require_auth),
) -> SettingRow:
    """Write one control at one of the caller's **own** layers, or refuse with a reason.

    **Every refusal is the resolver's**, not this route's:
    :func:`aegis.settings.resolver.write_setting` already refuses a role that may not
    write the key, a scope beyond that role's reach, a value that is the wrong type or
    out of bounds, and a ``TIGHTEN_ONLY`` write that would be weaker than the enclosing
    scope. Re-checking any of that here would be a second policy that can disagree with
    the first, which is how a control ends up enforced in a form and bypassed by a
    ``curl``. This function chooses the status code and nothing else.

    The row's tenant and user come from the **token**. A body cannot name a scope target,
    only a layer — so "tenant scope" is always this caller's tenant, and the sealed
    :func:`~aegis.retrieval.types.tenant_filter` resolves it exactly as every other
    scoped write does.

    The write and its audit row commit together: a setting that changed with no record of
    who changed it is precisely the evidence a governance review asks for.

    Raises:
        HTTPException: 404 unknown key; 403 role or scope refused; 409 a weakening of a
            tighten-only key; 422 an illegal value; 503 the store is unreadable.
    """
    tenant_id = _scope(auth)
    try:
        scope = SettingScope(req.scope)
    except ValueError as exc:
        legal = ", ".join(s.value for s in SettingScope)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown scope {req.scope!r}; expected one of {legal}",
        ) from exc
    if _stores_off():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="This deployment runs with no durable store, so a preference has "
            "nowhere to be written. The catalogue defaults are the only values in force.",
        )
    # The ids are the caller's own, never the body's. ``None`` here can only have come
    # from ALL_TENANTS (platform staff), whose layer is `platform` — a tenant-scoped row
    # with a NULL tenant would be readable by every tenant, and the resolver refuses it.
    row_tenant = None if scope is SettingScope.PLATFORM else tenant_id
    row_user = _owner(auth) if scope is SettingScope.USER else None
    try:
        async with get_sessionmaker()() as session:
            await set_tenant_scope(session, tenant_id)
            await write_setting(
                session,
                key,
                req.value,
                scope=scope,
                actor_role=auth.fine_role,
                tenant_id=row_tenant,
                user_id=row_user,
                actor_user_id=auth.user_id,
                updated_by=auth.username,
            )
            resolved = await resolve(
                session, key, tenant_id=tenant_id, user_id=auth.user_id
            )
            await session.commit()
    except UnknownSettingError as exc:
        raise _unknown_setting(exc) from exc
    except SettingError as exc:
        raise _settings_http_error(exc) from exc
    except SQLAlchemyError as exc:
        logger.error("Settings write failed for %s.", key, exc_info=True)  # noqa: TRY400
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The settings store is unreachable, so the preference was not saved.",
        ) from exc
    from app.api.routes import _safe_audit

    await _safe_audit(
        "settings.write",
        auth,
        payload={"key": key, "scope": scope.value, "value": req.value},
    )
    return _setting_row(key, resolved, fine_role=auth.fine_role)


# ─────────────────────────────────────────────────────────────────────────────
# GET /tools — the effective roster, and which layer decided each row
# ─────────────────────────────────────────────────────────────────────────────


class ToolRow(BaseModel):
    """One action tool, as this caller may (or may not) use it.

    ``decided_by`` names the **narrowest layer that constrains this tool for this
    caller**, which is the only version of "who decided" a screen can act on:

    * ``platform`` — the tool is declared in the registry and nothing below narrows it.
      It runs unattended.
    * ``persona`` — the persona allowlist (:func:`app.adapter.tools.is_allowed`, the one
      policy function) does not carry it. Not available.
    * ``tenant`` — available, but the tenant's resolved ``agent.gate_min_risk`` floor
      stands between the model and running it: the agent may only *propose* it, and a
      human decides. Different from "not allowed", and a screen that conflated the two
      would send somebody to the wrong settings page.
    """

    name: str
    description: str
    risk: str = Field(description="The tool's declared risk tier: low | medium | high.")
    allowed: bool = Field(description="Whether this caller's persona may call it.")
    decided_by: str = Field(description="platform | persona | tenant — see the class doc.")
    requires_approval: bool = Field(
        description="Whether a call would stop at the human gate for this tenant."
    )


class ToolRosterResponse(BaseModel):
    """Body for `GET /tools` — the effective roster for one caller.

    ``allowed_count`` of ``total`` is the composer's "6 of 9". It is a **report**: this
    endpoint is read-only, and the intersection it reports is the same
    ``platform ∩ persona`` one :func:`app.adapter.tools.is_allowed` enforces before any
    side effect, never a second copy of it.
    """

    persona: str
    gate_min_risk: str = Field(
        description="The tenant's effective human-gate floor, resolved exactly as a run "
        "resolves it (agent.gate_min_risk, tighten-only)."
    )
    rows: list[ToolRow]
    allowed_count: int
    total: int


@console_router.get("/tools", response_model=ToolRosterResponse, tags=["console"])
async def list_tools(
    persona: str | None = None,
    auth: AuthContext = Depends(require_auth),
    deps: AgentDeps = Depends(get_agent_deps),
) -> ToolRosterResponse:
    """Return every declared tool with whether this caller may use it, and who decided.

    Three real layers, read from the three places that actually hold them — no fourth
    invented here:

    1. **platform** — :data:`app.adapter.tools.TOOL_REGISTRY`: what exists at all.
    2. **persona** — :func:`app.adapter.tools.is_allowed`: what this persona may call.
       The same function ``run_tool`` checks before any side effect, and the same one a
       sub-agent's definitions are filtered through, so this cannot be a more permissive
       second intersection.
    3. **tenant** — ``agent.gate_min_risk``, resolved through
       :func:`app.agent.deps.resolve_run_config`, which is the function a *run* resolves
       it with. A tool at or above the floor may only be proposed.

    **Read-only, and deliberately.** A pin — "use only these three tools for this run" —
    needs three things that do not exist yet: a field on ``QueryRequest`` carrying the
    chosen names, that field threaded onto ``AgentState`` so the graph can read it, and
    the narrowing applied inside
    :func:`aegis.agent.subagent.allowed_tool_definitions`, which is where the one
    intersection already lives and which that function's docstring names as the place a
    Phase 6 pin must go. Serving a pin control before any of that exists would be a
    control that changes nothing, which is the defect this endpoint was written to
    remove.

    Args:
        persona: An explicit persona id to report for, subject to the same role scoping
            ``POST /query`` applies. Omitted → the caller's own.
        auth: The authenticated principal.
        deps: The agent's capability bundle; its config is the process-wide floor the
            tenant's settings are folded onto.

    Raises:
        HTTPException: 400 for an unknown persona, 403 for one this role may not drive.
    """
    from app.adapter.tools import TOOL_REGISTRY, is_allowed
    from app.agent.deps import resolve_run_config
    from app.api.routes import _resolve_governance, _resolve_persona
    from app.core.governance import reset_governance_context, set_governance_context

    persona_id = _resolve_persona(persona, auth)
    # The gate floor is the tenant's, so it is resolved through the tenant's own
    # governance binding — the same contextvar a run reads it from. Building it here
    # rather than passing ``auth.tenant_id`` keeps ONE way for a tenant's floor to be
    # found, which is what stops this surface reporting a floor a run would not obey.
    config = deps.config
    governance = await _resolve_governance(auth)
    token = set_governance_context(governance)
    try:
        config = await resolve_run_config(config)
    finally:
        reset_governance_context(token)

    rows: list[ToolRow] = []
    for name in sorted(TOOL_REGISTRY):
        spec = TOOL_REGISTRY[name]
        allowed = is_allowed(persona_id, name)
        gated = allowed and risk_at_least(spec.risk, config.gate_min_risk)
        rows.append(
            ToolRow(
                name=name,
                description=spec.description,
                risk=spec.risk.value,
                allowed=allowed,
                decided_by="persona" if not allowed else ("tenant" if gated else "platform"),
                requires_approval=gated,
            )
        )
    return ToolRosterResponse(
        persona=persona_id,
        gate_min_risk=config.gate_min_risk.value,
        rows=rows,
        allowed_count=sum(1 for row in rows if row.allowed),
        total=len(rows),
    )


def mount(target: APIRouter) -> None:
    """Attach this module's routes to ``target`` as real ``APIRoute`` objects.

    Args:
        target: The application's main router. Its ``routes`` list is extended in place;
            see the module docstring for why this is not ``include_router``.
    """
    target.routes.extend(console_router.routes)
