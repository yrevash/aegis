"""Forgetting-sweep tests: stale low-value facts are soft-archived, fresh ones kept.

Proves :func:`aegis.memory.consolidate.prune_forgotten` (wired into the sweeper) actually
retires memories per ``ForgetPolicy`` — closing them in transaction-time (``expired_at``)
so they drop out of hot recall, never hard-deleting them, and auditing each as a ``PRUNE``
write-log op.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from aegis.memory.config import MemoryConfig
from aegis.memory.consolidate import prune_forgotten, sweep_pending
from aegis.memory.stores import MemoryFact, MemoryWriteLog, WriteOp

pytestmark = pytest.mark.asyncio


def _fact(subject_id: str, predicate: str, **kw) -> MemoryFact:
    return MemoryFact(
        subject_id=subject_id,
        fact_type="preference",
        subject="customer",
        predicate=predicate,
        object="x",
        text=f"Customer {predicate}.",
        **kw,
    )


async def _fail_llm(*_a, **_k):  # pragma: no cover - must never be called by prune
    raise AssertionError("prune must not call an LLM")


async def _fail_embed(_texts):  # pragma: no cover - must never be called by prune
    raise AssertionError("prune must not embed")


async def test_prune_archives_stale_keeps_fresh_and_recalled(db):
    cfg = MemoryConfig()  # forget_floor=0.05, forget_min_age_days=90, half_life_fact=30
    old = datetime.now(UTC) - timedelta(days=200)
    fresh = datetime.now(UTC)

    async with db() as s:
        # STALE: old, never recalled, low confidence → decays below floor → archivable.
        s.add(_fact("user:1", "stale", confidence=0.5, access_count=0, valid_at=old))
        # FRESH high-value → keep (age below the floor).
        s.add(_fact("user:1", "fresh", confidence=1.0, access_count=0, valid_at=fresh))
        # OLD but RECALLED (access_count>0) → keep (frequency protects it).
        s.add(_fact("user:1", "recalled", confidence=0.5, access_count=3, valid_at=old))
        await s.commit()

    async with db() as s:
        archived = await prune_forgotten(s, config=cfg)
        await s.commit()
    assert archived == 1

    async with db() as s:
        rows = {
            f.predicate: f
            for f in (await s.execute(select(MemoryFact))).scalars().all()
        }
        # Nothing hard-deleted — every fact still present (bitemporal, auditable).
        assert set(rows) == {"stale", "fresh", "recalled"}
        assert rows["stale"].expired_at is not None  # dropped from hot recall
        assert rows["fresh"].expired_at is None       # kept
        assert rows["recalled"].expired_at is None    # kept

        # The archival is audited as a PRUNE op linked to the retired fact.
        logs = (
            await s.execute(
                select(MemoryWriteLog).where(MemoryWriteLog.op == WriteOp.PRUNE)
            )
        ).scalars().all()
        assert len(logs) == 1
        assert logs[0].fact_id == rows["stale"].id
        assert logs[0].before.get("expired_at") is None
        assert logs[0].after.get("expired_at") is not None


async def test_prune_is_idempotent(db):
    """A second pass finds nothing new — already-archived facts are out of the live set."""
    cfg = MemoryConfig()
    old = datetime.now(UTC) - timedelta(days=200)
    async with db() as s:
        s.add(_fact("user:1", "stale", confidence=0.5, access_count=0, valid_at=old))
        await s.commit()

    async with db() as s:
        assert await prune_forgotten(s, config=cfg) == 1
        await s.commit()
    async with db() as s:
        assert await prune_forgotten(s, config=cfg) == 0


async def test_sweeper_runs_prune_pass(db):
    """The drain sweeper applies the forget pass even with an empty consolidation queue."""
    cfg = MemoryConfig()
    old = datetime.now(UTC) - timedelta(days=200)
    async with db() as s:
        s.add(_fact("user:1", "stale", confidence=0.5, access_count=0, valid_at=old))
        await s.commit()

    async with db() as s:
        processed = await sweep_pending(
            s, config=cfg, complete=_fail_llm, embed=_fail_embed, limit=10
        )
    assert processed == 0  # no jobs, but the prune pass still ran

    async with db() as s:
        stale = (
            await s.execute(select(MemoryFact).where(MemoryFact.predicate == "stale"))
        ).scalar_one()
        assert stale.expired_at is not None
