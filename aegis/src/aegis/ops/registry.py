"""Prompt-version registry — versioned, reversible system prompts + an active cache.

This is the seam that lets the LLM-Ops loop feed an improved system prompt back into the
harness *safely*: the optimizer/human writes a ``draft`` :class:`PromptVersion`; a gated
``promote`` makes it the single ``active`` version for its ``prompt_key`` and archives the
prior one (so ``rollback`` is a one-call revert). The harness never reads the DB on the hot
path — it reads a process-wide **active cache** synchronously (:func:`get_cached_active`),
which ``promote``/``rollback`` update and startup/refresh repopulate. When no active version
exists, the injected floor prompt is the baseline (see :mod:`aegis.ops.config`).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import event, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aegis.ops.models import PromptStatus, PromptVersion

logger = logging.getLogger(__name__)

# prompt_key → (system_prompt, config, version). Process-wide; read synchronously by the
# harness. Empty until refreshed → callers fall back to the injected floor.
_ACTIVE_CACHE: dict[str, tuple[str, dict[str, Any], int]] = {}

#: How many times :func:`create_draft` re-reads ``max(version)`` and retries after a
#: unique-index collision with a concurrent writer.
_VERSION_COLLISION_RETRIES = 5


def get_cached_active(prompt_key: str) -> tuple[str, dict[str, Any], int] | None:
    """Return the cached active ``(system_prompt, config, version)`` for ``prompt_key``.

    Synchronous and hot-path-safe. ``None`` when nothing is active/cached — the caller
    must then fall back to the injected floor.
    """
    return _ACTIVE_CACHE.get(prompt_key)


def clear_cache() -> None:
    """Drop the active cache (test isolation)."""
    _ACTIVE_CACHE.clear()


def _cache(pv: PromptVersion) -> None:
    _ACTIVE_CACHE[pv.prompt_key] = (pv.system_prompt, dict(pv.config or {}), pv.version)


def _cache_on_commit(session: AsyncSession, pv: PromptVersion) -> None:
    """Publish ``pv`` to the active cache **only if the caller's transaction commits**.

    ``promote``/``rollback`` deliberately leave the transaction open for the caller, so
    caching at flush time publishes a prompt that may never be committed: a caller
    rollback (or a crash) would leave ``_ACTIVE_CACHE`` serving a phantom system prompt
    to every run through the synchronous hot path :func:`get_cached_active`, and nothing
    would correct it until the next :func:`refresh_cache` — which only runs at startup.

    Binding to the session's ``after_commit`` makes the cache exactly as durable as the
    row it mirrors. The listener is one-shot, and the payload is snapshotted now (the
    ORM object is expired by the commit).
    """
    key = pv.prompt_key
    payload = (pv.system_prompt, dict(pv.config or {}), pv.version)
    # AsyncSession proxies a sync Session; events are registered on the sync one.
    target = getattr(session, "sync_session", session)

    def _publish(_session: Any) -> None:  # noqa: ANN401 - SQLAlchemy Session
        _ACTIVE_CACHE[key] = payload

    event.listen(target, "after_commit", _publish, once=True)


async def refresh_cache(session: AsyncSession) -> int:
    """Reload every ``active`` version into the cache (called at startup / after release).

    Returns:
        The number of active versions loaded.
    """
    rows = (
        await session.execute(
            select(PromptVersion).where(PromptVersion.status == PromptStatus.ACTIVE)
        )
    ).scalars().all()
    _ACTIVE_CACHE.clear()
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
) -> PromptVersion:
    """Write a new ``draft`` version (auto-incrementing ``version`` per ``prompt_key``).

    ``max(version) + 1`` is check-then-act against the ``(prompt_key, version)`` unique
    index, so two concurrent diagnose passes can pick the same number. The loser's
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
                    PromptVersion.prompt_key == prompt_key
                )
            )
        ).scalar() or 0
        pv = PromptVersion(
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
    """Return the single ``active`` version for ``prompt_key`` (DB read), or ``None``."""
    stmt = select(PromptVersion).where(
        PromptVersion.prompt_key == prompt_key,
        PromptVersion.status == PromptStatus.ACTIVE,
    )
    if tenant_id is not None:
        stmt = stmt.where(PromptVersion.tenant_id == tenant_id)
    return (await session.execute(stmt)).scalars().first()


async def list_versions(session: AsyncSession, prompt_key: str) -> list[PromptVersion]:
    """Return all versions for ``prompt_key``, newest version first."""
    return list(
        (
            await session.execute(
                select(PromptVersion)
                .where(PromptVersion.prompt_key == prompt_key)
                .order_by(PromptVersion.version.desc())
            )
        ).scalars().all()
    )


async def promote(session: AsyncSession, version_id: int) -> PromptVersion:
    """Make ``version_id`` the active version for its key; archive the prior active.

    Enforces one-active-per-key and stamps ``activated_at``. The transaction is left
    open for the caller to commit; the active cache is updated **on that commit** (see
    :func:`_cache_on_commit`), never before, so a caller rollback can never leave a
    never-committed prompt serving live traffic.

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


async def rollback(session: AsyncSession, prompt_key: str) -> PromptVersion | None:
    """Reactivate the most-recently-active prior version for ``prompt_key`` (one-call revert).

    Archives the current active (if any) and reactivates the version that was live
    before it. Returns the newly-active version, or ``None`` if there is no prior
    version to roll back to.

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
                PromptVersion.status == PromptStatus.ARCHIVED,
                PromptVersion.activated_at.is_not(None),
            )
            .order_by(PromptVersion.activated_at.desc(), PromptVersion.version.desc())
        )
    ).scalars().first()
    if prev is None:
        return None
    now = datetime.now(UTC)
    current = await get_active(session, prompt_key)
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
