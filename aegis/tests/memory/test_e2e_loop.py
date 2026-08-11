"""End-to-end memory loop — the full write→consolidate→recall path, offline.

Turn 1 persists raw turns and enqueues a durable consolidation job; draining that durable
queue with :func:`sweep_pending` distils a bitemporal fact; turn 2's
:func:`assemble_working_memory` recalls that fact into the injected working-memory block.
Proves the fact is *learned* in turn 1 and *surfaced* in turn 2 — the honest durability
backstop (a job row, not a lost fire-and-forget task). SQLite + scripted fakes; no network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from sqlalchemy import func, select

from aegis.memory.config import MemoryConfig
from aegis.memory.consolidate import enqueue_consolidation, sweep_pending
from aegis.memory.stores import (
    ConsolidationStatus,
    MemoryConsolidationJob,
    MemoryFact,
    MemoryMessage,
    MemoryOrigin,
    MemorySession,
)
from aegis.memory.working import assemble_working_memory

from ._spec import FACT_EXTRACTION_PROMPT

pytestmark = pytest.mark.asyncio


def _vec(text: str) -> list[float]:
    """Deterministic 4-dim one-hot embedding by keyword (email/phone orthogonal)."""
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

    def __init__(self, extractions, decisions, summary="Customer prefers email contact."):
        self.extractions = list(extractions)
        self.decisions = list(decisions)
        self.summary = summary

    async def __call__(self, role, messages, *, response_format=None, **_kw):
        content = messages[0]["content"]
        if content == FACT_EXTRACTION_PROMPT:
            payload = self.extractions.pop(0) if self.extractions else {"facts": []}
            return _Res(json.dumps(payload))
        if "reconcile" in content.lower():
            payload = self.decisions.pop(0) if self.decisions else {"op": "add"}
            return _Res(json.dumps(payload))
        return _Res(self.summary)


class FakeEmbed:
    async def __call__(self, texts):
        return [_vec(t) for t in texts]


async def _add_turn(session, *, subject, session_id, turn, user_text, assistant_text):
    """Persist one raw (user, assistant) turn pair, reusing the query embedding."""
    session.add(
        MemoryMessage(
            subject_id=subject,
            session_id=session_id,
            turn_index=turn,
            role="user",
            origin=MemoryOrigin.USER,
            content=user_text,
            embedding=_vec(user_text),
            embedding_dim=4,
        )
    )
    session.add(
        MemoryMessage(
            subject_id=subject,
            session_id=session_id,
            turn_index=turn,
            role="assistant",
            origin=MemoryOrigin.ASSISTANT,
            content=assistant_text,
        )
    )


async def test_multi_turn_write_consolidate_recall(db):
    """Turn 1 learns a durable fact via the queue; turn 2 recalls it into working memory."""
    cfg = MemoryConfig()
    subject, session_id = "user:1", "sess-e2e"
    fake_complete = FakeComplete(
        extractions=[
            {
                "facts": [
                    {
                        "fact_type": "preference",
                        "subject": "customer",
                        "predicate": "prefers_channel",
                        "object": "email",
                        "text": "Customer prefers to be contacted by email.",
                        "confidence": 0.95,
                        "importance": 7,
                    }
                ]
            }
        ],
        decisions=[{"op": "add"}],  # no prior facts → the candidate is genuinely new
    )
    fake_embed = FakeEmbed()

    # ── Turn 1: persist the raw turns and ENQUEUE a durable consolidation job ────────
    async with db() as s:
        s.add(MemorySession(id=session_id, subject_id=subject))
        await _add_turn(
            s,
            subject=subject,
            session_id=session_id,
            turn=0,
            user_text="Going forward, please always reach me by email, not phone.",
            assistant_text="Understood — I'll note email as your preferred channel.",
        )
        await s.flush()
        await enqueue_consolidation(s, subject_id=subject, session_id=session_id)

    # A durable PENDING job now exists — the honest backstop (not a lost bg task).
    async with db() as s:
        pending = (
            await s.execute(
                select(func.count())
                .select_from(MemoryConsolidationJob)
                .where(MemoryConsolidationJob.status == ConsolidationStatus.PENDING)
            )
        ).scalar_one()
        assert pending == 1

    # ── Drain the durable queue → consolidation distils the bitemporal fact ──────────
    async with db() as s:
        processed = await sweep_pending(
            s, config=cfg, complete=fake_complete, embed=fake_embed, limit=10
        )
        assert processed == 1

    # The fact was LEARNED in turn 1: exactly one currently-valid fact, and the job DONE.
    async with db() as s:
        facts = (
            await s.execute(
                select(MemoryFact).where(
                    MemoryFact.subject_id == subject,
                    MemoryFact.invalid_at.is_(None),
                    MemoryFact.expired_at.is_(None),
                )
            )
        ).scalars().all()
        assert len(facts) == 1
        learned = facts[0]
        assert learned.predicate == "prefers_channel"
        assert learned.object == "email"
        assert learned.embedding is not None  # embedded during consolidation

        done = (
            await s.execute(
                select(MemoryConsolidationJob.status).where(
                    MemoryConsolidationJob.session_id == session_id
                )
            )
        ).scalar_one()
        assert done == ConsolidationStatus.DONE
        learned_id = learned.id

    # ── Turn 2: a new query recalls that durable fact into the working-memory block ──
    async with db() as s:
        await _add_turn(
            s,
            subject=subject,
            session_id=session_id,
            turn=1,
            user_text="What's the best way to send me the invoice?",
            assistant_text="",
        )
        await s.flush()

        assembled = await assemble_working_memory(
            s,
            subject_id=subject,
            session_id=session_id,
            persona=None,
            query="How should I contact you by email?",
            query_vec=_vec("email"),
            config=cfg,
        )

    # The fact SURFACED in turn 2 — injected into the assembled block by id and by text.
    assert learned_id in assembled.recalled_fact_ids
    assert "prefers to be contacted by email" in assembled.text.lower()
    assert assembled.tokens_used > 0
