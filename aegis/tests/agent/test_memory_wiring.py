"""Wiring tests for the long-term-memory integration (fakes only, no infra).

Three graph-level properties, each load-bearing:

1. **Backward-compat.** A run with ``session_id=None`` and ``deps.memory=None`` yields
   the SAME ordered event stream as today — the two silent nodes
   (``recall_memory``/``persist_memory``) add no events at all.
2. **Memory-active.** With a fake ``MemoryDeps`` injected and a ``session_id`` present,
   the assembled working memory is threaded into the plan/generate system prompt, a
   ``memory`` event is emitted, and the user + assistant turns are persisted.
3. **Conversation history reaches the query rewriter.** The turns recalled by
   ``recall_memory`` are what ``retrieve`` hands the pre-retrieval rewriter, so a
   follow-up turn ("what is *its* licence?") resolves against what was actually said.
   ``state["messages"]`` cannot serve this — it is a per-planning-round scratch buffer
   written by ``plan``, which runs *after* ``retrieve``, so it is empty there. This is
   driven through the REAL memory layer over SQLite and the REAL ``rewrite_query``, so a
   regression anywhere on that chain fails the test.

(The ``query_vec`` surfacing property is exercised in ``aegis.retrieval``'s own tests.)
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aegis.agent import run_agent
from aegis.data import AegisBase
from aegis.gateway.types import LLMResult, Usage
from aegis.memory.config import MemoryConfig
from aegis.memory.stores import MemoryMessage, MemorySession
from aegis.memory.working import assemble_working_memory
from tests.memory._spec import FAKE_SPEC

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


# ── History → query rewriter (the real memory layer, the real rewriter) ───────

_TURNS = [
    ("user", "Tell me about Neo4j"),
    ("assistant", "It is a graph database"),
]


class _StoreBackedMemory:
    """MemoryDeps backed by the REAL memory layer over SQLite (nothing faked between)."""

    def __init__(self, sessionmaker) -> None:  # noqa: ANN001
        self._sessionmaker = sessionmaker
        self.config = MemoryConfig()

    async def assemble(self, *, subject_id, session_id, persona, query, query_vec):  # noqa: ANN001
        async with self._sessionmaker() as s:
            return await assemble_working_memory(
                s,
                subject_id=subject_id,
                session_id=session_id,
                persona=persona,
                query=query,
                query_vec=query_vec,
                config=self.config,
                spec=FAKE_SPEC,
            )

    async def persist(self, **_kwargs) -> None:
        """No-op: this test is about the READ path."""


@pytest_asyncio.fixture
async def memory_db(tmp_path):
    """A SQLite memory store already holding the two-turn Neo4j conversation."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'agent-mem.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(AegisBase.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        s.add(MemorySession(id="sess-neo4j", subject_id="user:1"))
        for turn_index, (role, content) in enumerate(_TURNS):
            s.add(
                MemoryMessage(
                    subject_id="user:1",
                    session_id="sess-neo4j",
                    turn_index=turn_index,
                    role=role,
                    content=content,
                )
            )
        await s.commit()
    yield maker
    await engine.dispose()


def _instrument(deps, histories: list[str], queries: list[str]) -> None:
    """Record the history the REAL rewriter sees, and the query retrieval receives.

    The rewriter double resolves "its" ONLY when the antecedent is actually present in
    the CONVERSATION section of the prompt the real ``rewrite_query`` builds — so the
    rewritten query is proof that the history was threaded through, not an assertion
    about plumbing shape.
    """
    complete, retrieve = deps.complete, deps.retrieve

    async def wrapped_complete(role, messages, **kwargs):  # noqa: ANN001
        system = messages[0]["content"] if messages else ""
        user = messages[-1]["content"] if messages else ""
        if "standalone search query" not in system:
            return await complete(role, messages, **kwargs)
        conversation = user.split("CONVERSATION:\n", 1)[-1].split("\n\nLATEST TURN:")[0]
        histories.append(conversation)
        match = re.search(r"LATEST TURN: (.*?)\n\n", user, re.DOTALL)
        latest = match.group(1) if match else user
        if "its" in latest.lower() and "neo4j" in conversation.lower():
            payload = {
                "rewritten": "what is Neo4j's licence?",
                "reason": "resolved 'its' against the prior turns",
            }
        else:
            payload = {"rewritten": latest, "reason": "no antecedent in history"}
        return LLMResult(
            content=json.dumps(payload),
            tool_calls=[],
            usage=Usage(prompt_tokens=3, completion_tokens=2, cost_usd=0.0001),
            model="fake-cheap",
        )

    async def wrapped_retrieve(query, *, persona=None):  # noqa: ANN001
        queries.append(query)
        return await retrieve(query, persona=persona)

    deps.complete = wrapped_complete
    deps.retrieve = wrapped_retrieve


@pytest.mark.asyncio
async def test_rewriter_gets_the_recalled_conversation_on_the_real_graph_path(
    make_deps, memory_db
):
    """A follow-up turn resolves its pronoun against the recalled session transcript."""
    deps = make_deps(propose_tool=False)
    deps.memory = _StoreBackedMemory(memory_db)
    histories: list[str] = []
    queries: list[str] = []
    _instrument(deps, histories, queries)

    events = [
        e
        async for e in run_agent(
            "what is its licence?",
            persona="operations_lead",
            deps=deps,
            session_id="sess-neo4j",
            memory_subject="user:1",
        )
    ]

    assert histories, "the query rewriter never ran"
    # The rewriter saw the REAL transcript, oldest-first, in chat shape.
    assert "user: Tell me about Neo4j" in histories[0]
    assert "assistant: It is a graph database" in histories[0]
    assert histories[0].index("Tell me about Neo4j") < histories[0].index(
        "It is a graph database"
    )
    # ...and therefore could resolve "its" — retrieval ran on the standalone query.
    assert queries and queries[0] == "what is Neo4j's licence?"
    assert [e["type"] for e in events][-1] == "run_finished"


@pytest.mark.asyncio
async def test_single_shot_path_recalls_nothing_and_passes_no_history(make_deps):
    """No memory + no session → ``recall_memory`` emits nothing and adds no history."""
    deps = make_deps(propose_tool=False)
    assert deps.memory is None
    histories: list[str] = []
    queries: list[str] = []
    _instrument(deps, histories, queries)

    events = [e async for e in run_agent("what is its licence?", deps=deps)]

    assert "memory" not in [e["type"] for e in events]
    # The rewriter was handed no history at all — byte-identical to before the fix.
    assert histories == ["(no prior conversation)"]
    assert queries == ["what is its licence?"]
