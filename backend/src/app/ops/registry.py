"""Prompt-version registry — versioned, reversible system prompts + an active cache.

This is the seam that lets the LLM-Ops loop feed an improved system prompt back into the
harness *safely*: the optimizer/human writes a ``draft`` :class:`PromptVersion`; a gated
``promote`` makes it the single ``active`` version for its ``prompt_key`` and archives the
prior one (so ``rollback`` is a one-call revert). The harness never reads the DB on the hot
path — it reads a process-wide **active cache** synchronously (:func:`get_cached_active`),
which ``promote``/``rollback`` update and startup/refresh repopulate. When no active version
exists, the adapter prompt is the floor (see ``deps._default_render_system_prompt``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import PromptStatus, PromptVersion

# prompt_key → (system_prompt, config, version). Process-wide; read synchronously by the
# harness. Empty until refreshed → callers fall back to the adapter default (the floor).
_ACTIVE_CACHE: dict[str, tuple[str, dict[str, Any], int]] = {}


def get_cached_active(prompt_key: str) -> tuple[str, dict[str, Any], int] | None:
    """Return the cached active ``(system_prompt, config, version)`` for ``prompt_key``.

    Synchronous and hot-path-safe. ``None`` when nothing is active/cached — the caller
    must then fall back to the adapter default.
    """
    return _ACTIVE_CACHE.get(prompt_key)


def clear_cache() -> None:
    """Drop the active cache (test isolation)."""
    _ACTIVE_CACHE.clear()


def _cache(pv: PromptVersion) -> None:
    _ACTIVE_CACHE[pv.prompt_key] = (pv.system_prompt, dict(pv.config or {}), pv.version)


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
    """Write a new ``draft`` version (auto-incrementing ``version`` per ``prompt_key``)."""
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
    session.add(pv)
    await session.flush()
    return pv


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

    Enforces one-active-per-key, stamps ``activated_at``, and refreshes the cache so the
    next run picks it up. The transaction is left open for the caller to commit.

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
    _cache(pv)
    return pv


async def rollback(session: AsyncSession, prompt_key: str) -> PromptVersion | None:
    """Reactivate the most-recent archived version for ``prompt_key`` (one-call revert).

    Archives the current active (if any), reactivates the highest-version archived one,
    and refreshes the cache. Returns the newly-active version, or ``None`` if there is no
    prior version to roll back to.
    """
    # Only a version that was ACTUALLY LIVE (activated_at set) is a valid revert
    # target. Rejected drafts are also ARCHIVED — without this guard rollback would
    # happily reactivate an eval-failed draft that was never live (it usually has a
    # higher version), i.e. the opposite of a safe revert. Pick the most-recently
    # activated prior version.
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
    current = await get_active(session, prompt_key)
    if current is not None:
        current.status = PromptStatus.ARCHIVED
    prev.status = PromptStatus.ACTIVE
    prev.activated_at = datetime.now(UTC)
    await session.flush()
    _cache(prev)
    return prev
