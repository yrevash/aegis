"""Startup cache refresh — the lifespan warms the registry's active-prompt cache.

Exercises the real :func:`app.main.lifespan` startup: with an ACTIVE prompt version in the
DB and the stores enabled, entering the lifespan must load that version into the process-
wide active cache (:func:`app.ops.registry.get_cached_active`) — the synchronous seam the
harness reads on the hot path. The ML warm-up is stubbed so the test stays fast/offline.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from app.adapter import DEFAULT_PERSONA_ID
from app.data.session import bootstrap, configure_engine, get_sessionmaker
from app.main import app, lifespan
from app.ops import registry

pytestmark = pytest.mark.asyncio

PK = DEFAULT_PERSONA_ID
ACTIVE_PROMPT = "You are the promoted, live system prompt."


@pytest_asyncio.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'startup.db'}")
    configure_engine(engine)
    await bootstrap(engine)
    registry.clear_cache()
    yield get_sessionmaker()
    registry.clear_cache()
    await engine.dispose()


async def test_lifespan_warms_active_prompt_cache(db, monkeypatch):
    # Promote a live version straight into the DB, then drop the in-process cache so the
    # assertion proves the *startup refresh* (not the promote) repopulates it.
    async with db() as s:
        pv = await registry.create_draft(s, prompt_key=PK, system_prompt=ACTIVE_PROMPT)
        await registry.promote(s, pv.id)
        await s.commit()
        version = pv.version
    registry.clear_cache()
    assert registry.get_cached_active(PK) is None

    # Keep the ML warm-up a no-op so the lifespan is fast and offline (best-effort anyway).
    monkeypatch.setattr("app.ml.get_model", lambda: None, raising=False)

    async with lifespan(app):
        cached = registry.get_cached_active(PK)
        assert cached is not None
        system_prompt, _config, cached_version = cached
        assert system_prompt == ACTIVE_PROMPT and cached_version == version
