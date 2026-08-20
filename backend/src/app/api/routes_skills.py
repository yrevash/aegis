"""The skills control plane — authoring, activation, and the rail that guards both.

`§10.1-10.3`. A skill used to be a Markdown file in a directory on a container's
filesystem, read at recall time and pasted whole into the prompt. There was no way for a
tenant to have its own, no way for a person to have their own, no screen to write one on,
and no evidence in a trace that one had been used. This module is the surface that fixes
the middle two; :mod:`aegis.skills` is the mechanism and
:mod:`app.agent.skills_tool` is the visibility.

``GET /skills``
    Every skill this caller can see, at every layer they can see it at, with whether it
    is currently **in force** — which is a different question from whether the row
    exists, and is answered by the settings resolver rather than by a column.
``POST /skills``
    Author one, from a ``SKILL.md`` document. Screened before storage; see below.
``PUT /skills/{scope}/{name}/active``
    Put one in force at a layer, or take it out. Writes ``skills.enabled``.
``DELETE /skills/{scope}/{name}``
    Remove the row and its activation together.

**Who may write what is not decided here.** It is
:func:`aegis.settings.resolver.write_setting`'s, reached through
:func:`aegis.skills.store.set_active`: a business user writes the user layer and is
refused the tenant one, a tenant admin writes their tenant's, and only a platform admin
writes the platform's. Re-deciding any of that in this module would be a second policy
that can disagree with the first — the reasoning
:mod:`app.api.routes_seats` sets out at length, and this module simply obeys.

**Validation on write, not on use, and that is the whole security posture.** A skill
body is stored text that reaches a prompt: the same attack surface as uploaded memory,
which §7.16 row 11 already screens *before* storage for exactly this reason. The payload
costs nothing at write time, survives every session, and arrives *inside* the prompt
rather than in front of it — so a skill that fails the input rail is refused at authoring
time with a 422 and **no row is written**. That ordering is enforced one layer down, in
:func:`aegis.skills.store.write_skill`, which takes the rail as a required argument
rather than an optional one, because a default is something a caller can be unaware of.

The rail's redactions are reported back rather than applied silently: a REDACT verdict
stores the redacted text (returning the original would make the rail decorative), and the
author is told which kinds were masked so they can rephrase instead of discovering
``[REDACTED_PERSON]`` in their own runbook a week later.
"""

from __future__ import annotations

import logging
from typing import Any

from aegis.settings import SettingError
from aegis.skills import (
    SkillBodyRefusedError,
    SkillFormatError,
    SkillNotFoundError,
    SkillScope,
    SkillScopeError,
    delete_skill,
    list_skills,
    parse_skill_md,
    render_skill_md,
    resolve_skills,
    set_active,
    write_skill,
)
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.api.routes import AuthContext, _safe_audit, _scope_tenant, require_auth
from app.data.session import get_sessionmaker, set_tenant_scope

__all__ = [
    "SkillRow",
    "SkillWriteRequest",
    "SkillsResponse",
    "mount",
    "skills_router",
]

logger = logging.getLogger(__name__)

skills_router = APIRouter()


class SkillRow(BaseModel):
    """One authored skill, with the layer it lives at and whether it is live."""

    name: str
    scope: str = Field(description="platform | tenant | user — the layer it was authored at.")
    description: str
    document: str = Field(description="The whole skill, as a SKILL.md document.")
    triggers: list[str] = Field(default_factory=list)
    in_force: bool = Field(
        alias="inForce",
        description="Whether this skill resolves for a run right now — the resolver's answer.",
    )
    is_safety: bool = Field(
        default=False,
        alias="isSafety",
        description="A platform safety skill: no other layer may rebind its name.",
    )
    updated_by: str | None = Field(default=None, alias="updatedBy")

    model_config = ConfigDict(populate_by_name=True)


class SkillsResponse(BaseModel):
    """Body for ``GET /skills``."""

    rows: list[SkillRow] = Field(default_factory=list)
    scopes: list[str] = Field(
        default_factory=list,
        description="The layers this caller may author at, strongest first.",
    )


