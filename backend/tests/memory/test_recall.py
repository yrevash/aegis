"""Recall READ-path tests (SQLite): ranking, valid-only, dedup, isolation, skills.

Seeds via the same ``bootstrap`` + ``async_sessionmaker`` pattern as ``test_stores.py``.
All isolation is proven with **RLS off** (SQLite) — the app-level ``WHERE subject_id``
is the sole isolator (``docs/MEMORY_SPEC.md`` BLOCKER 2).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.data.session import bootstrap, configure_engine, get_sessionmaker
from app.memory.config import MemoryConfig
from app.memory.recall import recall
from app.memory.stores import MemoryFact, MemoryMessage, MemorySession

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db(tmp_path) -> async_sessionmaker:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mem.db'}")
    configure_engine(engine)
    await bootstrap(engine)
    yield get_sessionmaker()
    await engine.dispose()


def _fact(subject_id: str, predicate: str, obj: str, emb: list[float], **kw) -> MemoryFact:
    return MemoryFact(
        subject_id=subject_id,
        fact_type="preference",
        subject="customer",
        predicate=predicate,
        object=obj,
        text=f"Customer {predicate} {obj}.",
        embedding=emb,
        **kw,
    )


def _msg(
    subject_id: str, session_id: str, turn: int, content: str, emb: list[float]
) -> MemoryMessage:
    return MemoryMessage(
        subject_id=subject_id,
        session_id=session_id,
        turn_index=turn,
        role="user",
        content=content,
        embedding=emb,
        embedding_dim=len(emb),
    )


async def test_facts_ranked_and_valid_only(db):
    cfg = MemoryConfig()
    async with db() as s:
        s.add(MemorySession(id="sess-1", subject_id="user:1"))
        s.add(_fact("user:1", "prefers_channel", "email", [1.0, 0.0, 0.0, 0.0], importance=6))
        s.add(_fact("user:1", "region", "emea", [0.0, 1.0, 0.0, 0.0], importance=6))
        # Invalidated fact that is ALSO a strong vector match — must be excluded.
        s.add(
            _fact(
                "user:1",
                "old_tier",
                "free",
                [1.0, 0.0, 0.0, 0.0],
                importance=6,
                invalid_at=datetime.now(UTC),
            )
        )
        await s.commit()

    async with db() as s:
        bundle = await recall(
            s,
            subject_id="user:1",
            session_id="sess-1",
            persona="ops",
            query="what channel do they prefer",
            query_vec=[1.0, 0.0, 0.0, 0.0],
            config=cfg,
        )
    keys = [c.key for c in bundle.facts]
    assert "customer|prefers_channel" in keys
    assert "customer|old_tier" not in keys  # valid-only excludes the invalidated match
    assert bundle.facts[0].key == "customer|prefers_channel"  # closest ranked first


async def test_episodic_dedup_vs_raw_window(db):
    cfg = MemoryConfig(raw_window_turns=2)
    async with db() as s:
        s.add(MemorySession(id="sess-1", subject_id="user:1"))
        # turn 0 is OLD but a strong vector match → should surface via episodic recall.
        s.add(_msg("user:1", "sess-1", 0, "old but relevant refund note", [1.0, 0.0, 0.0, 0.0]))
        s.add(_msg("user:1", "sess-1", 1, "chit chat one", [0.0, 1.0, 0.0, 0.0]))
        s.add(_msg("user:1", "sess-1", 2, "chit chat two", [0.0, 1.0, 0.0, 0.0]))
        # turn 3 is recent (in the 2-turn raw window) AND a vector match → must be deduped.
        s.add(_msg("user:1", "sess-1", 3, "recent relevant refund note", [1.0, 0.0, 0.0, 0.0]))
        await s.commit()
        ids = {
            m.turn_index: m.id
            for m in (await s.execute(select(MemoryMessage))).scalars().all()
        }

    async with db() as s:
        bundle = await recall(
            s,
            subject_id="user:1",
            session_id="sess-1",
            persona="ops",
            query="refund",
            query_vec=[1.0, 0.0, 0.0, 0.0],
            config=cfg,
        )
    epi_ids = {c.payload.id for c in bundle.episodic}
    assert ids[0] in epi_ids  # older relevant turn recalled beyond the window
    assert ids[3] not in epi_ids  # already in the raw window → not double-injected
    assert ids[2] not in epi_ids  # raw-window turn is never episodic


async def test_subject_isolation_rls_off(db):
    cfg = MemoryConfig()
    async with db() as s:
        s.add(MemorySession(id="sess-a", subject_id="user:A"))
        s.add(MemorySession(id="sess-b", subject_id="user:B"))
        s.add(_fact("user:A", "secret", "alpha", [1.0, 0.0, 0.0, 0.0], importance=9))
        s.add(_fact("user:B", "topic", "beta", [0.0, 1.0, 0.0, 0.0], importance=5))
        await s.commit()

    async with db() as s:
        bundle = await recall(
            s,
            subject_id="user:B",
            session_id="sess-b",
            persona="ops",
            query="anything",
            query_vec=[1.0, 0.0, 0.0, 0.0],  # matches A's secret exactly
            config=cfg,
        )
    keys = [c.key for c in bundle.facts]
    assert "customer|secret" not in keys  # subject-A fact never leaks to subject-B
    assert all(c.payload.subject_id == "user:B" for c in bundle.facts)


async def test_skills_selected_for_refund_query(db):
    cfg = MemoryConfig()
    async with db() as s:
        s.add(MemorySession(id="sess-1", subject_id="user:1"))
        await s.commit()

    async with db() as s:
        bundle = await recall(
            s,
            subject_id="user:1",
            session_id="sess-1",
            persona="ops",
            query="I want a refund for a duplicate charge",
            query_vec=None,
            config=cfg,
        )
    names = [name for name, _ in bundle.skills]
    assert "handling_refunds" in names
    assert all(text.strip() for _, text in bundle.skills)  # bodies actually read


async def test_recall_bumps_access_count_durably(db):
    """The recall READ path bumps + commits access_count for what it recalled this turn.

    Proves the frequency signal is real end to end: the increment survives into a fresh
    session (i.e. recall committed it), so later turns' composite can weigh it.
    """
    cfg = MemoryConfig(raw_window_turns=1)
    async with db() as s:
        s.add(MemorySession(id="sess-1", subject_id="user:1"))
        s.add(_fact("user:1", "prefers_channel", "email", [1.0, 0.0, 0.0, 0.0]))
        # turn 0 is OLD (outside the 1-turn window) yet a strong vector match → recalled.
        s.add(_msg("user:1", "sess-1", 0, "old relevant refund note", [1.0, 0.0, 0.0, 0.0]))
        s.add(_msg("user:1", "sess-1", 1, "recent chit chat", [0.0, 1.0, 0.0, 0.0]))
        await s.commit()

    async with db() as s:
        bundle = await recall(
            s,
            subject_id="user:1",
            session_id="sess-1",
            persona="ops",
            query="refund by email",
            query_vec=[1.0, 0.0, 0.0, 0.0],
            config=cfg,
        )
        recalled_fact_id = bundle.facts[0].payload.id
        recalled_msg_ids = {c.payload.id for c in bundle.episodic}
    assert recalled_msg_ids  # the old relevant turn was recalled episodically

    # Re-open a clean session: the bump must have been committed, not rolled back.
    async with db() as s:
        fact = (
            await s.execute(select(MemoryFact).where(MemoryFact.id == recalled_fact_id))
        ).scalar_one()
        assert fact.access_count == 1
        assert fact.last_access_at is not None

        for mid in recalled_msg_ids:
            msg = (
                await s.execute(select(MemoryMessage).where(MemoryMessage.id == mid))
            ).scalar_one()
            assert msg.access_count == 1
            assert msg.last_access_at is not None


async def test_facts_recency_only_when_no_query_vec(db):
    cfg = MemoryConfig()
    async with db() as s:
        s.add(MemorySession(id="sess-1", subject_id="user:1"))
        s.add(_fact("user:1", "prefers_channel", "email", [1.0, 0.0, 0.0, 0.0]))
        await s.commit()

    async with db() as s:
        bundle = await recall(
            s,
            subject_id="user:1",
            session_id="sess-1",
            persona="ops",
            query="channel?",
            query_vec=None,  # degradation ladder: recency-only facts still served
            config=cfg,
        )
    assert [c.key for c in bundle.facts] == ["customer|prefers_channel"]
