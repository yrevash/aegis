"""Working-memory assembly tests: budget, ordering, empty, cross-tier dedup.

These exercise the pure :func:`build_working_text` (no DB) so the budget/ordering logic
is verified deterministically, plus one end-to-end pass through
:func:`assemble_working_memory` over SQLite.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aegis.memory.config import MemoryConfig
from aegis.memory.recall import RecallBundle
from aegis.memory.scoring import RecallCandidate
from aegis.memory.stores import MemoryMessage, MemorySession
from aegis.memory.tokens import count_tokens
from aegis.memory.working import (
    _PROFILE_HEADER,
    _RAW_HEADER,
    assemble_working_memory,
    build_working_text,
)


def _fact_candidate(idx: int, text: str) -> RecallCandidate:
    return RecallCandidate(
        key=f"customer|pred_{idx}",
        text=text,
        relevance=1.0,
        payload=SimpleNamespace(id=1000 + idx),
    )


def _episodic_candidate(msg_id: int, text: str) -> RecallCandidate:
    return RecallCandidate(
        key=f"msg:{msg_id}",
        text=text,
        relevance=1.0,
        payload=SimpleNamespace(id=msg_id),
    )


def _raw_msg(msg_id: int, content: str) -> MemoryMessage:
    m = MemoryMessage(
        subject_id="user:1",
        session_id="sess-1",
        turn_index=msg_id,
        role="user",
        content=content,
    )
    m.id = msg_id
    return m


@pytest.mark.parametrize("cap", [300, 800, 2000, 8000])
@pytest.mark.parametrize("n_items", [0, 3, 20, 80])
def test_assembled_tokens_within_budget(cap: int, n_items: int):
    """Property: tokens_used never exceeds the budget, across sizes and caps."""
    cfg = MemoryConfig(ctx_token_cap=cap, answer_reserve=100)
    query = "please summarise the account status and any refund history"
    bundle = RecallBundle(
        profile_text="tier: enterprise\nregion: emea\ntimezone: Europe/Berlin",
        facts=[
            _fact_candidate(i, f"Durable fact number {i} about the customer's setup.")
            for i in range(n_items)
        ],
        episodic=[
            _episodic_candidate(500 + i, f"Earlier turn {i} discussing the issue at length.")
            for i in range(n_items)
        ],
        skills=[("handling_refunds", "Follow the refund SOP: verify, authorise, confirm." * 3)],
        running_summary="The customer opened a billing dispute and escalated twice." * 2,
    )
    raw_turns = [
        _raw_msg(i, f"user turn {i} with some detailed content to fill space")
        for i in range(n_items)
    ]

    assembled = build_working_text(bundle, raw_turns, query=query, config=cfg)
    budget = cfg.ctx_token_cap - cfg.answer_reserve - count_tokens(query)
    assert assembled.tokens_used <= budget
    assert count_tokens(assembled.text) == assembled.tokens_used


def test_profile_precedes_raw_turns():
    """Lost-in-the-middle: profile is at the TOP, raw turns at the BOTTOM."""
    cfg = MemoryConfig(ctx_token_cap=8000, answer_reserve=500)
    bundle = RecallBundle(
        profile_text="tier: enterprise\nregion: emea",
        facts=[_fact_candidate(0, "Customer prefers email.")],
        running_summary="Ongoing billing dispute.",
        episodic=[_episodic_candidate(500, "Earlier they mentioned a duplicate charge.")],
    )
    raw_turns = [_raw_msg(1, "hi there"), _raw_msg(2, "any update on my refund?")]

    assembled = build_working_text(bundle, raw_turns, query="update?", config=cfg)
    assert _PROFILE_HEADER in assembled.text
    assert _RAW_HEADER in assembled.text
    assert assembled.text.index(_PROFILE_HEADER) < assembled.text.index(_RAW_HEADER)


def test_empty_recall_yields_empty_text():
    """No recalled material → text == '' so the single-shot path injects nothing."""
    cfg = MemoryConfig()
    assembled = build_working_text(RecallBundle(), [], query="anything", config=cfg)
    assert assembled.text == ""
    assert assembled.tokens_used == 0
    assert assembled.recalled_fact_ids == []
    assert assembled.recalled_message_ids == []


def test_cross_tier_dedup_message_not_double_injected():
    """A message id present in BOTH episodic and the raw window is injected once."""
    cfg = MemoryConfig(ctx_token_cap=8000, answer_reserve=200)
    shared_id = 77
    bundle = RecallBundle(
        episodic=[_episodic_candidate(shared_id, "shared message content")],
    )
    raw_turns = [_raw_msg(shared_id, "shared message content"), _raw_msg(78, "distinct later turn")]

    assembled = build_working_text(bundle, raw_turns, query="q", config=cfg)
    assert assembled.recalled_message_ids.count(shared_id) == 1  # deduped across tiers
    assert 78 in assembled.recalled_message_ids  # the distinct raw turn still injected


def test_budget_forces_eviction_of_raw_turns():
    """Under a tight budget, low-priority raw turns are evicted (non-LLM valve)."""
    cfg = MemoryConfig(ctx_token_cap=260, answer_reserve=100)
    bundle = RecallBundle(
        profile_text="tier: enterprise\nregion: emea\ntimezone: Europe/Berlin",
        facts=[
            _fact_candidate(i, f"Durable fact {i} that occupies real budget space here.")
            for i in range(6)
        ],
    )
    raw_turns = [_raw_msg(i, f"raw turn {i} " * 20) for i in range(10)]
    assembled = build_working_text(bundle, raw_turns, query="hello", config=cfg)
    budget = cfg.ctx_token_cap - cfg.answer_reserve - count_tokens("hello")
    assert assembled.tokens_used <= budget
    # Not every raw turn can fit — eviction/tier-cap kept it under budget.
    assert len(assembled.recalled_message_ids) < len(raw_turns)


@pytest.mark.asyncio
async def test_assemble_end_to_end(db):
    """Full recall → assemble path returns a budgeted block with recorded ids."""
    cfg = MemoryConfig()
    async with db() as s:
        s.add(MemorySession(id="sess-1", subject_id="user:1", summary="Prior billing dispute."))
        s.add(
            MemoryMessage(
                subject_id="user:1",
                session_id="sess-1",
                turn_index=0,
                role="user",
                content="I was charged twice for my subscription.",
            )
        )
        await s.commit()

    async with db() as s:
        assembled = await assemble_working_memory(
            s,
            subject_id="user:1",
            session_id="sess-1",
            persona="ops",
            query="what's the status of my refund?",
            query_vec=None,
            config=cfg,
        )
    query = "what's the status of my refund?"
    budget = cfg.ctx_token_cap - cfg.answer_reserve - count_tokens(query)
    assert assembled.tokens_used <= budget
    assert "Prior billing dispute." in assembled.text  # running summary injected
    assert assembled.recalled_message_ids  # the raw turn was recorded as injected
