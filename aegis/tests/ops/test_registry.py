"""Tests for the prompt-version registry + the process-wide active-prompt cache."""

from __future__ import annotations

from aegis.ops import registry
from aegis.ops.models import PromptStatus

from .conftest import DEFAULT_PERSONA_ID

PK = "ops"


async def test_create_draft_increments_version(db):
    async with db() as s:
        a = await registry.create_draft(s, prompt_key=PK, system_prompt="v1")
        b = await registry.create_draft(s, prompt_key=PK, system_prompt="v2")
        await s.commit()
        assert (a.version, b.version) == (1, 2)
        assert a.status is PromptStatus.DRAFT


async def test_promote_activates_one_and_archives_prior(db):
    async with db() as s:
        v1 = await registry.create_draft(s, prompt_key=PK, system_prompt="first")
        await registry.promote(s, v1.id)
        v2 = await registry.create_draft(s, prompt_key=PK, system_prompt="second")
        await registry.promote(s, v2.id)
        await s.commit()
        active = await registry.get_active(s, PK)
        assert active.version == 2 and active.status is PromptStatus.ACTIVE
        await s.refresh(v1)
        assert v1.status is PromptStatus.ARCHIVED  # one-active-per-key
    # cache reflects the promotion
    cached = registry.get_cached_active(PK)
    assert cached is not None and cached[0] == "second" and cached[2] == 2


async def test_rollback_reactivates_previous(db):
    async with db() as s:
        v1 = await registry.create_draft(s, prompt_key=PK, system_prompt="first")
        await registry.promote(s, v1.id)
        v2 = await registry.create_draft(s, prompt_key=PK, system_prompt="second")
        await registry.promote(s, v2.id)
        rolled = await registry.rollback(s, PK)
        await s.commit()
        assert rolled.version == 1 and rolled.status is PromptStatus.ACTIVE
    assert registry.get_cached_active(PK)[0] == "first"


async def test_refresh_cache_loads_active(db):
    async with db() as s:
        v1 = await registry.create_draft(s, prompt_key=PK, system_prompt="live")
        await registry.promote(s, v1.id)
        await s.commit()
    registry.clear_cache()
    async with db() as s:
        n = await registry.refresh_cache(s)
    assert n == 1 and registry.get_cached_active(PK)[0] == "live"


async def test_list_versions_newest_first(db):
    async with db() as s:
        await registry.create_draft(s, prompt_key=PK, system_prompt="v1")
        await registry.create_draft(s, prompt_key=PK, system_prompt="v2")
        await registry.create_draft(s, prompt_key=PK, system_prompt="v3")
        await s.commit()
        versions = await registry.list_versions(s, PK)
    assert [v.version for v in versions] == [3, 2, 1]


def test_cache_is_empty_by_default_so_host_falls_back_to_floor():
    """An empty cache returns ``None`` — the caller then uses the injected floor."""
    registry.clear_cache()
    assert registry.get_cached_active(DEFAULT_PERSONA_ID) is None


def test_cache_returns_the_active_tuple_when_populated():
    """A populated cache hands back ``(system_prompt, config, version)`` synchronously."""
    registry.clear_cache()
    registry._ACTIVE_CACHE[DEFAULT_PERSONA_ID] = ("ACTIVE PROMPT", {}, 7)
    got = registry.get_cached_active(DEFAULT_PERSONA_ID)
    assert got == ("ACTIVE PROMPT", {}, 7)
    registry.clear_cache()
