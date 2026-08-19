"""The per-tenant prompt control plane — see the live prompt, change it, prove which ran.

§7.7, second half. The first half was the read path (``aegis.ops.registry`` is now keyed
on ``(tenant_id, prompt_key)`` everywhere, in both directions), and this is the surface
that path made safe to build. The north-star test it answers: **an operator changes the
live system prompt without a deploy, and can see that it happened** — what is live now,
what was live before, and which version a given run was actually served.

``GET /llmops/prompts``
    One call renders the whole screen for one key: the active version with its body, the
    version history, the *platform floor* that is composed underneath every one of them,
    and the scope the answer was read in.
``POST /llmops/prompts/versions``
    Write a new draft of the task prompt.
``POST /llmops/prompts/versions/{version_id}/activate``
    Make one version live for this tenant, now. Refreshes **that tenant's** cache slot
    and nobody else's, and leaves an audit row.
``POST /llmops/prompts/rollback``
    One call back to the previously-live version.
``GET /llmops/runs`` / ``GET /llmops/runs/{run_id}``
    Which version each recent run was served, and which one a *named* run was served
    (see :mod:`app.ops.prompt_runs` for what "recent" honestly means).

**Where the tenant comes from.** ``AuthContext.tenant_scope()`` — the sealed scope, never
a body field or a query parameter (§7.16 row 12). A tenant admin has exactly one scope
and cannot name another. Platform staff (``platform_admin`` / ``ai_team``) may pass
``tenant_id`` as a *selector*, which is the tenant picker §7.7 asks for; that is an
authority they already hold, not a hole, and :func:`_scope` is the single place it is
decided.

**Where the platform floor sits, and why a tenant cannot reach it.** A prompt version is
the *task* half only. The platform composes :func:`app.adapter.render_platform_floor`
underneath it at render time — the safety preamble, plus the persona's data scope and
tool allowlist derived from enforcement rather than typed by anyone — so there is no
version anyone can promote that removes it. Three things enforce that line here, on the
server:

1. A tenant may write only **its own** ``tenant_id``. The platform rows (``tenant_id IS
   NULL``) are the fallback every tenant without a version of its own resolves to;
   writing them is platform staff only.
2. A tenant may write only a **persona** key. :data:`_PLATFORM_ONLY_PREFIXES` names the
   keys that drive platform machinery — the guardrail layers and the sub-agent roster —
   and they are refused with a sentence saying why.
3. The floor itself is **not a row in this table at all**, so no key reaches it. It is
   returned by ``GET /llmops/prompts`` as ``floor`` precisely so the operator can read
   what they cannot edit, rather than discovering it by experiment.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.routes import AuthContext, _safe_audit, require_auth
from app.core.security import PLATFORM_ADMIN, TENANT_ADMIN

logger = logging.getLogger(__name__)

llmops_router = APIRouter()

#: Prompt keys a tenant may never write. These are not task prompts: they instruct the
#: guardrail layers and the platform's own sub-agent roster, and a tenant editing them
#: would be editing the controls rather than the work — §7.16 rows 7 and 14.
_PLATFORM_ONLY_PREFIXES = ("guardrail:", "safety:", "platform:", "subagent:")

#: Said to a tenant admin who aims at a platform-owned key. A bare 403 on a governance
#: decision is indistinguishable from a bug.
_PLATFORM_KEY_REFUSAL = (
    "This prompt belongs to the platform, not to a tenant: it instructs the guardrail "
    "layers and the platform's own agents rather than your task. You can write and "
    "activate versions of your own task prompts; the platform floor is composed "
    "underneath every one of them and is not editable from here."
)

_NOT_YOUR_TENANT = (
    "That prompt version belongs to another scope. You can only read and activate "
    "versions in your own tenant."
)

#: Clamp on the version history and the run list.
_LIMIT_MAX = 200


# ─────────────────────────────────────────────────────────────────────────────
# Authorisation and scope
# ─────────────────────────────────────────────────────────────────────────────


def require_llmops_operator(auth: AuthContext = Depends(require_auth)) -> AuthContext:
    """Admit a principal who may operate prompts: a tenant admin, or platform staff.

    A tenant admin operates *their* prompts (§7.7 mounts the surface on that portal); the
    ``ai_team`` and ``platform_admin`` tiers operate the platform's and, with the
    selector, any tenant's. A client or a plain member is refused: the active system
    prompt is the instruction set behind every answer, and reading it is a map of what
    the assistant will and will not do.
    """
    if auth.fine_role in (PLATFORM_ADMIN, TENANT_ADMIN) or (
        auth.is_platform_staff() and auth.role.value == "ai_team"
    ):
        return auth
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            "Operating prompt versions requires a tenant administrator, the AI team or "
            "a platform administrator."
        ),
    )


def _is_platform_staff(auth: AuthContext) -> bool:
    """Whether ``auth`` may aim at a tenant other than its own."""
    return auth.is_platform_staff() and (
        auth.fine_role == PLATFORM_ADMIN or auth.role.value == "ai_team"
    )


def _scope(auth: AuthContext, requested: int | None) -> int | None:
    """Return the tenant scope this request reads and writes in.

    Platform staff may name a tenant — that is the §7.7 tenant selector, and their
    authority already spans tenants. Everybody else gets their own sealed scope and a 403
    if they name a different one, so ``tenant_id`` is never an isolation key taken from
    the wire (§7.16 row 12).

    Args:
        auth: The authenticated principal.
        requested: The ``tenant_id`` query parameter, or ``None``.

    Returns:
        The tenant scope: an id, or ``None`` for the platform scope.

    Raises:
        HTTPException: 403 when a tenant-bound principal names another tenant, or when
            the principal has no tenant to scope to.
    """
    from aegis.retrieval.types import UntenantedPrincipalError, tenant_filter

    if _is_platform_staff(auth):
        return requested
    try:
        own = tenant_filter(auth.tenant_scope())
    except UntenantedPrincipalError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This account is not bound to a tenant, so there is no prompt scope to "
                "read. Ask an administrator to assign it to a tenant."
            ),
        ) from exc
    if requested is not None and requested != own:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_NOT_YOUR_TENANT)
    return own


def _guard_writable_key(auth: AuthContext, prompt_key: str) -> None:
    """Refuse a write aimed at a platform-owned prompt key (§7.16 rows 7 and 14)."""
    if _is_platform_staff(auth):
        return
    if any(prompt_key.startswith(prefix) for prefix in _PLATFORM_ONLY_PREFIXES):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=_PLATFORM_KEY_REFUSAL
        )


def _guard_writable_scope(auth: AuthContext, scope: int | None) -> None:
    """Refuse a tenant principal writing into the platform (``tenant_id IS NULL``) scope."""
    if _is_platform_staff(auth):
        return
    if scope is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=_PLATFORM_KEY_REFUSAL
        )


def _require_stores() -> None:
    """Raise a clean 503 when the durable stores are off (a prompt cannot be faked)."""
    from app.config import get_settings

    if not get_settings().stores_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Prompt versions need the durable stores; this deployment is running in "
                "offline/lite mode."
            ),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Wire shapes
# ─────────────────────────────────────────────────────────────────────────────


class PromptVersionRow(BaseModel):
    """One version in a tenant's history."""

    id: int
    version: int
    status: str
    is_active: bool = Field(alias="isActive")
    system_prompt: str = Field(alias="systemPrompt")
    created_by: str | None = Field(default=None, alias="createdBy")
    notes: str | None = None
    created_at: str | None = Field(default=None, alias="createdAt")
    activated_at: str | None = Field(default=None, alias="activatedAt")

    model_config = ConfigDict(populate_by_name=True)