class SkillWriteRequest(BaseModel):
    """Body for ``POST /skills``.

    Carries **no tenant and no user**: both are the server's to decide from the sealed
    scope, and a body that could name either is §7.16 row 12 waiting to happen.
    ``extra='forbid'`` makes a stray field a 422 rather than a silently ignored one.
    """

    model_config = ConfigDict(extra="forbid")

    document: str = Field(description="The whole SKILL.md, frontmatter included.")
    scope: str = Field(default="user", description="platform | tenant | user.")
    is_safety: bool = Field(
        default=False,
        alias="isSafety",
        description="Platform only. Refused at any other layer, by the table as well as here.",
    )
    enable: bool = Field(
        default=True, description="Put it in force at this layer as part of the same write."
    )


class SkillWriteResponse(BaseModel):
    """What the author gets back: the row, and what the rail did to it on the way in."""

    row: SkillRow
    verdict: str = Field(description="pass | flag | redact — the input rail's verdict.")
    redactions: list[str] = Field(
        default_factory=list,
        description="PII kinds the rail masked before storage. Stored redacted, not raw.",
    )


def _scope_of(raw: str) -> SkillScope:
    """Parse a wire scope, or refuse with the three legal values.

    Raises:
        HTTPException: 422 when the value is not a layer.
    """
    try:
        return SkillScope(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{raw!r} is not a skill layer. Use 'platform', 'tenant' or 'user'.",
        ) from exc


def _target(auth: AuthContext, scope: SkillScope) -> tuple[int | None, int | None]:
    """Return the ``(tenant_id, user_id)`` a write at ``scope`` is stamped with.

    Never read from the request body. A platform-scoped row carries neither; a tenant
    row carries the caller's sealed tenant; a user row carries that tenant and the
    caller's own id. Platform staff have no tenant of their own, so they cannot author
    a tenant- or user-scoped skill by accident.

    Raises:
        HTTPException: 400 when the caller has no tenant to write the layer into.
    """
    if scope is SkillScope.PLATFORM:
        return None, None
    tenant_id = _scope_tenant(auth, None)
    if tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "A tenant- or user-scoped skill belongs to a tenant, and this account "
                "operates the platform rather than living in one. Author it at platform "
                "scope, or act as a member of the tenant it is for."
            ),
        )
    if scope is SkillScope.TENANT:
        return tenant_id, None
    if auth.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A personal skill needs a user account; this token names none.",
        )
    return tenant_id, auth.user_id


def _authorable(auth: AuthContext) -> list[str]:
    """Return the layers this caller may author at, strongest first.

    A hint for the screen, not the enforcement: the refusal is
    :func:`aegis.settings.resolver.write_setting`'s and it happens on the server whatever
    the form offers.
    """
    from aegis.governance.config import role_rank
    from aegis.governance.security import PLATFORM_ADMIN
    from aegis.governance.types import Role

    layers: list[str] = []
    if auth.fine_role == PLATFORM_ADMIN:
        layers.append(SkillScope.PLATFORM.value)
    if _scope_tenant(auth, None) is not None:
        if role_rank(auth.fine_role) >= role_rank(Role.AI_TEAM.value):
            layers.append(SkillScope.TENANT.value)
        layers.append(SkillScope.USER.value)
    return layers


def _row(skill: Any, *, in_force: bool) -> SkillRow:  # noqa: ANN401 - aegis AgentSkill
    """Project one stored :class:`~aegis.skills.models.AgentSkill` onto the wire."""
    triggers = [str(t) for t in (skill.triggers or ())]
    scope = skill.scope.value if hasattr(skill.scope, "value") else str(skill.scope)
    return SkillRow(
        name=skill.name,
        scope=scope,
        description=skill.description,
        document=render_skill_md(
            name=skill.name,
            description=skill.description,
            body=skill.body,
            triggers=triggers,
        ),
        triggers=triggers,
        in_force=in_force,
        is_safety=bool(skill.is_safety),
        updated_by=skill.updated_by,
    )


