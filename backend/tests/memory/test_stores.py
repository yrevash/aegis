"""Slice-2 schema tests: the memory tables materialise + round-trip on PostgreSQL.

The schema is the one ``app.data.session.bootstrap`` builds on the scratch database
from the shared ``db`` fixture, so what is asserted here is the shape a deployment
actually gets — including the ``jsonb`` embedding columns and the enum types SQLite
used to flatten to plain text.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.memory.stores import (
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
        # Flush the parent before its children: ``memory_message`` and
        # ``memory_consolidation_job`` carry a real FK to ``memory_session``, and
        # nothing declares an ORM relationship to order the INSERTs — so the write
        # path flushes the session first (see ``app.agent.deps``). SQLite never
        # enforced the constraint, so this ordering used not to matter here.
        await s.flush()
        s.add(
            MemoryMessage(
                subject_id="user:1",
                session_id="sess-1",
                turn_index=0,
                role="user",
                origin=MemoryOrigin.USER,
                content="hello",
                embedding=[0.1, 0.2, 0.3],  # VectorColumn → jsonb on PostgreSQL
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
        await s.flush()
        s.add(MemoryConsolidationJob(subject_id="user:2", session_id="sess-2"))
        await s.commit()
    async with db() as s:
        job = (await s.execute(select(MemoryConsolidationJob))).scalar_one()
        assert job.status is ConsolidationStatus.PENDING and job.attempts == 0
