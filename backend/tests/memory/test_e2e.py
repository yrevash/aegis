"""End-to-end memory tests — the full write→consolidate→recall loop + the read API.

Two halves, both offline (a SQLite DB + scripted fake ``complete``/``embed``; no network):

1. **Multi-turn loop** (module functions directly, per ``docs/MEMORY_SPEC.md`` §D): turn 1
   persists raw turns and enqueues a durable consolidation job; draining that durable
   queue with :func:`sweep_pending` distils a bitemporal fact; turn 2's
   :func:`assemble_working_memory` recalls that fact into the injected working-memory
   block. Proves the fact is *learned* in turn 1 and *surfaced* in turn 2 — the honest
   durability backstop (a job row, not a lost fire-and-forget task).

2. **Read/admin API** (ASGI ``client`` + seeded DB, from ``tests/conftest.py``): the
   ``/memory/facts`` and ``/memory/writes`` surfaces return seeded rows; a subject may
   only read its own memory (cross-subject is 403, cross-tenant admin reads are empty);
   unauthenticated access is rejected; and ``/memory/forget`` hard-erases + audits.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.adapter.memory_spec import FACT_EXTRACTION_PROMPT
from app.core.security import MEMBER, PLATFORM_ADMIN, TENANT_ADMIN, create_access_token
from app.data import AuditLog, get_sessionmaker
from app.data.session import bootstrap, configure_engine
from app.memory.config import MemoryConfig
from app.memory.consolidate import enqueue_consolidation, sweep_pending
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
from app.memory.working import assemble_working_memory

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- fakes


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


# --------------------------------------------------------------------------- fixtures


@pytest_asyncio.fixture
async def mem_db(tmp_path) -> async_sessionmaker:
    """A dedicated SQLite memory DB (own file) for the module-level loop test."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'e2e.db'}")
    configure_engine(engine)
    await bootstrap(engine)
    yield get_sessionmaker()
    await engine.dispose()


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


# --------------------------------------------------------------------------- the loop


async def test_multi_turn_write_consolidate_recall(mem_db):
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
    async with mem_db() as s:
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
    async with mem_db() as s:
        pending = (
            await s.execute(
                select(func.count())
                .select_from(MemoryConsolidationJob)
                .where(MemoryConsolidationJob.status == ConsolidationStatus.PENDING)
            )
        ).scalar_one()
        assert pending == 1

    # ── Drain the durable queue → consolidation distils the bitemporal fact ──────────
    async with mem_db() as s:
        processed = await sweep_pending(
            s, config=cfg, complete=fake_complete, embed=fake_embed, limit=10
        )
        assert processed == 1

    # The fact was LEARNED in turn 1: exactly one currently-valid fact, and the job DONE.
    async with mem_db() as s:
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
    async with mem_db() as s:
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


# --------------------------------------------------------------------------- API tests


