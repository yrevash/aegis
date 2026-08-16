"""Multi-agent memory is real: subject+tenant scoped and SHARED across agent runs.

Memory is not per-agent scratch — it is durable, subject-scoped, and shared. Two
independent agent runs (separate DB sessions) over the SAME subject read/write the SAME
facts, while a different subject/tenant sees nothing. The isolator under test is the
app-level ``WHERE subject_id`` (+ ``tenant_id``): ``subject_id`` is an opaque host
identifier that no RLS policy can key on, so it has to hold on its own. (The tenant
policy is live on this fixture, but it is a second, coarser axis — it would not catch a
subject leak inside one tenant, which is the leak these tests are about.)
"""

from __future__ import annotations

import pytest

from aegis.memory.config import MemoryConfig
from aegis.memory.recall import recall
from aegis.memory.stores import MemoryFact, MemorySession

pytestmark = pytest.mark.asyncio


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


async def test_agent_b_reads_fact_agent_a_wrote(db):
    cfg = MemoryConfig()
    vec = [1.0, 0.0, 0.0, 0.0]

    # Agent A's run: writes a durable fact for the shared subject.
    async with db() as s:
        s.add(MemorySession(id="sess-A", subject_id="cust:42"))
        s.add(_fact("cust:42", "prefers_channel", "email", vec, importance=6))
        await s.commit()

    # Agent B's run: a fresh session/thread over the SAME subject recalls it.
    async with db() as s:
        s.add(MemorySession(id="sess-B", subject_id="cust:42"))
        await s.commit()
        bundle = await recall(
            s,
            subject_id="cust:42",
            session_id="sess-B",
            persona="ops",
            query="what channel do they prefer",
            query_vec=vec,
            config=cfg,
        )

    keys = [c.key for c in bundle.facts]
    assert "customer|prefers_channel" in keys  # B sees A's write — shared memory


async def test_other_subject_does_not_see_the_fact(db):
    cfg = MemoryConfig()
    vec = [1.0, 0.0, 0.0, 0.0]

    async with db() as s:
        s.add(MemorySession(id="sess-A", subject_id="cust:42"))
        s.add(_fact("cust:42", "prefers_channel", "email", vec, importance=6))
        s.add(MemorySession(id="sess-Z", subject_id="cust:99"))
        await s.commit()

    async with db() as s:
        bundle = await recall(
            s,
            subject_id="cust:99",  # a different subject
            session_id="sess-Z",
            persona="ops",
            query="what channel do they prefer",
            query_vec=vec,
            config=cfg,
        )
    assert bundle.facts == []  # isolation: another subject's memory is invisible


async def test_tenant_scopes_shared_reads(db):
    cfg = MemoryConfig()
    vec = [1.0, 0.0, 0.0, 0.0]

    async with db() as s:
        s.add(MemorySession(id="sess-A", subject_id="cust:42", tenant_id=1))
        s.add(_fact("cust:42", "prefers_channel", "email", vec, importance=6, tenant_id=1))
        await s.commit()

    # Same subject id under a different tenant must not read tenant 1's fact.
    async with db() as s:
        bundle = await recall(
            s,
            subject_id="cust:42",
            session_id="sess-A",
            persona="ops",
            query="what channel do they prefer",
            query_vec=vec,
            config=cfg,
            tenant_id=2,
        )
    assert bundle.facts == []
