"""Consolidation (WRITE-path) tests — mem0 two-phase + Zep bitemporal, offline.

Everything is dependency-injected: a scripted fake ``complete`` (routes responses by
phase — extract / decide-op / summary) and a deterministic fake ``embed`` (keyword →
fixed vector, so cosine neighbourhoods are exactly controllable). No network, no LLM.
The domain seam is the default fake MemorySpec configured in ``conftest``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from aegis.memory.config import MemoryConfig
from aegis.memory.consolidate import (
    ConsolidationResult,
    consolidate,
    enqueue_consolidation,
    sweep_pending,
)
from aegis.memory.stores import (
    ConsolidationStatus,
    MemoryConsolidationJob,
    MemoryFact,
    MemoryMessage,
    MemoryOrigin,
    MemoryProfile,
    MemorySession,
    MemoryWriteLog,
    WriteOp,
)

from ._spec import FACT_EXTRACTION_PROMPT

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- fakes


def _vec(text: str) -> list[float]:
    """Deterministic 4-dim embedding by keyword — orthogonal per concept."""
    t = text.lower()
    if "email" in t:
        return [1.0, 0.0, 0.0, 0.0]
    if "phone" in t:
        return [0.0, 1.0, 0.0, 0.0]
    if "enterprise" in t or "tier" in t:
        return [0.0, 0.0, 1.0, 0.0]
    return [0.0, 0.0, 0.0, 1.0]


@dataclass
class _Res:
    content: str


class FakeComplete:
    """Routes by system prompt: extraction JSON / decide-op JSON / summary text."""

    def __init__(self, extractions, decisions, summary="Refreshed running summary."):
        self.extractions = list(extractions)
        self.decisions = list(decisions)
        self.summary = summary
        self.extract_calls = 0
        self.decide_calls = 0
        self.summary_calls = 0

    async def __call__(self, role, messages, *, response_format=None):
        content = messages[0]["content"]
        if content == FACT_EXTRACTION_PROMPT:
            self.extract_calls += 1
            payload = self.extractions.pop(0) if self.extractions else {"facts": []}
            return _Res(json.dumps(payload))
        if "reconcile" in content.lower():
            self.decide_calls += 1
            payload = self.decisions.pop(0) if self.decisions else {"op": "add"}
            return _Res(json.dumps(payload))
        self.summary_calls += 1
        return _Res(self.summary)


class FakeEmbed:
    def __init__(self):
        self.calls = 0

    async def __call__(self, texts):
        self.calls += 1
        return [_vec(t) for t in texts]


def _fact(**kw):
    payload = {
        "fact_type": "preference",
        "subject": "customer",
        "confidence": 0.9,
        "importance": 6,
    }
    payload.update(kw)
    return payload


async def _seed_session(s, *, subject="user:1", session_id="sess-1", summary=None):
    s.add(MemorySession(id=session_id, subject_id=subject, summary=summary))
    s.add(
        MemoryMessage(
            subject_id=subject,
            session_id=session_id,
            turn_index=0,
            role="user",
            origin=MemoryOrigin.USER,
            content="Please contact me differently from now on.",
        )
    )
    await s.flush()


# --------------------------------------------------------------------------- tests


async def test_contradiction_invalidates_old_fact(db):
    cfg = MemoryConfig()
    async with db() as s:
        await _seed_session(s)
        old = MemoryFact(
            subject_id="user:1",
            fact_type="preference",
            predicate="prefers_channel",
            object="email",
            text="User prefers email.",
            embedding=_vec("email"),
            confidence=0.9,
            importance=6,
        )
        s.add(old)
        await s.flush()
        old_id = old.id

        fake = FakeComplete(
            extractions=[
                {"facts": [_fact(predicate="prefers_channel", object="phone",
                                 text="User now prefers phone.")]}
            ],
            decisions=[{"op": "invalidate"}],  # target defaults to sole neighbor
        )
        result = await consolidate(
            s, subject_id="user:1", session_id="sess-1", config=cfg,
            complete=fake, embed=FakeEmbed(),
        )
        assert result == ConsolidationResult(added=0, updated=0, invalidated=1, noop=0)

        # old fact is soft-invalidated, NOT deleted, still queryable
        old_row = await s.get(MemoryFact, old_id)
        assert old_row is not None
        assert old_row.invalid_at is not None
        assert old_row.object == "email"

        # the new contradicting fact is currently valid
        valid = (
            await s.execute(
                select(MemoryFact).where(
                    MemoryFact.predicate == "prefers_channel",
                    MemoryFact.invalid_at.is_(None),
                    MemoryFact.expired_at.is_(None),
                )
            )
        ).scalars().all()
        assert len(valid) == 1 and valid[0].object == "phone"
        assert valid[0].supersedes_id == old_id

        # exactly one INVALIDATE audit row
        n_inval = (
            await s.execute(
                select(func.count()).select_from(MemoryWriteLog).where(
                    MemoryWriteLog.op == WriteOp.INVALIDATE
                )
            )
        ).scalar_one()
        assert n_inval == 1


async def test_restatement_of_valid_fact_is_noop_without_llm(db):
    cfg = MemoryConfig()
    async with db() as s:
        await _seed_session(s)
        s.add(
            MemoryFact(
                subject_id="user:1",
                predicate="prefers_channel",
                object="email",
                text="User prefers email.",
                embedding=_vec("email"),
                confidence=0.9,
                importance=6,
            )
        )
        await s.flush()

        fake = FakeComplete(
            extractions=[
                {"facts": [_fact(predicate="prefers_channel", object="email",
                                 text="User prefers email.")]}
            ],
            decisions=[{"op": "add"}],  # must NOT be consumed
        )
        result = await consolidate(
            s, subject_id="user:1", session_id="sess-1", config=cfg,
            complete=fake, embed=FakeEmbed(),
        )
        assert result.noop == 1 and result.added == 0
        assert fake.decide_calls == 0  # dedup short-circuit → no decide-op LLM call

        # still exactly one fact, access bumped
        facts = (await s.execute(select(MemoryFact))).scalars().all()
        assert len(facts) == 1 and facts[0].access_count == 1


async def test_reassertion_of_invalidated_fact_adds_new_row(db):
    cfg = MemoryConfig()
    async with db() as s:
        await _seed_session(s)
        dead = MemoryFact(
            subject_id="user:1",
            predicate="prefers_channel",
            object="email",
            text="User prefers email.",
            embedding=_vec("email"),
            confidence=0.9,
            importance=6,
            invalid_at=datetime(2020, 1, 1, tzinfo=UTC),
            expired_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
        s.add(dead)
        await s.flush()
        dead_id = dead.id
        dead_invalid_at = dead.invalid_at

        fake = FakeComplete(
            extractions=[
                {"facts": [_fact(predicate="prefers_channel", object="email",
                                 text="User prefers email.")]}
            ],
            decisions=[{"op": "add"}],
        )
        result = await consolidate(
            s, subject_id="user:1", session_id="sess-1", config=cfg,
            complete=fake, embed=FakeEmbed(),
        )
        # invalidated row is not a valid neighbour → no dedup → decide → ADD
        assert result.added == 1 and result.noop == 0
        assert fake.decide_calls == 1

        all_email = (
            await s.execute(
                select(MemoryFact).where(MemoryFact.object == "email")
            )
        ).scalars().all()
        assert len(all_email) == 2  # old dead row + fresh valid row

        # old invalidated row untouched
        dead_row = await s.get(MemoryFact, dead_id)
        assert dead_row.invalid_at == dead_invalid_at
        new_valid = [f for f in all_email if f.invalid_at is None]
        assert len(new_valid) == 1 and new_valid[0].id != dead_id


async def test_confidence_gate_drops_low_candidate(db):
    cfg = MemoryConfig()
    async with db() as s:
        await _seed_session(s)
        fake = FakeComplete(
            extractions=[
                {"facts": [_fact(predicate="tier", object="gold",
                                 text="Customer is gold tier.", confidence=0.30)]}
            ],
            decisions=[],
        )
        embed = FakeEmbed()
        result = await consolidate(
            s, subject_id="user:1", session_id="sess-1", config=cfg,
            complete=fake, embed=embed,
        )
        assert result == ConsolidationResult()  # all zero
        assert fake.decide_calls == 0
        assert embed.calls == 0  # dropped before the batched embed
        n_facts = (
            await s.execute(select(func.count()).select_from(MemoryFact))
        ).scalar_one()
        assert n_facts == 0


async def test_summary_and_profile_are_written(db):
    cfg = MemoryConfig()
    async with db() as s:
        await _seed_session(s, summary=None)
        fake = FakeComplete(
            extractions=[
                {"facts": [_fact(fact_type="entity_attr", predicate="tier",
                                 object="enterprise",
                                 text="Customer is on the enterprise tier.")]}
            ],
            decisions=[{"op": "add"}],
            summary="Customer wants a channel change; enterprise tier.",
        )
        result = await consolidate(
            s, subject_id="user:1", session_id="sess-1", config=cfg,
            complete=fake, embed=FakeEmbed(),
        )
        assert result.added == 1
        assert fake.summary_calls == 1

        sess = await s.get(MemorySession, "sess-1")
        assert sess.summary == "Customer wants a channel change; enterprise tier."

        prof = (
            await s.execute(
                select(MemoryProfile).where(MemoryProfile.subject_id == "user:1")
            )
        ).scalar_one()
        assert prof.data.get("tier") == "enterprise"


async def test_enqueue_then_sweep_marks_done(db):
    cfg = MemoryConfig()
    async with db() as s:
        await _seed_session(s, subject="user:9", session_id="sess-9")
        await s.commit()

        job = await enqueue_consolidation(
            s, subject_id="user:9", session_id="sess-9"
        )
        assert job.id is not None

        pending = (
            await s.execute(
                select(MemoryConsolidationJob).where(
                    MemoryConsolidationJob.status == ConsolidationStatus.PENDING
                )
            )
        ).scalars().all()
        assert len(pending) == 1

        fake = FakeComplete(extractions=[{"facts": []}], decisions=[])
        processed = await sweep_pending(
            s, config=cfg, complete=fake, embed=FakeEmbed(), limit=10
        )
        assert processed == 1

        done = await s.get(MemoryConsolidationJob, job.id)
        assert done.status is ConsolidationStatus.DONE
        assert done.attempts == 1
