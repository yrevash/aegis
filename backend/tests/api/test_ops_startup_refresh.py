"""Startup cache refresh — the lifespan warms the registry's active-prompt cache.

Exercises the real :func:`app.main.lifespan` startup: with an ACTIVE prompt version in the
DB and the stores enabled, entering the lifespan must load that version into the process-
wide active cache (:func:`app.ops.registry.get_cached_active`) — the synchronous seam the
harness reads on the hot path. The ML warm-up is stubbed so the test stays fast/offline.
"""

from __future__ import annotations

import pytest

from app.adapter import DEFAULT_PERSONA_ID
from app.main import app, lifespan
from app.ops import registry

pytestmark = pytest.mark.asyncio

PK = DEFAULT_PERSONA_ID
ACTIVE_PROMPT = "You are the promoted, live system prompt."

# ``db`` is the shared scratch-PostgreSQL fixture from ``tests/conftest.py``; it already
# binds the engines and clears the active-prompt cache around each test.


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