def _refusal(exc: Exception) -> HTTPException:
    """Map an authoring refusal to its status code, always carrying the server's sentence."""
    if isinstance(exc, SkillBodyRefusedError | SkillFormatError | SkillScopeError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    if isinstance(exc, SkillNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    from app.api.routes_console import _settings_http_error

    return _settings_http_error(exc)


@skills_router.get("/skills", response_model=SkillsResponse, tags=["skills"])
async def list_authored_skills(auth: AuthContext = Depends(require_auth)) -> SkillsResponse:
    """Return every skill this caller can see, and whether each is currently in force.

    Two reads, deliberately: :func:`aegis.skills.store.list_skills` is what *exists* and
    :func:`aegis.skills.store.resolve_skills` is what is *live*. A management screen that
    could only show the second could never show a skill somebody had switched off, and
    one that could only show the first would be a list of rows with no relationship to
    any run.

    Raises:
        HTTPException: 503 when the skills store is unreachable.
    """
    tenant_id = None if auth.is_platform_staff() else _scope_tenant(auth, None)
    try:
        async with get_sessionmaker()() as session:
            await set_tenant_scope(session, tenant_id)
            rows = await list_skills(
                session, tenant_id=tenant_id, user_id=auth.user_id
            )
            live = {
                s.name
                for s in await resolve_skills(
                    session, tenant_id=tenant_id, user_id=auth.user_id
                )
            }
            # Projected **inside** the session: ``rollback`` expires every attribute on
            # a loaded row, so reading one after it is a DetachedInstanceError rather
            # than a stale value — which is the better failure, and still a failure.
            projected = [_row(row, in_force=row.name in live) for row in rows]
            await session.rollback()
    except SQLAlchemyError as exc:
        logger.error("Skill listing failed.", exc_info=True)  # noqa: TRY400
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The skills store is unreachable, so no skill can be reported. Nothing "
                "is returned rather than a list that might be wrong about what is live."
            ),
        ) from exc
    return SkillsResponse(rows=projected, scopes=_authorable(auth))


@skills_router.post(
    "/skills",
    response_model=SkillWriteResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["skills"],
)
async def author_skill(
    req: SkillWriteRequest, auth: AuthContext = Depends(require_auth)
) -> SkillWriteResponse:
    """Author one skill from a ``SKILL.md``, screening it **before** a row exists.

    The rail is the platform's bound ``check_input`` — the same one ``POST /query`` runs
    on a live question and the same one a memory write runs before storage. A BLOCK is a
    422 with the rail's own reason and nothing is written; a REDACT stores the redacted
    text and reports what was masked.

    Raises:
        HTTPException: 403 when the layer is beyond this caller's reach, 409 when the
            name is taken at that layer, 422 for a malformed document or one the rail
            refused, 503 when the store is unreachable.
    """
    from app.guardrails import check_input

    scope = _scope_of(req.scope)
    try:
        document = parse_skill_md(req.document)
    except SkillFormatError as exc:
        raise _refusal(exc) from exc
    tenant_id, user_id = _target(auth, scope)

    seen: list[Any] = []

    async def _recording_rail(text: str) -> Any:  # noqa: ANN401 - the host's GuardResult
        """The real rail, with its verdict kept so the author can be told about it."""
        result = await check_input(text)
        seen.append(result)
        return result

    try:
        async with get_sessionmaker()() as session:
            await set_tenant_scope(session, tenant_id)
            row = await write_skill(
                session,
                document,
                screen=_recording_rail,
                scope=scope,
                actor_role=auth.fine_role,
                tenant_id=tenant_id,
                user_id=user_id,
                actor_user_id=auth.user_id,
                updated_by=auth.username,
                is_safety=req.is_safety,
                enable=req.enable,
            )
            projected = _row(row, in_force=req.enable)
            await session.commit()
    except (SkillBodyRefusedError, SkillScopeError, SettingError) as exc:
        raise _refusal(exc) from exc
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A skill named {document.name!r} already exists at {scope.value} scope "
                "and could not be replaced."
            ),
        ) from exc
    except SQLAlchemyError as exc:
        logger.error("Skill write failed for %r.", document.name, exc_info=True)  # noqa: TRY400
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The skills store is unreachable, so the skill was not saved.",
        ) from exc

    await _safe_audit(
        "skills.author",
        auth,
        payload={
            "name": document.name,
            "scope": scope.value,
            "enabled": req.enable,
            "is_safety": req.is_safety,
        },
        tenant_id=tenant_id,
    )
    verdict = "pass"
    redactions: list[str] = []
    for result in seen:
        redactions.extend(str(k) for k in getattr(result, "redactions", ()) or ())
        value = str(getattr(result.verdict, "value", result.verdict))
        if value != "pass":
            verdict = value
    return SkillWriteResponse(
        row=projected, verdict=verdict, redactions=sorted(set(redactions))
    )


