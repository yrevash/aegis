"""Prompt-version registry — versioned, reversible system prompts + an active cache.

This is the seam that lets the LLM-Ops loop feed an improved system prompt back into the
harness *safely*: the optimizer/human writes a ``draft`` :class:`PromptVersion`; a gated
``promote`` makes it the single ``active`` version for its ``(tenant_id, prompt_key)``
and archives the prior one (so ``rollback`` is a one-call revert). The harness never
reads the DB on the hot path — it reads a process-wide **active cache** synchronously
(:func:`get_cached_active`), which ``promote``/``rollback`` update and startup/refresh
repopulate. When no active version exists, the injected floor prompt is the baseline
(see :mod:`aegis.ops.config`).

**Every lookup in this module is keyed on the tenant as well as the prompt key, and that
is the whole point of the module.** It was not: the cache was a ``dict[prompt_key]``, the
``ACTIVE`` lookup had an *optional* tenant filter that defaulted to "any tenant", and
``promote`` archived every active row sharing a ``prompt_key`` regardless of who owned
it. With one tenant in the database none of that could manifest. With two it leaks in
three directions at once — tenant B's prompt served to tenant A's run, tenant A's history
listed to tenant B, and tenant A's promotion silently archiving tenant B's live version —
and the screen on top of it would look correct the whole time.

So the tenant is part of every key here, in both directions:

* **Reads** — :func:`get_cached_active`, :func:`get_active` and :func:`list_versions`
  take the tenant explicitly. ``tenant_id=None`` means the **platform** scope (the rows
  whose ``tenant_id`` is NULL), never "whichever row turns up first"; a tenant with no
  version of its own falls back to that platform row, which is the one prompt it is
  entitled to see.
* **Writes** — :func:`promote` and :func:`rollback` derive the tenant from the row being
  activated and confine their archive/reactivate to it, and the cache entry they publish
  is that tenant's alone. Activating for tenant A therefore cannot serve, archive or
  invalidate anything of tenant B's.

The tenant itself is never a parameter a client supplies: a host binds it from its sealed
request scope (see ``app.ops.registry``, which defaults it from the governance context).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import event, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aegis.ops.config import apply_tenant_scope
from aegis.ops.models import PromptStatus, PromptVersion

logger = logging.getLogger(__name__)

#: ``(tenant_id, prompt_key)`` → ``(system_prompt, config, version)``. Process-wide; read
#: synchronously by the harness. Empty until refreshed → callers fall back to the injected
#: floor. The tenant is **in the key**: a ``dict[prompt_key]`` served whichever tenant
#: promoted last to every tenant in the process.
_ACTIVE_CACHE: dict[tuple[int | None, str], tuple[str, dict[str, Any], int]] = {}

#: The platform scope — the rows whose ``tenant_id`` is NULL. A tenant with no version of
#: its own resolves to this one, which is the only prompt other than its own it may see.
PLATFORM_SCOPE: int | None = None

#: How many times :func:`create_draft` re-reads ``max(version)`` and retries after a
#: unique-index collision with a concurrent writer.
_VERSION_COLLISION_RETRIES = 5

#: Distinguishes "no tenant argument given" from ``None`` (the platform scope), which is
#: a real, addressable tenant scope here rather than an absence.
_UNSET_TENANT: Any = object()


def get_cached_active(
    prompt_key: str, tenant_id: int | None = None
) -> tuple[str, dict[str, Any], int] | None:
    """Return the cached active ``(system_prompt, config, version)`` for one tenant's key.

    Synchronous and hot-path-safe. Resolution is ``(tenant_id, prompt_key)`` first, then
    the **platform** row ``(None, prompt_key)`` — a tenant that has never written a
    version of its own runs on the platform's, which is the only other prompt it is
    entitled to. ``None`` when neither is cached; the caller then falls back to the
    injected floor.

    Args:
        prompt_key: The registry key (persona / sub-agent) being resolved.
        tenant_id: The **sealed** tenant scope of the run. Never client-supplied — a host
            binds it from its request scope. ``None`` asks for the platform row only.

    Returns:
        The active ``(system_prompt, config, version)``, or ``None``.
    """
    hit = _ACTIVE_CACHE.get((tenant_id, prompt_key))
    if hit is not None or tenant_id is None:
        return hit
    return _ACTIVE_CACHE.get((PLATFORM_SCOPE, prompt_key))


def clear_cache(tenant_id: Any = _UNSET_TENANT) -> None:  # noqa: ANN401 - int | None | _UNSET_TENANT
    """Drop cached active versions — the whole cache, or one tenant's entries.

    Args:
        tenant_id: Omit to drop everything (test isolation, a full startup refresh).
            Pass a tenant (``None`` for the platform scope) to drop **only** that
            tenant's entries, which is what keeps an activation for one tenant from
            evicting — or worse, re-serving — another tenant's live prompt.
    """
    if tenant_id is _UNSET_TENANT:
        _ACTIVE_CACHE.clear()
        return
    for key in [k for k in _ACTIVE_CACHE if k[0] == tenant_id]:
        del _ACTIVE_CACHE[key]


def _cache(pv: PromptVersion) -> None:
    _ACTIVE_CACHE[(pv.tenant_id, pv.prompt_key)] = (
        pv.system_prompt,
        dict(pv.config or {}),
        pv.version,
    )


def _tenant_clause(tenant_id: int | None) -> Any:  # noqa: ANN401 - SQLAlchemy clause
    """Return the WHERE clause pinning a query to exactly one tenant scope.

    ``tenant_id=None`` is the **platform** scope (``tenant_id IS NULL``), not "any
    tenant". Spelling that out is the fix: the previous ``if tenant_id is not None``
    guard meant an unscoped caller read whichever tenant's row the planner returned
    first.
    """
    if tenant_id is None:
        return PromptVersion.tenant_id.is_(None)
    return PromptVersion.tenant_id == tenant_id


def _cache_on_commit(session: AsyncSession, pv: PromptVersion) -> None:
    """Publish ``pv`` to the active cache **only if the caller's transaction commits**.

    ``promote``/``rollback`` deliberately leave the transaction open for the caller, so
    caching at flush time publishes a prompt that may never be committed: a caller
    rollback (or a crash) would leave ``_ACTIVE_CACHE`` serving a phantom system prompt
    to every run through the synchronous hot path :func:`get_cached_active`, and nothing
    would correct it until the next :func:`refresh_cache` — which only runs at startup.

    The published key is ``(pv.tenant_id, pv.prompt_key)``, so a commit for one tenant
    touches exactly one entry and leaves every other tenant's live prompt alone.

    Binding to the session's ``after_commit`` makes the cache exactly as durable as the
    row it mirrors. The listener is one-shot, and the payload is snapshotted now (the
    ORM object is expired by the commit).
    """
    key = (pv.tenant_id, pv.prompt_key)
    payload = (pv.system_prompt, dict(pv.config or {}), pv.version)
    # AsyncSession proxies a sync Session; events are registered on the sync one.
    target = getattr(session, "sync_session", session)

    def _publish(_session: Any) -> None:  # noqa: ANN401 - SQLAlchemy Session
        _ACTIVE_CACHE[key] = payload

    event.listen(target, "after_commit", _publish, once=True)


async def refresh_cache(
    session: AsyncSession,
    tenant_id: Any = _UNSET_TENANT,  # noqa: ANN401 - int | None | _UNSET_TENANT
) -> int:
    """Reload ``active`` versions into the cache (startup, or after one tenant changes).

    Args:
        session: An open session. **Unscoped** for the whole-process refresh: the
            startup warm-up loads every tenant's active row into its own cache slot.
        tenant_id: Omit to reload everything (startup). Pass a tenant (``None`` for the
            platform scope) to re-read **only** that tenant's rows and replace **only**
            that tenant's cache entries — the invalidation half of per-tenant keying. A
            whole-cache clear on one tenant's activation would drop every other tenant to
            the floor prompt until the next restart.

    Returns:
        The number of active versions loaded.
    """
    # §9.5. The startup warm-up is a genuinely platform-wide read — it loads every
    # tenant's active prompt into that tenant's own cache slot — and it used to make
    # that claim by binding nothing at all, which is indistinguishable from a path that
    # forgot to. It says so now: ``None`` is the platform scope, and under
    # ``RLS_FAIL_CLOSED`` it is the only thing that keeps the warm-up loading anything.
    await apply_tenant_scope(
        session, None if tenant_id is _UNSET_TENANT else tenant_id
    )
    stmt = select(PromptVersion).where(PromptVersion.status == PromptStatus.ACTIVE)
    if tenant_id is not _UNSET_TENANT:
        stmt = stmt.where(_tenant_clause(tenant_id))
    rows = (await session.execute(stmt)).scalars().all()
    clear_cache(tenant_id)
    for pv in rows:
        _cache(pv)
    return len(rows)


async def create_draft(
    session: AsyncSession,
    *,
    prompt_key: str,
    system_prompt: str,
    config: dict[str, Any] | None = None,
    parent_version: int | None = None,
    created_by: str | None = None,
    notes: str | None = None,
    tenant_id: int | None = None,
) -> PromptVersion:
    """Write a new ``draft`` version (``version`` auto-increments per tenant + key).

    Version numbers are **per ``(tenant_id, prompt_key)``**, so every tenant's history
    starts at 1 and reads as its own. They were global per key, which under RLS is not
    merely untidy: a tenant-scoped session reads ``max(version)`` over the rows it can
    see, and then collides forever with an invisible row another tenant already holds —
    every retry recomputes the same number.

    ``max(version) + 1`` is check-then-act against the ``(tenant_id, prompt_key, version)``
    unique index, so two concurrent diagnose passes can pick the same number. The loser's
    ``IntegrityError`` would otherwise surface *after* its optimizer LLM call has
    already been paid for and throw the rewritten prompt away — so the insert is
    retried inside a SAVEPOINT with a freshly-read ``max(version)``, and only a
    persistently-contended key gives up (loudly).

    Raises:
        IntegrityError: If the version could not be allocated within
            :data:`_VERSION_COLLISION_RETRIES` retries (re-raised, never swallowed).
    """
    last_error: IntegrityError | None = None
    for attempt in range(_VERSION_COLLISION_RETRIES + 1):
        current_max = (
            await session.execute(
                select(func.max(PromptVersion.version)).where(
                    PromptVersion.prompt_key == prompt_key,
                    _tenant_clause(tenant_id),
                )
            )
        ).scalar() or 0
        pv = PromptVersion(
            tenant_id=tenant_id,
            prompt_key=prompt_key,
            version=current_max + 1,
            system_prompt=system_prompt,
            config=config or {},
            status=PromptStatus.DRAFT,
            parent_version=parent_version,
            created_by=created_by,
            notes=notes,
        )
        try:
            # A SAVEPOINT so a collision rolls back only this INSERT — the caller's
            # outer transaction (which it owns and may still commit) stays usable.
            async with session.begin_nested():
                session.add(pv)
                await session.flush()
        except IntegrityError as exc:
            last_error = exc
            logger.warning(
                "create_draft: version %d for prompt_key=%s collided with a concurrent "
                "writer (attempt %d); retrying",
                current_max + 1,
                prompt_key,
                attempt + 1,
            )
            continue
        return pv
    raise last_error  # type: ignore[misc]  # unreachable unless a collision occurred


async def get_active(
    session: AsyncSession, prompt_key: str, tenant_id: int | None = None
) -> PromptVersion | None:
    """Return one tenant's single ``active`` version for ``prompt_key``, or ``None``.

    Args:
        session: An open session.
        prompt_key: The registry key.
        tenant_id: The scope to read. ``None`` is the **platform** row (``tenant_id IS
            NULL``) — it used to mean "any tenant", which is how a tenant admin's screen
            would have rendered another tenant's live prompt as their own.

    Returns:
        The active version in that scope, or ``None``.
    """
    stmt = select(PromptVersion).where(
        PromptVersion.prompt_key == prompt_key,
        PromptVersion.status == PromptStatus.ACTIVE,
        _tenant_clause(tenant_id),
    )
    return (await session.execute(stmt)).scalars().first()


async def list_versions(
    session: AsyncSession, prompt_key: str, tenant_id: int | None = None
) -> list[PromptVersion]:
    """Return one tenant's versions for ``prompt_key``, newest version first.

    Args:
        session: An open session.
        prompt_key: The registry key.
        tenant_id: The scope to list. ``None`` is the **platform** scope; it is not a
            wildcard, so no caller can list another tenant's history by omitting it.

    Returns:
        That tenant's versions, newest first.
    """
    return list(
        (
            await session.execute(
                select(PromptVersion)
                .where(
                    PromptVersion.prompt_key == prompt_key,
                    _tenant_clause(tenant_id),
                )
                .order_by(PromptVersion.version.desc())
            )
        ).scalars().all()
    )


async def promote(session: AsyncSession, version_id: int) -> PromptVersion:
    """Activate ``version_id`` for its own tenant, archiving that tenant's prior active.

    Enforces one-active-per ``(tenant_id, prompt_key)`` and stamps ``activated_at``. The
    scope is taken from the row itself — never from an argument a caller could get wrong
    — so promoting Acme's draft archives Acme's previous version and **nothing** of any
    other tenant's. The archive statement used to match on ``prompt_key`` alone, which
    meant one tenant activating a prompt silently retired every other tenant's live
    version of the same key while their caches kept serving it: a leak and a
    denial-of-service in one statement.

    The transaction is left open for the caller to commit; the active cache is updated
    **on that commit** (see :func:`_cache_on_commit`), never before, so a caller rollback
    can never leave a never-committed prompt serving live traffic.

    Raises:
        ValueError: If ``version_id`` does not exist.
    """
    pv = await session.get(PromptVersion, version_id)
    if pv is None:
        raise ValueError(f"No PromptVersion with id={version_id}")
    await session.execute(
        update(PromptVersion)
        .where(
            PromptVersion.prompt_key == pv.prompt_key,
            _tenant_clause(pv.tenant_id),
            PromptVersion.status == PromptStatus.ACTIVE,
            PromptVersion.id != pv.id,
        )
        .values(status=PromptStatus.ARCHIVED)
    )
    pv.status = PromptStatus.ACTIVE
    pv.activated_at = datetime.now(UTC)
    await session.flush()
    _cache_on_commit(session, pv)
    return pv


async def rollback(
    session: AsyncSession, prompt_key: str, tenant_id: int | None = None
) -> PromptVersion | None:
    """Reactivate one tenant's most-recently-active prior version (one-call revert).

    Archives the current active (if any) and reactivates the version that was live
    before it. Returns the newly-active version, or ``None`` if there is no prior
    version to roll back to. Everything is confined to ``tenant_id`` (``None`` = the
    platform scope): unscoped, a revert could have reached back into another tenant's
    history and put *their* archived prompt live under this tenant's key.

    ``activated_at`` doubles as the **rollback-eligibility marker**, and rolling back
    *clears* it on the version being rolled back FROM. That is what makes repeated
    rollbacks walk the history backwards instead of oscillating: the naive version
    stamped the revert target with ``now()`` and left the archived (bad) version with an
    older-but-present ``activated_at``, so the next rollback — ordering archived rows by
    ``activated_at DESC`` — picked the very version just rolled back from and put the
    broken prompt straight back into production. A cleared marker also keeps a *rejected
    draft* (ARCHIVED but never live) off the revert path, as before. The audit trail is
    preserved in ``notes``, which records each deactivation.
    """
    # Only a version that was ACTUALLY LIVE and has not itself been rolled back
    # (activated_at still set) is a valid revert target.
    prev = (
        await session.execute(
            select(PromptVersion)
            .where(
                PromptVersion.prompt_key == prompt_key,
                _tenant_clause(tenant_id),
                PromptVersion.status == PromptStatus.ARCHIVED,
                PromptVersion.activated_at.is_not(None),
            )
            .order_by(PromptVersion.activated_at.desc(), PromptVersion.version.desc())
        )
    ).scalars().first()
    if prev is None:
        return None
    now = datetime.now(UTC)
    current = await get_active(session, prompt_key, tenant_id)
    if current is not None:
        current.status = PromptStatus.ARCHIVED
        # Retire it as a revert target: it is the version we are rolling back FROM.
        current.notes = _note_rollback(current, now)
        current.activated_at = None
    prev.status = PromptStatus.ACTIVE
    prev.activated_at = now
    await session.flush()
    _cache_on_commit(session, prev)
    return prev


def _note_rollback(pv: PromptVersion, when: datetime) -> str:
    """Append an audit line recording that ``pv`` was rolled back at ``when``."""
    line = (
        f"rolled back from active (was activated_at="
        f"{pv.activated_at.isoformat() if pv.activated_at else 'unknown'}) "
        f"at {when.isoformat()}"
    )
    return f"{pv.notes}\n{line}" if pv.notes else line
