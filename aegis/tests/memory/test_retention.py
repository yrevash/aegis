"""Retention: what the horizon removes, what it refuses to remove, and what it needs told.

Three claims, and each one is load-bearing in a different direction:

1. **A valid fact is never deleted by age.** Retention is the only unconditional hard
   delete in the memory subsystem, and the one way to get it catastrophically wrong is to
   sweep the distilled knowledge along with the raw turns — the agent would quietly
   forget everything it had learned while every screen still read healthy.
2. **A scope must be stated.** ``tenant_id=None`` means two opposite things elsewhere in
   this codebase ("the null-tenant scope" and "unrestricted"), and this module deletes
   rows, so an unstated scope raises instead of picking the destructive reading.
3. **The preview and the sweep agree**, because a confirmation dialogue that promises a
   different number from the receipt is worse than no number at all.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from aegis.memory.retention import (
    RetentionPolicy,
    apply_retention,
    retention_preview,
)
from aegis.memory.stores import MemoryFact, MemoryMessage, MemorySession

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


def _fact(subject_id: str, text: str, *, tenant_id: int, **kw) -> MemoryFact:
    return MemoryFact(
        subject_id=subject_id,
        tenant_id=tenant_id,
        fact_type="preference",
        subject="customer",
        predicate="prefers_channel",
        object="email",
        text=text,
        **kw,
    )


async def _seed(db) -> None:  # noqa: ANN001 - the sessionmaker fixture
    """One tenant with an ancient turn, a fresh turn, and two facts of different ages."""
    async with db() as s:
        s.add(
            MemorySession(
                id="sess-old",
                tenant_id=1,
                subject_id="user:1",
                last_active_at=NOW - timedelta(days=400),
            )
        )
        s.add(
            MemorySession(
                id="sess-new",
                tenant_id=1,
                subject_id="user:1",
                last_active_at=NOW - timedelta(days=1),
            )
        )
        await s.flush()  # the sessions must exist before their turns reference them
        s.add(
            MemoryMessage(
                tenant_id=1,
                subject_id="user:1",
                session_id="sess-old",
                role="user",
                content="said long ago",
                created_at=NOW - timedelta(days=400),
            )
        )
        s.add(
            MemoryMessage(
                tenant_id=1,
                subject_id="user:1",
                session_id="sess-new",
                role="user",
                content="said yesterday",
                created_at=NOW - timedelta(days=1),
            )
        )
        # A fact the agent still believes, older than every horizon in the test.
        s.add(_fact("user:1", "still true", tenant_id=1, valid_at=NOW - timedelta(days=500)))
        # A fact superseded a year ago — the only kind retention may remove.
        s.add(
            _fact(
                "user:1",
                "was true once",
                tenant_id=1,
                valid_at=NOW - timedelta(days=500),
                expired_at=NOW - timedelta(days=365),
            )
        )
        await s.commit()


async def test_a_valid_fact_survives_any_horizon_while_a_closed_one_goes(db):
    await _seed(db)
    policy = RetentionPolicy(episodic_days=90, closed_fact_days=30)

    async with db() as s:
        removed = await apply_retention(s, policy=policy, tenant_id=1, now=NOW)
        await s.commit()

    assert removed.facts == 1
    async with db() as s:
        surviving = [f.text for f in (await s.execute(select(MemoryFact))).scalars()]
    # The distilled knowledge is what the subsystem exists to hold. Age alone never
    # removes it — only a supersession, an explicit forget, or an erasure request does.
    assert surviving == ["still true"]


async def test_the_horizon_removes_old_turns_and_the_sessions_they_emptied(db):
    await _seed(db)

    async with db() as s:
        removed = await apply_retention(
            s, policy=RetentionPolicy(episodic_days=90, closed_fact_days=0), tenant_id=1, now=NOW
        )
        await s.commit()

    assert removed.messages == 1
    # The empty-session sweep is evaluated AFTER the turns are gone, so a session whose
    # whole transcript just aged out goes with it — and one still holding an in-horizon
    # turn keeps its summary.
    assert removed.sessions == 1
    async with db() as s:
        ids = sorted(s_.id for s_ in (await s.execute(select(MemorySession))).scalars())
    assert ids == ["sess-new"]


async def test_the_preview_promises_what_the_sweep_delivers(db):
    await _seed(db)
    policy = RetentionPolicy(episodic_days=90, closed_fact_days=30)

    async with db() as s:
        promised = await retention_preview(s, policy=policy, tenant_id=1, now=NOW)

    async with db() as s:
        delivered = await apply_retention(s, policy=policy, tenant_id=1, now=NOW)
        await s.commit()

    assert promised.messages == delivered.messages
    assert promised.facts == delivered.facts
    assert promised.jobs == delivered.jobs


async def test_a_sweep_with_no_stated_scope_refuses_rather_than_guessing(db):
    await _seed(db)
    async with db() as s:
        with pytest.raises(ValueError, match="retention needs a scope"):
            await apply_retention(s, policy=RetentionPolicy(), now=NOW)

    # Nothing was deleted on the way to the refusal.
    async with db() as s:
        assert len((await s.execute(select(MemoryMessage))).scalars().all()) == 2


async def test_a_disabled_horizon_deletes_nothing(db):
    await _seed(db)
    async with db() as s:
        removed = await apply_retention(
            s,
            policy=RetentionPolicy(episodic_days=0, closed_fact_days=0),
            tenant_id=1,
            now=NOW,
        )
        await s.commit()
    assert removed.total == 0
    async with db() as s:
        assert len((await s.execute(select(MemoryMessage))).scalars().all()) == 2
