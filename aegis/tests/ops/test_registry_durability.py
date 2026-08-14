"""Registry durability: the cache mirrors committed rows, and rollback walks backwards.

Three separate defects:

1. ``promote``/``rollback`` cached the new active *after ``flush``* while the docstring
   promises the transaction is left open for the caller. A caller rollback therefore
   left ``_ACTIVE_CACHE`` serving a never-committed prompt to every run through the
   synchronous hot path ``get_cached_active`` — uncorrected until the next
   ``refresh_cache``, which only runs at startup.
2. ``rollback`` stamped the revert target with ``now()`` and archived the current active
   keeping its *older* ``activated_at``. Since the selection query orders archived rows
   by ``activated_at DESC``, a second rollback picked the version just rolled back FROM
   and put the broken prompt straight back into production.
3. ``create_draft`` is check-then-act on ``max(version) + 1`` against a unique index, so
   a concurrent writer's ``IntegrityError`` threw away a draft whose optimizer LLM call
   had already been paid for.
"""

from __future__ import annotations

import asyncio

from aegis.ops import registry
from aegis.ops.models import PromptStatus, PromptVersion

from .conftest import DEFAULT_PERSONA_ID

PK = DEFAULT_PERSONA_ID


# ── 1. The cache must not outrun the transaction ──────────────────────────────


async def test_promote_does_not_cache_before_the_caller_commits(db):
    """REGRESSION: an uncommitted promote must never reach the hot-path cache."""
    registry.clear_cache()
    async with db() as s:
        pv = await registry.create_draft(s, prompt_key=PK, system_prompt="phantom")
        await registry.promote(s, pv.id)
        # Flushed, promoted — but NOT committed. Nothing may be serving it yet.
        assert registry.get_cached_active(PK) is None
        await s.rollback()

    assert registry.get_cached_active(PK) is None
    async with db() as s:
        assert await registry.get_active(s, PK) is None


async def test_promote_caches_once_the_caller_commits(db):
    registry.clear_cache()
    async with db() as s:
        pv = await registry.create_draft(s, prompt_key=PK, system_prompt="real")
        await registry.promote(s, pv.id)
        await s.commit()
    cached = registry.get_cached_active(PK)
    assert cached is not None and cached[0] == "real"


async def test_rollback_does_not_cache_before_the_caller_commits(db):
    registry.clear_cache()
    async with db() as s:
        v1 = await registry.create_draft(s, prompt_key=PK, system_prompt="first")
        await registry.promote(s, v1.id)
        v2 = await registry.create_draft(s, prompt_key=PK, system_prompt="second")
        await registry.promote(s, v2.id)
        await s.commit()
    assert registry.get_cached_active(PK)[0] == "second"

    async with db() as s:
        await registry.rollback(s, PK)
        assert registry.get_cached_active(PK)[0] == "second"  # not yet committed
        await s.rollback()
    assert registry.get_cached_active(PK)[0] == "second"


# ── 2. Repeated rollbacks walk the history backwards ──────────────────────────


async def test_second_rollback_does_not_repromote_the_version_just_rolled_back(db):
    """REGRESSION: rolling back twice must not restore the prompt you rolled back FROM."""
    async with db() as s:
        v1 = await registry.create_draft(s, prompt_key=PK, system_prompt="first")
        await registry.promote(s, v1.id)
        v2 = await registry.create_draft(s, prompt_key=PK, system_prompt="broken")
        await registry.promote(s, v2.id)
        await s.commit()
        broken_id = v2.id

    async with db() as s:
        rolled = await registry.rollback(s, PK)
        await s.commit()
        assert rolled.system_prompt == "first"

    async with db() as s:
        again = await registry.rollback(s, PK)
        await s.commit()
        # There is nothing older than v1 to revert to — and certainly not the broken one.
        assert again is None or again.id != broken_id
        active = await registry.get_active(s, PK)
        assert active.system_prompt == "first"


async def test_rollback_walks_a_three_version_history_backwards(db):
    async with db() as s:
        for text in ("v1", "v2", "v3"):
            pv = await registry.create_draft(s, prompt_key=PK, system_prompt=text)
            await registry.promote(s, pv.id)
        await s.commit()

    seen = []
    for _ in range(3):
        async with db() as s:
            rolled = await registry.rollback(s, PK)
            await s.commit()
            seen.append(None if rolled is None else rolled.system_prompt)
    assert seen == ["v2", "v1", None]


async def test_rollback_records_the_deactivation_in_notes(db):
    """The audit trail survives even though the rollback marker is cleared."""
    async with db() as s:
        v1 = await registry.create_draft(s, prompt_key=PK, system_prompt="first")
        await registry.promote(s, v1.id)
        v2 = await registry.create_draft(s, prompt_key=PK, system_prompt="broken")
        await registry.promote(s, v2.id)
        await s.commit()
        broken_id = v2.id

    async with db() as s:
        await registry.rollback(s, PK)
        await s.commit()

    async with db() as s:
        broken = await s.get(PromptVersion, broken_id)
        assert broken.status is PromptStatus.ARCHIVED
        assert broken.activated_at is None  # no longer a revert target
        assert "rolled back from active" in (broken.notes or "")


async def test_rollback_still_ignores_a_rejected_draft(db):
    """A never-live ARCHIVED draft is not a revert target (pre-existing guard, kept)."""
    async with db() as s:
        v1 = await registry.create_draft(s, prompt_key=PK, system_prompt="live")
        await registry.promote(s, v1.id)
        v2 = await registry.create_draft(s, prompt_key=PK, system_prompt="live-2")
        await registry.promote(s, v2.id)
        rejected = await registry.create_draft(s, prompt_key=PK, system_prompt="rejected")
        rejected.status = PromptStatus.ARCHIVED  # eval-failed, never activated
        await s.commit()

    async with db() as s:
        rolled = await registry.rollback(s, PK)
        await s.commit()
        assert rolled.system_prompt == "live"


# ── 3. Concurrent create_draft does not lose a paid-for prompt ────────────────


async def test_create_draft_retries_a_version_collision(db):
    """REGRESSION: a concurrent writer must not destroy the loser's draft."""

    async def make(text: str) -> int:
        async with db() as s:
            pv = await registry.create_draft(s, prompt_key=PK, system_prompt=text)
            await s.commit()
            return pv.version

    # Force the collision deterministically: seed version 1 out from under a writer
    # that has already read max(version) == 0.
    async with db() as s:
        s.add(
            PromptVersion(
                prompt_key=PK, version=1, system_prompt="squatter", config={},
                status=PromptStatus.DRAFT,
            )
        )
        await s.commit()

    version = await make("optimizer output worth an LLM call")
    assert version == 2

    versions = await asyncio.gather(*(make(f"draft-{i}") for i in range(4)))
    assert sorted(versions) == [3, 4, 5, 6]


async def test_create_draft_still_increments_normally(db):
    async with db() as s:
        a = await registry.create_draft(s, prompt_key=PK, system_prompt="v1")
        b = await registry.create_draft(s, prompt_key=PK, system_prompt="v2")
        await s.commit()
    assert (a.version, b.version) == (1, 2)
    assert a.status is PromptStatus.DRAFT