class PromptScreen(BaseModel):
    """Everything one prompt key's screen needs, read in one sealed scope."""

    prompt_key: str = Field(alias="promptKey")
    tenant_id: int | None = Field(default=None, alias="tenantId")
    scope_label: str = Field(alias="scopeLabel")
    active_version: int | None = Field(default=None, alias="activeVersion")
    active_prompt: str | None = Field(default=None, alias="activePrompt")
    #: ``True`` when nothing is active in this scope and the shipped prompt is running.
    on_shipped_prompt: bool = Field(default=True, alias="onShippedPrompt")
    #: The part no tenant may edit, returned so it can be read rather than guessed.
    floor: str = ""
    versions: list[PromptVersionRow] = Field(default_factory=list)
    editable: bool = True

    model_config = ConfigDict(populate_by_name=True)


class PromptDraftRequest(BaseModel):
    """Write a new draft of a task prompt."""

    prompt_key: str = Field(alias="promptKey", min_length=1, max_length=128)
    system_prompt: str = Field(alias="systemPrompt", min_length=1, max_length=20_000)
    notes: str | None = Field(default=None, max_length=2_000)
    tenant_id: int | None = Field(
        default=None,
        alias="tenantId",
        description=(
            "Platform staff only — the tenant selector. Ignored for a tenant-bound "
            "principal, whose scope is sealed."
        ),
    )

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class PromptRollbackRequest(BaseModel):
    """Revert a key to the version that was live before the current one."""

    prompt_key: str = Field(alias="promptKey", min_length=1, max_length=128)
    tenant_id: int | None = Field(default=None, alias="tenantId")

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class PromptRunRow(BaseModel):
    """Which prompt version one run was served."""

    run_id: str = Field(alias="runId")
    prompt_key: str = Field(alias="promptKey")
    version: int | None = None
    source: str
    ts: str

    model_config = ConfigDict(populate_by_name=True)


