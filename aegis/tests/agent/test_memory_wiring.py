"""Wiring tests for the long-term-memory integration (fakes only, no infra).

Two graph-level properties, each load-bearing:

1. **Backward-compat.** A run with ``session_id=None`` and ``deps.memory=None`` yields
   the SAME ordered event stream as today — the two silent nodes
   (``recall_memory``/``persist_memory``) add no events at all.
2. **Memory-active.** With a fake ``MemoryDeps`` injected and a ``session_id`` present,
   the assembled working memory is threaded into the plan/generate system prompt, a
   ``memory`` event is emitted, and the user + assistant turns are persisted.

(The ``query_vec`` surfacing property is exercised in ``aegis.retrieval``'s own tests.)
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aegis.agent import run_agent

_MONEY_SHOT = ["run_started", "retrieval", "token", "run_finished"]


def _ordered_subsequence(whole: list[str], sub: list[str]) -> bool:
    it = iter(whole)
    return all(item in it for item in sub)


class _FakeMemory:
    """Recording stand-in for the concrete MemoryDeps (duck-typed to the Protocol)."""

    def __init__(self) -> None:
        self.assembled = SimpleNamespace(
            text="## Durable facts\n- prefers email\n- CET",
            recalled_fact_ids=[1],
            recalled_message_ids=[7, 8],
            tokens_used=7,
        )
        self.assemble_calls: list[dict] = []
        self.persist_calls: list[dict] = []

    async def assemble(self, *, subject_id, session_id, persona, query, query_vec):  # noqa: ANN001
        self.assemble_calls.append({"subject_id": subject_id, "query": query})
        return self.assembled

    async def persist(  # noqa: ANN001
        self, *, subject_id, session_id, turn_index, user_text, assistant_text,
        query_vec, run_id, trace_id,
    ) -> None:
        self.persist_calls.append(
            {
                "subject_id": subject_id,
                "session_id": session_id,
                "user_text": user_text,
                "assistant_text": assistant_text,
            }
        )


@pytest.mark.asyncio
async def test_memory_inactive_stream_is_unchanged(make_deps):
    deps = make_deps(propose_tool=False)  # build_fake_deps leaves deps.memory = None
    assert deps.memory is None

    events = [e async for e in run_agent("what is the refund policy?", deps=deps)]
    types = [e["type"] for e in events]

    assert "memory" not in types
    node_names = {
        e["node"] for e in events if e["type"] in ("node_started", "node_finished")
    }
    assert "recall_memory" not in node_names
    assert "persist_memory" not in node_names
    assert _ordered_subsequence(types, _MONEY_SHOT)
    assert types[-1] == "run_finished"


@pytest.mark.asyncio
async def test_memory_active_injects_working_memory_and_persists(make_deps):
    deps = make_deps(propose_tool=False)
    fake_mem = _FakeMemory()
    deps.memory = fake_mem

    seen_extra: list[str | None] = []

    def render_system_prompt(persona: str, extra_context: str | None = None) -> str:
        seen_extra.append(extra_context)
        base = "You are a helpful, grounded support assistant."
        return f"{base}\n\n{extra_context}" if extra_context else base

    deps.render_system_prompt = render_system_prompt

    events = [
        e
        async for e in run_agent(
            "please help with my account",
            persona="operations_lead",
            deps=deps,
            session_id="sess-1",
            memory_subject="user:1",
        )
    ]
    types = [e["type"] for e in events]

    assert fake_mem.assemble_calls
    assert any(extra == fake_mem.assembled.text for extra in seen_extra)
    assert any(extra for extra in seen_extra)

    memory_events = [e for e in events if e["type"] == "memory"]
    assert len(memory_events) == 1
    assert memory_events[0]["recalled_fact_count"] == 1
    assert memory_events[0]["recalled_message_count"] == 2
    assert memory_events[0]["tokens_used"] == 7

    assert len(fake_mem.persist_calls) == 1
    call = fake_mem.persist_calls[0]
    assert call["subject_id"] == "user:1"
    assert call["session_id"] == "sess-1"
    assert call["user_text"] == "please help with my account"
    assert call["assistant_text"]
    assert types[-1] == "run_finished"


@pytest.mark.asyncio
async def test_memory_inert_when_subject_none(make_deps):
    """A session_id with NO resolved subject stays inert (no event, no persist)."""
    deps = make_deps(propose_tool=False)
    fake_mem = _FakeMemory()
    deps.memory = fake_mem

    types = [
        e["type"]
        async for e in run_agent(
            "hello", deps=deps, session_id="sess-1", memory_subject=None
        )
    ]

    assert "memory" not in types
    assert fake_mem.assemble_calls == []
    assert fake_mem.persist_calls == []