@skills_router.put(
    "/skills/{scope}/{name}/active", response_model=SkillRow, tags=["skills"]
)
async def set_skill_active(
    scope: str, name: str, active: bool = True, auth: AuthContext = Depends(require_auth)
) -> SkillRow:
    """Put one skill in force at a layer, or take it out.

    Writing ``skills.enabled`` at *your own* layer is the only thing this does, which is
    why a tenant admin cannot switch off a platform safety skill with it: the effective
    set is a union, and a union has no way to express a removal from a layer above.

    Raises:
        HTTPException: 403 when the layer is beyond this caller's reach, 404 for an
            unknown skill, 503 when the store is unreachable.
    """
    layer = _scope_of(scope)
    tenant_id, user_id = _target(auth, layer)
    try:
        async with get_sessionmaker()() as session:
            await set_tenant_scope(session, tenant_id)
            rows = await list_skills(
                session, scope=layer, tenant_id=tenant_id, user_id=user_id
            )
            row = next((r for r in rows if r.name == name), None)
            if row is None:
                raise SkillNotFoundError(
                    f"No skill named {name!r} is authored at {layer.value} scope."
                )
            await set_active(
                session,
                name,
                active=active,
                scope=layer,
                actor_role=auth.fine_role,
                tenant_id=tenant_id,
                user_id=user_id,
                actor_user_id=auth.user_id,
                updated_by=auth.username,
            )
            live = {
                s.name
                for s in await resolve_skills(
                    session, tenant_id=tenant_id, user_id=user_id
                )
            }
            projected = _row(row, in_force=name in live)
            await session.commit()
    except (SkillNotFoundError, SettingError) as exc:
        raise _refusal(exc) from exc
    except SQLAlchemyError as exc:
        logger.error("Skill activation failed for %r.", name, exc_info=True)  # noqa: TRY400
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The skills store is unreachable, so nothing was changed.",
        ) from exc
    await _safe_audit(
        "skills.activate",
        auth,
        payload={"name": name, "scope": layer.value, "active": active},
        tenant_id=tenant_id,
    )
    return projected


@skills_router.delete(
    "/skills/{scope}/{name}", status_code=status.HTTP_204_NO_CONTENT, tags=["skills"]
)
async def remove_skill(
    scope: str, name: str, auth: AuthContext = Depends(require_auth)
) -> None:
    """Delete one authored skill and take its name out of force, in one transaction.

    Raises:
        HTTPException: 403 when the layer is beyond this caller's reach, 404 for an
            unknown skill, 503 when the store is unreachable.
    """
    layer = _scope_of(scope)
    tenant_id, user_id = _target(auth, layer)
    try:
        async with get_sessionmaker()() as session:
            await set_tenant_scope(session, tenant_id)
            await delete_skill(
                session,
                name,
                scope=layer,
                actor_role=auth.fine_role,
                tenant_id=tenant_id,
                user_id=user_id,
                actor_user_id=auth.user_id,
                updated_by=auth.username,
            )
            await session.commit()
    except (SkillNotFoundError, SettingError) as exc:
        raise _refusal(exc) from exc
    except SQLAlchemyError as exc:
        logger.error("Skill delete failed for %r.", name, exc_info=True)  # noqa: TRY400
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The skills store is unreachable, so nothing was deleted.",
        ) from exc
    await _safe_audit(
        "skills.delete",
        auth,
        payload={"name": name, "scope": layer.value},
        tenant_id=tenant_id,
    )


def mount(target: APIRouter) -> None:
    """Attach this module's routes to ``target``, skipping any already present.

    Idempotent for the same reason :func:`app.api.routes_seats.mount` is: mounting twice
    puts a second, shadowed copy of every handler in the served table, invisible at
    runtime and confusing exactly where the route-coverage test reads.
    """
    existing = {
        (route.path, frozenset(getattr(route, "methods", ()) or ()))
        for route in target.routes
    }
    for route in skills_router.routes:
        key = (route.path, frozenset(getattr(route, "methods", ()) or ()))
        if key not in existing:
            target.routes.append(route)