class PromptRunsResponse(BaseModel):
    """Recent runs and the prompt each was served."""

    rows: list[PromptRunRow] = Field(default_factory=list)
    #: Says out loud that this is a per-process window, so nobody reads it as an archive.
    window: str = (
        "Runs served by this API process since it started. The durable per-run record "
        "is run_events, which agent runs are not yet written to."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _iso(ts: object) -> str | None:
    """Render a (possibly naive) timestamp as an ISO 8601 UTC string, or ``None``."""
    from datetime import UTC, datetime

    if not isinstance(ts, datetime):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).isoformat()


def _scope_label(tenant_id: int | None) -> str:
    """Name the scope an answer was read in, so the screen can show it."""
    return "Platform (the default every tenant falls back to)" if tenant_id is None else (
        f"Tenant {tenant_id}"
    )


def _floor_for(prompt_key: str) -> str:
    """Return the platform floor composed underneath ``prompt_key``'s versions."""
    from app.adapter import get_persona, render_platform_floor

    try:
        persona = get_persona(prompt_key)
    except KeyError:
        persona = None
    return render_platform_floor(persona)


def _row(pv: Any, active_id: int | None) -> PromptVersionRow:  # noqa: ANN401 - PromptVersion
    """Render one ``PromptVersion`` on the wire."""
    status_value = pv.status.value if hasattr(pv.status, "value") else str(pv.status)
    return PromptVersionRow(
        id=pv.id,
        version=pv.version,
        status=status_value,
        is_active=pv.id == active_id,
        system_prompt=pv.system_prompt,
        created_by=pv.created_by,
        notes=pv.notes,
        created_at=_iso(pv.created_at),
        activated_at=_iso(pv.activated_at),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@llmops_router.get("/llmops/prompts", response_model=PromptScreen, tags=["llmops"])
async def llmops_prompt_screen(
    prompt_key: str = Query(..., min_length=1, max_length=128),
    tenant_id: int | None = Query(default=None),
    auth: AuthContext = Depends(require_llmops_operator),
) -> PromptScreen:
    """Return the live prompt, its history and the floor, for one key in one scope."""
    _require_stores()
    scope = _scope(auth, tenant_id)
    from app.data.session import get_sessionmaker
    from app.ops import registry

    async with get_sessionmaker()() as session:
        active = await registry.get_active(session, prompt_key, scope)
        versions = await registry.list_versions(session, prompt_key, scope)
    return PromptScreen(
        prompt_key=prompt_key,
        tenant_id=scope,
        scope_label=_scope_label(scope),
        active_version=active.version if active is not None else None,
        active_prompt=active.system_prompt if active is not None else None,
        on_shipped_prompt=active is None,
        floor=_floor_for(prompt_key),
        versions=[_row(pv, active.id if active is not None else None) for pv in versions],
        editable=_is_platform_staff(auth)
        or not any(prompt_key.startswith(p) for p in _PLATFORM_ONLY_PREFIXES),
    )


@llmops_router.post(
    "/llmops/prompts/versions",
    response_model=PromptVersionRow,
    status_code=status.HTTP_201_CREATED,
    tags=["llmops"],
)
async def llmops_create_version(
    body: PromptDraftRequest = Body(...),
    auth: AuthContext = Depends(require_llmops_operator),
) -> PromptVersionRow:
    """Write a new draft version of a task prompt in the caller's scope."""
    _require_stores()
    scope = _scope(auth, body.tenant_id)
    _guard_writable_key(auth, body.prompt_key)
    _guard_writable_scope(auth, scope)
    from app.data.session import get_sessionmaker
    from app.ops import registry

    async with get_sessionmaker()() as session:
        pv = await registry.create_draft(
            session,
            prompt_key=body.prompt_key,
            system_prompt=body.system_prompt,
            notes=body.notes,
            created_by=auth.username,
            tenant_id=scope,
        )
        await session.commit()
        row = _row(pv, None)
    await _safe_audit(
        "llmops.prompt.draft",
        auth,
        payload={"prompt_key": body.prompt_key, "version": row.version},
        tenant_id=scope,
    )
    return row


@llmops_router.post(
    "/llmops/prompts/versions/{version_id}/activate",
    response_model=PromptScreen,
    tags=["llmops"],
)
async def llmops_activate_version(
    version_id: int,
    auth: AuthContext = Depends(require_llmops_operator),
) -> PromptScreen:
    """Make one version live for its tenant — the no-deploy change, and its evidence.

    The scope is taken from the **row**, then checked against the caller's sealed scope,
    so an id belonging to another tenant is a 403 rather than a cross-tenant activation.
    After the commit, only that tenant's cache slot is re-read
    (``refresh_cache(session, scope)``): a whole-cache refresh here would drop every other
    tenant to the shipped prompt until the next restart.
    """
    _require_stores()
    from app.data.models import PromptVersion
    from app.data.session import get_sessionmaker
    from app.ops import registry

    async with get_sessionmaker()() as session:
        pv = await session.get(PromptVersion, version_id)
        if pv is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No prompt version with id={version_id}.",
            )
        scope = _scope(auth, pv.tenant_id)
        if scope != pv.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=_NOT_YOUR_TENANT
            )
        _guard_writable_key(auth, pv.prompt_key)
        _guard_writable_scope(auth, scope)
        prompt_key = pv.prompt_key
        version = pv.version
        await registry.promote(session, version_id)
        await session.commit()
        # This tenant's slot only — invalidation is per tenant for the same reason the
        # key is.
        await registry.refresh_cache(session, scope)
    await _safe_audit(
        "llmops.prompt.activate",
        auth,
        payload={"prompt_key": prompt_key, "version": version},
        tenant_id=scope,
    )
    return await llmops_prompt_screen(prompt_key=prompt_key, tenant_id=scope, auth=auth)


