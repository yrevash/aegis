"""Tests for the prompt-version registry + the harness active-prompt cache."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.adapter import DEFAULT_PERSONA_ID, get_persona, render_system_prompt
from app.agent import deps as agent_deps
from app.data.models import PromptStatus
from app.data.session import bootstrap, configure_engine, get_sessionmaker
from app.ops import registry

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db(tmp_path) -> async_sessionmaker:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ops.db'}")
    configure_engine(engine)
    await bootstrap(engine)
    registry.clear_cache()
    yield get_sessionmaker()
    registry.clear_cache()
    await engine.dispose()


async def test_create_draft_increments_version(db):
    async with db() as s:
        a = await registry.create_draft(s, prompt_key="ops", system_prompt="v1")
        b = await registry.create_draft(s, prompt_key="ops", system_prompt="v2")
        await s.commit()
        assert (a.version, b.version) == (1, 2)
        assert a.status is PromptStatus.DRAFT


async def test_promote_activates_one_and_archives_prior(db):
    async with db() as s:
        v1 = await registry.create_draft(s, prompt_key="ops", system_prompt="first")
        await registry.promote(s, v1.id)
        v2 = await registry.create_draft(s, prompt_key="ops", system_prompt="second")
        await registry.promote(s, v2.id)
        await s.commit()
        active = await registry.get_active(s, "ops")
        assert active.version == 2 and active.status is PromptStatus.ACTIVE
        await s.refresh(v1)
        assert v1.status is PromptStatus.ARCHIVED  # one-active-per-key
    # cache reflects the promotion
    cached = registry.get_cached_active("ops")
    assert cached is not None and cached[0] == "second" and cached[2] == 2


async def test_rollback_reactivates_previous(db):
    async with db() as s:
        v1 = await registry.create_draft(s, prompt_key="ops", system_prompt="first")
        await registry.promote(s, v1.id)
        v2 = await registry.create_draft(s, prompt_key="ops", system_prompt="second")
        await registry.promote(s, v2.id)
        rolled = await registry.rollback(s, "ops")
        await s.commit()
        assert rolled.version == 1 and rolled.status is PromptStatus.ACTIVE
    assert registry.get_cached_active("ops")[0] == "first"


async def test_refresh_cache_loads_active(db):
    async with db() as s:
        v1 = await registry.create_draft(s, prompt_key="ops", system_prompt="live")
        await registry.promote(s, v1.id)
        await s.commit()
    registry.clear_cache()
    async with db() as s:
        n = await registry.refresh_cache(s)
    assert n == 1 and registry.get_cached_active("ops")[0] == "live"


def test_render_falls_back_to_adapter_floor_when_cache_empty():
    # Empty cache → byte-identical to today's adapter render (the floor).
    registry.clear_cache()
    got = agent_deps._default_render_system_prompt(DEFAULT_PERSONA_ID)
    expected = render_system_prompt(get_persona(DEFAULT_PERSONA_ID))
    assert got == expected


def test_render_uses_active_version_when_cached():
    registry.clear_cache()
    registry._ACTIVE_CACHE[DEFAULT_PERSONA_ID] = ("ACTIVE PROMPT", {}, 7)
    got = agent_deps._default_render_system_prompt(DEFAULT_PERSONA_ID, extra_context="ctx")
    assert got == "ACTIVE PROMPT\n\nctx"
    registry.clear_cache()
