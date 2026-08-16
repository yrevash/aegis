"""Recall READ-path tests: ranking, valid-only, dedup, subject isolation, skills.

Subject isolation is an **app-level** control and stays one here: ``subject_id`` is an
opaque host identifier with no column in any policy, so the ``WHERE subject_id`` predicate
is the sole isolator (``docs/architecture/memory-spec.md`` BLOCKER 2). The tenant policy
underneath is real on this fixture, but it is a different axis and does not stand in for
this one.

Rows are written parent-first: ``memory_message.session_id`` is a live foreign key here.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from aegis.memory.config import MemoryConfig
from aegis.memory.recall import recall
from aegis.memory.stores import MemoryFact, MemoryMessage, MemorySession

from .._seed import add_in_fk_order

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
        await add_in_fk_order(
            s,
            MemorySession(id="sess-1", subject_id="user:1"),
            # turn 0 is OLD but a strong vector match → should surface via episodic recall.
            _msg("user:1", "sess-1", 0, "old but relevant closure note", [1.0, 0.0, 0.0, 0.0]),
            _msg("user:1", "sess-1", 1, "chit chat one", [0.0, 1.0, 0.0, 0.0]),
            _msg("user:1", "sess-1", 2, "chit chat two", [0.0, 1.0, 0.0, 0.0]),
            # turn 3 is recent (in the 2-turn raw window) AND a match → must be deduped.
            _msg("user:1", "sess-1", 3, "recent relevant closure note", [1.0, 0.0, 0.0, 0.0]),
        )
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
            query="closure",
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


async def test_skills_selected_for_closure_query(db):
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
            query="please close my duplicate request",
            query_vec=None,
            config=cfg,
        )
    names = [name for name, _ in bundle.skills]
    assert "closing_requests" in names
    assert all(text.strip() for _, text in bundle.skills)  # bodies actually read


async def test_recall_bumps_access_count_durably(db):
    """The recall READ path bumps + commits access_count for what it recalled this turn.

    Proves the frequency signal is real end to end: the increment survives into a fresh
    session (i.e. recall committed it), so later turns' composite can weigh it.
    """
    cfg = MemoryConfig(raw_window_turns=1)
    async with db() as s:
        await add_in_fk_order(
            s,
            MemorySession(id="sess-1", subject_id="user:1"),
            _fact("user:1", "prefers_channel", "email", [1.0, 0.0, 0.0, 0.0]),
            # turn 0 is OLD (outside the 1-turn window) yet a strong match → recalled.
            _msg("user:1", "sess-1", 0, "old relevant closure note", [1.0, 0.0, 0.0, 0.0]),
            _msg("user:1", "sess-1", 1, "recent chit chat", [0.0, 1.0, 0.0, 0.0]),
        )
        await s.commit()

    async with db() as s:
        bundle = await recall(
            s,
            subject_id="user:1",
            session_id="sess-1",
            persona="ops",
            query="closure by email",
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


async def test_null_tenant_recall_never_returns_a_tenants_profile(db):
    """SECURITY REGRESSION: the tenant predicate is NULL-symmetric, not "when supplied".

    The same ``subject_id`` may legally hold one profile per tenant. ``_recall_profile``
    only added the predicate ``if tenant_id is not None``, so an unscoped recall could
    ``.first()`` a *tenant's* profile and render it verbatim into the prompt's human block.
    """
    from aegis.memory.stores import MemoryProfile

    cfg = MemoryConfig()
    async with db() as s:
        s.add(MemorySession(id="sess-1", subject_id="user:1"))
        s.add(
            MemoryProfile(
                subject_id="user:1", tenant_id=7, data={"display_name": "Tenant Seven"}
            )
        )
        await s.commit()

    async with db() as s:
        bundle = await recall(
            s,
            subject_id="user:1",
            session_id="sess-1",
            persona="ops",
            query="who am i",
            query_vec=None,
            config=cfg,
            tenant_id=None,  # the null-tenant scope — NOT "any tenant"
        )
    assert "Tenant Seven" not in bundle.profile_text
    assert bundle.profile_text == ""

    # ...and the tenant's own recall still sees it (the fix scopes, it does not hide).
    async with db() as s:
        scoped = await recall(
            s,
            subject_id="user:1",
            session_id="sess-1",
            persona="ops",
            query="who am i",
            query_vec=None,
            config=cfg,
            tenant_id=7,
        )
    assert "Tenant Seven" in scoped.profile_text


async def test_null_tenant_recall_never_returns_a_tenants_fact(db):
    """The same NULL-symmetric scoping applies to the fact tier (also prompt-injected)."""
    cfg = MemoryConfig()
    async with db() as s:
        s.add(MemorySession(id="sess-1", subject_id="user:1"))
        f = _fact("user:1", "secret", "tenant-seven-only", [1.0, 0.0, 0.0, 0.0])
        f.tenant_id = 7
        s.add(f)
        await s.commit()

    async with db() as s:
        vec_bundle = await recall(
            s, subject_id="user:1", session_id="sess-1", persona="ops",
            query="secret", query_vec=[1.0, 0.0, 0.0, 0.0], config=cfg, tenant_id=None,
        )
        recency_bundle = await recall(
            s, subject_id="user:1", session_id="sess-1", persona="ops",
            query="secret", query_vec=None, config=cfg, tenant_id=None,
        )
    assert vec_bundle.facts == []  # ANN path: SQL source-of-truth join gates the tenant
    assert recency_bundle.facts == []  # recency fallback gates it too


async def test_episodic_recency_signal_reaches_the_output(db):
    """REGRESSION: the RRF recency list must rank turns that can actually survive.

    ``recency_list`` used to BE the raw window, every id of which the dedup filter drops —
    so RRF only ever ranked discarded items and the surviving order collapsed to pure
    vector rank. A recent, eligible turn with no comparable embedding could therefore
    never be recalled episodically at all, however close to the window it sat.
    """
    cfg = MemoryConfig(raw_window_turns=1, n_epi=4)
    async with db() as s:
        await add_in_fk_order(
            s,
            MemorySession(id="sess-1", subject_id="user:1"),
            # turn 0 is just outside the 1-turn window and carries NO embedding, so the
            # vector list can never surface it — only the recency list can.
            MemoryMessage(
                subject_id="user:1",
                session_id="sess-1",
                turn_index=0,
                role="user",
                content="my order number is 4417",
            ),
            _msg("user:1", "sess-1", 1, "recent chit chat", [0.0, 1.0, 0.0, 0.0]),
        )
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
            query="what was the order number",
            query_vec=[1.0, 0.0, 0.0, 0.0],
            config=cfg,
        )
    epi_ids = {c.payload.id for c in bundle.episodic}
    assert ids[0] in epi_ids  # recency genuinely contributed a survivor
    assert ids[1] not in epi_ids  # still deduped against the raw window