@llmops_router.post(
    "/llmops/prompts/rollback", response_model=PromptScreen, tags=["llmops"]
)
async def llmops_rollback(
    body: PromptRollbackRequest = Body(...),
    auth: AuthContext = Depends(require_llmops_operator),
) -> PromptScreen:
    """Revert one key to the version that was live before the current one."""
    _require_stores()
    scope = _scope(auth, body.tenant_id)
    _guard_writable_key(auth, body.prompt_key)
    _guard_writable_scope(auth, scope)
    from app.data.session import get_sessionmaker
    from app.ops import registry

    async with get_sessionmaker()() as session:
        reverted = await registry.rollback(session, body.prompt_key, scope)
        if reverted is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "There is no earlier version to roll back to for this prompt in "
                    "this scope."
                ),
            )
        version = reverted.version
        await session.commit()
        await registry.refresh_cache(session, scope)
    await _safe_audit(
        "llmops.prompt.rollback",
        auth,
        payload={"prompt_key": body.prompt_key, "version": version},
        tenant_id=scope,
    )
    return await llmops_prompt_screen(
        prompt_key=body.prompt_key, tenant_id=scope, auth=auth
    )


@llmops_router.get("/llmops/runs", response_model=PromptRunsResponse, tags=["llmops"])
async def llmops_runs(
    limit: int = Query(default=25, ge=1, le=_LIMIT_MAX),
    tenant_id: int | None = Query(default=None),
    auth: AuthContext = Depends(require_llmops_operator),
) -> PromptRunsResponse:
    """Return which prompt version each of this tenant's recent runs was served."""
    scope = _scope(auth, tenant_id)
    from app.ops import prompt_runs

    return PromptRunsResponse(
        rows=[
            PromptRunRow(
                run_id=entry.run_id,
                prompt_key=entry.prompt_key,
                version=entry.version,
                source=entry.source,
                ts=entry.ts.isoformat(),
            )
            for entry in prompt_runs.recent(scope, limit=limit)
        ]
    )


@llmops_router.get(
    "/llmops/runs/{run_id}", response_model=PromptRunRow, tags=["llmops"]
)
async def llmops_run(
    run_id: str,
    tenant_id: int | None = Query(default=None),
    auth: AuthContext = Depends(require_llmops_operator),
) -> PromptRunRow:
    """Return which prompt version one run was served.

    The scope is a **filter, not a hint**: a run id that exists but belongs to another
    tenant answers 404, so a guessed or leaked id cannot name another tenant's prompt.
    """
    scope = _scope(auth, tenant_id)
    from app.ops import prompt_runs

    entry = prompt_runs.resolve(run_id, scope)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "This process has no prompt attribution for that run. Runs are recorded "
                "in memory since the API started; the durable per-run record is "
                "run_events, which agent runs are not yet written to."
            ),
        )
    return PromptRunRow(
        run_id=entry.run_id,
        prompt_key=entry.prompt_key,
        version=entry.version,
        source=entry.source,
        ts=entry.ts.isoformat(),
    )


def mount(target: APIRouter) -> None:
    """Attach this module's routes to ``target`` as real ``APIRoute`` objects.

    Idempotent, like :mod:`app.api.routes_redteam`: a route already on ``target`` at the
    same path and methods is skipped, so mounting twice cannot put a second shadowed copy
    of every handler in the served table.

    Args:
        target: The application's main router, extended in place.
    """
    present = {
        (route.path, frozenset(getattr(route, "methods", ()) or ()))
        for route in target.routes
    }
    target.routes.extend(
        route
        for route in llmops_router.routes
        if (route.path, frozenset(getattr(route, "methods", ()) or ())) not in present
    )
