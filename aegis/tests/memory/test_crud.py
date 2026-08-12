"""Explicit memory CRUD tests: list, get, and soft/hard forget with audit + isolation."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from aegis.memory.crud import forget_fact, get_fact, list_facts
from aegis.memory.stores import MemoryFact, MemoryWriteLog, WriteOp

pytestmark = pytest.mark.asyncio


def _fact(subject_id: str, predicate: str, obj: str, **kw) -> MemoryFact:
    return MemoryFact(
        subject_id=subject_id,
        fact_type="preference",
        subject="customer",
        predicate=predicate,
        object=obj,
        text=f"Customer {predicate} {obj}.",
        embedding=[1.0, 0.0, 0.0, 0.0],
        **kw,
    )


async def test_list_facts_valid_only_and_scoped(db):
    async with db() as s:
        s.add(_fact("user:1", "prefers_channel", "email"))
        s.add(_fact("user:1", "region", "emea"))
        s.add(_fact("user:2", "prefers_channel", "sms"))  # other subject
        await s.commit()

    async with db() as s:
        facts = await list_facts(s, subject_id="user:1")
        preds = {f.predicate for f in facts}
        assert preds == {"prefers_channel", "region"}  # user:2 never leaks in


async def test_list_facts_excludes_forgotten_but_history_keeps_them(db):
    async with db() as s:
        s.add(_fact("user:1", "prefers_channel", "email"))
        await s.commit()
        fact_id = (await s.execute(select(MemoryFact.id))).scalar_one()

    async with db() as s:
        await forget_fact(s, fact_id=fact_id, subject_id="user:1")
        await s.commit()

    async with db() as s:
        assert await list_facts(s, subject_id="user:1") == []  # gone from hot recall
        history = await list_facts(s, subject_id="user:1", valid_only=False)
        assert len(history) == 1  # still auditable


async def test_forget_fact_soft_sets_both_time_axes_and_logs_delete(db):
    async with db() as s:
        s.add(_fact("user:1", "prefers_channel", "email"))
        await s.commit()
        fact_id = (await s.execute(select(MemoryFact.id))).scalar_one()

    async with db() as s:
        result = await forget_fact(
            s, fact_id=fact_id, subject_id="user:1", reason="user asked"
        )
        assert result is not None
        await s.commit()

    async with db() as s:
        fact = (await s.execute(select(MemoryFact))).scalar_one()
        assert fact.invalid_at is not None
        assert fact.expired_at is not None  # dropped from hot recall
        log = (await s.execute(select(MemoryWriteLog))).scalar_one()
        assert log.op is WriteOp.DELETE
        assert log.fact_id == fact_id
        assert log.reason == "user asked"


async def test_forget_fact_hard_removes_row_but_logs(db):
    async with db() as s:
        s.add(_fact("user:1", "prefers_channel", "email"))
        await s.commit()
        fact_id = (await s.execute(select(MemoryFact.id))).scalar_one()

    async with db() as s:
        await forget_fact(s, fact_id=fact_id, subject_id="user:1", hard=True)
        await s.commit()

    async with db() as s:
        assert (await s.execute(select(MemoryFact))).first() is None  # erased
        log = (await s.execute(select(MemoryWriteLog))).scalar_one()
        assert log.op is WriteOp.DELETE
        assert log.after == {}  # nothing left after a hard delete


async def test_forget_fact_wrong_subject_is_noop(db):
    async with db() as s:
        s.add(_fact("user:1", "prefers_channel", "email"))
        await s.commit()
        fact_id = (await s.execute(select(MemoryFact.id))).scalar_one()

    async with db() as s:
        # A different subject must never be able to forget user:1's fact.
        assert await forget_fact(s, fact_id=fact_id, subject_id="user:2") is None
        await s.commit()

    async with db() as s:
        fact = (await s.execute(select(MemoryFact))).scalar_one()
        assert fact.expired_at is None  # untouched


async def test_get_fact_isolation(db):
    async with db() as s:
        s.add(_fact("user:1", "prefers_channel", "email"))
        await s.commit()
        fact_id = (await s.execute(select(MemoryFact.id))).scalar_one()

    async with db() as s:
        assert await get_fact(s, fact_id=fact_id, subject_id="user:1") is not None
        assert await get_fact(s, fact_id=fact_id, subject_id="user:2") is None
