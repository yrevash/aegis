"""Schema tests: the memory tables materialise + round-trip on SQLite (aegis.data base)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from aegis.memory.stores import (
    ConsolidationStatus,
    MemoryConsolidationJob,
    MemoryFact,
    MemoryMessage,
    MemoryOrigin,
    MemorySession,
    MemoryWriteLog,
    WriteOp,
)

pytestmark = pytest.mark.asyncio


async def test_session_message_vector_roundtrip(db):
    async with db() as s:
        s.add(MemorySession(id="sess-1", subject_id="user:1", persona="ops"))
        s.add(
            MemoryMessage(
                subject_id="user:1",
                session_id="sess-1",
                turn_index=0,
                role="user",
                origin=MemoryOrigin.USER,
                content="hello",
                embedding=[0.1, 0.2, 0.3],  # VectorType → JSON on SQLite
                embedding_dim=3,
            )
        )
        await s.commit()
    async with db() as s:
        msg = (await s.execute(select(MemoryMessage))).scalar_one()
        assert msg.content == "hello"
        assert msg.embedding == [0.1, 0.2, 0.3]  # vector survives the JSON round-trip
        assert msg.origin is MemoryOrigin.USER


async def test_bitemporal_fact_and_writelog(db):
    async with db() as s:
        fact = MemoryFact(
            subject_id="user:1",
            fact_type="preference",
            subject="user",
            predicate="prefers_channel",
            object="email",
            text="User prefers email.",
            embedding=[0.0] * 4,
            confidence=0.9,
            importance=7,
            source_turn_ids=[1, 2],
        )
        s.add(fact)
        await s.flush()
        assert fact.invalid_at is None and fact.expired_at is None  # currently valid
        s.add(
            MemoryWriteLog(
                subject_id="user:1", op=WriteOp.ADD, fact_id=fact.id,
                after={"predicate": "prefers_channel", "object": "email"},
                reason="new fact", model="cheap",
            )
        )
        await s.commit()
    async with db() as s:
        wl = (await s.execute(select(MemoryWriteLog))).scalar_one()
        assert wl.op is WriteOp.ADD and wl.after["object"] == "email"


async def test_consolidation_job_enqueue(db):
    async with db() as s:
        s.add(MemorySession(id="sess-2", subject_id="user:2"))
        s.add(MemoryConsolidationJob(subject_id="user:2", session_id="sess-2"))
        await s.commit()
    async with db() as s:
        job = (await s.execute(select(MemoryConsolidationJob))).scalar_one()
        assert job.status is ConsolidationStatus.PENDING and job.attempts == 0