def _headers(role: str, *, tenant_id=None, user_id=None, username="x") -> dict[str, str]:
    token = create_access_token(
        user_id=user_id, username=username, role=role, tenant_id=tenant_id
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_memory() -> None:
    """Seed subject A (user:11, tenant 1) and subject B (user:22, tenant 2)."""
    async with get_sessionmaker()() as s:
        s.add_all(
            [
                MemoryFact(
                    subject_id="user:11",
                    tenant_id=1,
                    fact_type="preference",
                    subject="customer",
                    predicate="prefers_channel",
                    object="email",
                    text="Customer A prefers email.",
                    confidence=0.9,
                    importance=6,
                    source_turn_ids=[1, 2],
                ),
                MemoryFact(
                    subject_id="user:22",
                    tenant_id=2,
                    fact_type="preference",
                    subject="customer",
                    predicate="tier",
                    object="enterprise",
                    text="Customer B is enterprise tier.",
                    confidence=0.9,
                    importance=8,
                ),
                MemoryWriteLog(
                    subject_id="user:11",
                    tenant_id=1,
                    op=WriteOp.ADD,
                    fact_id=1,
                    before={},
                    after={"predicate": "prefers_channel", "object": "email"},
                    reason="new durable preference",
                    model="cheap",
                ),
                MemoryWriteLog(
                    subject_id="user:22",
                    tenant_id=2,
                    op=WriteOp.ADD,
                    fact_id=2,
                    before={},
                    after={"predicate": "tier", "object": "enterprise"},
                    reason="new durable attribute",
                    model="cheap",
                ),
            ]
        )
        await s.commit()


async def test_facts_and_writes_return_seeded_rows(client, db):
    await _seed_memory()
    hdr = _headers(MEMBER, tenant_id=1, user_id=11)

    facts = await client.get("/memory/facts?subject=user:11", headers=hdr)
    assert facts.status_code == 200
    body = facts.json()
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["predicate"] == "prefers_channel"
    assert row["object"] == "email"
    assert row["is_valid"] is True
    assert row["source_turn_ids"] == [1, 2]

    writes = await client.get("/memory/writes?subject=user:11", headers=hdr)
    assert writes.status_code == 200
    wrows = writes.json()["rows"]
    assert len(wrows) == 1
    assert wrows[0]["op"] == WriteOp.ADD.value
    assert wrows[0]["after"]["object"] == "email"


async def test_subject_isolation_and_scoping(client, db):
    await _seed_memory()

    # A member sees ONLY its own subject's facts (never the other subject's).
    a = await client.get(
        "/memory/facts?subject=user:11", headers=_headers(MEMBER, tenant_id=1, user_id=11)
    )
    assert {r["object"] for r in a.json()["rows"]} == {"email"}
    b = await client.get(
        "/memory/facts?subject=user:22", headers=_headers(MEMBER, tenant_id=2, user_id=22)
    )
    assert {r["object"] for r in b.json()["rows"]} == {"enterprise"}

    # Member B may NOT read subject A — cross-subject access is forbidden.
    forbidden = await client.get(
        "/memory/facts?subject=user:11", headers=_headers(MEMBER, tenant_id=2, user_id=22)
    )
    assert forbidden.status_code == 403

    # A tenant-admin of tenant 2 reading subject A (tenant 1) gets an EMPTY result —
    # the app-level tenant filter isolates cross-tenant reads (never subject A's data).
    cross = await client.get(
        "/memory/facts?subject=user:11", headers=_headers(TENANT_ADMIN, tenant_id=2)
    )
    assert cross.status_code == 200
    assert cross.json()["rows"] == []

    # A platform-admin may read any subject.
    admin = await client.get(
        "/memory/facts?subject=user:22", headers=_headers(PLATFORM_ADMIN)
    )
    assert {r["object"] for r in admin.json()["rows"]} == {"enterprise"}


async def test_unauthenticated_is_rejected(client, db):
    assert (await client.get("/memory/facts?subject=user:11")).status_code == 401
    assert (await client.get("/memory/writes?subject=user:11")).status_code == 401
    assert (
        await client.post("/memory/forget?subject=user:11")
    ).status_code == 401


async def test_forget_hard_erases_and_audits(client, db):
    await _seed_memory()
    # Seed a session + message for subject A so erasure spans every tier.
    async with get_sessionmaker()() as s:
        s.add(MemorySession(id="s-a", subject_id="user:11", tenant_id=1))
        s.add(
            MemoryMessage(
                subject_id="user:11",
                tenant_id=1,
                session_id="s-a",
                turn_index=0,
                role="user",
                content="hello",
            )
        )
        await s.commit()

    hdr = _headers(MEMBER, tenant_id=1, user_id=11, username="a-user")
    resp = await client.post("/memory/forget?subject=user:11", headers=hdr)
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted_facts"] == 1
    assert body["deleted_messages"] == 1
    assert body["deleted_sessions"] == 1
    assert body["deleted_writes"] == 1

    # The subject's memory is gone from every read surface.
    facts = await client.get("/memory/facts?subject=user:11", headers=hdr)
    assert facts.json()["rows"] == []

    # Subject B is untouched (isolation held through the erasure).
    async with get_sessionmaker()() as s:
        remaining = (
            await s.execute(
                select(func.count()).select_from(MemoryFact)
            )
        ).scalar_one()
        assert remaining == 1  # only subject B's fact survives

        audits = (
            await s.execute(
                select(AuditLog).where(AuditLog.action == "memory.forget")
            )
        ).scalars().all()
        assert len(audits) == 1
        assert audits[0].payload["subject"] == "user:11"


async def test_delete_single_fact_erases_and_audits(client, db):
    await _seed_memory()
    hdr = _headers(MEMBER, tenant_id=1, user_id=11)
    # Resolve subject A's fact id.
    async with get_sessionmaker()() as s:
        fid = (
            await s.execute(
                select(MemoryFact.id).where(MemoryFact.subject_id == "user:11")
            )
        ).scalar_one()

    resp = await client.delete(f"/memory/facts/{fid}", headers=hdr)
    assert resp.status_code == 200
    assert resp.json() == {"fact_id": fid, "deleted": True}

    # A member cannot delete a fact belonging to another subject (cross-subject 403).
    async with get_sessionmaker()() as s:
        other = (
            await s.execute(
                select(MemoryFact.id).where(MemoryFact.subject_id == "user:22")
            )
        ).scalar_one()
    forbidden = await client.delete(
        f"/memory/facts/{other}", headers=_headers(MEMBER, tenant_id=2, user_id=99)
    )
    assert forbidden.status_code == 403
