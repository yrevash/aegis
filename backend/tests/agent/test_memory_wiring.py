"""Wiring tests for the long-term-memory integration (fakes only, no infra).

Three properties are load-bearing and each test below *bites*:

1. **Backward-compat.** A run with ``session_id=None`` and ``deps.memory=None`` yields
   the SAME ordered event stream as today — the two new silent nodes
   (``recall_memory``/``persist_memory``) add no events at all.
2. **Memory-active.** With a fake ``MemoryDeps`` injected and a ``session_id`` present,
   the assembled working memory is threaded into the plan/generate system prompt
   (``render_system_prompt`` receives non-empty ``extra_context``), a ``memory`` event
   is emitted, and the user + assistant turns are persisted.
3. **query_vec surfacing.** ``retrieve`` puts the query embedding on the result only
   when it is a real ``EMBED_DIM`` gateway vector; a lite/256-dim vector and an exact
   cache hit leave it ``None`` (so no mismatched-dim vector reaches the episodic write).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent import run_agent
from app.data.models import EMBED_DIM
from app.memory.working import AssembledMemory
from app.retrieval.cache import SemanticCache
from app.retrieval.models import Candidate, Recall
from app.retrieval.pipeline import RetrievalConfig, RetrievalScope, Retriever

#: The unscoped (no tenant) partition these tests retrieve under.
_SCOPE = RetrievalScope(tenant_id=None)

# The money-shot subsequence every /query run preserves (see test_orchestrator.py).
_MONEY_SHOT = ["run_started", "retrieval", "token", "run_finished"]


def _ordered_subsequence(whole: list[str], sub: list[str]) -> bool:
    it = iter(whole)
    return all(item in it for item in sub)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Backward-compat: session_id=None + deps.memory=None → today's exact stream
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_memory_inactive_stream_is_unchanged(make_deps):
    deps = make_deps(propose_tool=False)  # build_fake_deps leaves deps.memory = None
    assert deps.memory is None

    events = [e async for e in run_agent("what is the escalation policy?", deps=deps)]
    types = [e.type for e in events]

    # The two new nodes are SILENT: no memory event, and no node events for them.
    assert "memory" not in types
    node_names = {e.node for e in events if e.type in ("node_started", "node_finished")}
    assert "recall_memory" not in node_names
    assert "persist_memory" not in node_names
    # The exact money-shot ordering still holds; stream still ends on run_finished.
    assert _ordered_subsequence(types, _MONEY_SHOT)
    assert types[-1] == "run_finished"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Memory-active: fake MemoryDeps → working memory injected + memory event + persist
# ─────────────────────────────────────────────────────────────────────────────


class _FakeMemory:
    """Recording stand-in for :class:`app.agent.deps.MemoryDeps` (duck-typed)."""

    def __init__(self) -> None:
        self.assembled = AssembledMemory(
            text="## Durable facts\n- the customer prefers email",
            recalled_fact_ids=[1],
            recalled_message_ids=[2, 3],
            tokens_used=7,
        )
        self.assemble_calls: list[dict] = []
        self.persist_calls: list[dict] = []

    async def assemble(self, *, subject_id, session_id, persona, query, query_vec):  # noqa: ANN001
        self.assemble_calls.append(
            {"subject_id": subject_id, "session_id": session_id, "query": query}
        )
        return self.assembled

    async def persist(  # noqa: ANN001
        self,
        *,
        subject_id,
        session_id,
        turn_index,
        user_text,
        assistant_text,
        query_vec,
        run_id,
        trace_id,
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
async def test_memory_active_injects_working_memory_and_persists(make_deps):
    deps = make_deps(propose_tool=False)
    fake_mem = _FakeMemory()
    deps.memory = fake_mem

    # Record every extra_context the graph passes into render_system_prompt.
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
    types = [e.type for e in events]

    # The recall node ran and assembled memory was injected into the plan prompt.
    assert fake_mem.assemble_calls
    assert any(extra == fake_mem.assembled.text for extra in seen_extra)
    assert any(extra for extra in seen_extra)  # non-empty extra_context reached it

    # The glass-box memory event was emitted with the recalled counts.
    memory_events = [e for e in events if e.type == "memory"]
    assert len(memory_events) == 1
    assert memory_events[0].recalled_fact_count == 1
    assert memory_events[0].recalled_message_count == 2
    assert memory_events[0].tokens_used == 7

    # The turn was persisted exactly once, with the user query + a real answer.
    assert len(fake_mem.persist_calls) == 1
    call = fake_mem.persist_calls[0]
    assert call["subject_id"] == "user:1"
    assert call["session_id"] == "sess-1"
    assert call["user_text"] == "please help with my account"
    assert call["assistant_text"]  # the generated answer, non-empty

    # And the run still completes cleanly.
    assert types[-1] == "run_finished"


@pytest.mark.asyncio
async def test_memory_inert_when_subject_none(make_deps):
    """A session_id with NO resolved subject stays inert (no event, no persist)."""
    deps = make_deps(propose_tool=False)
    fake_mem = _FakeMemory()
    deps.memory = fake_mem

    types = [
        e.type
        async for e in run_agent(
            "hello", deps=deps, session_id="sess-1", memory_subject=None
        )
    ]

    assert "memory" not in types
    assert fake_mem.assemble_calls == []
    assert fake_mem.persist_calls == []


# ─────────────────────────────────────────────────────────────────────────────
# 3. retrieve surfaces query_vec only for a real EMBED_DIM vector
# ─────────────────────────────────────────────────────────────────────────────


class _FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    async def get(self, key):  # noqa: ANN001
        return self.kv.get(key)

    async def set(self, key, value, *, ex=None):  # noqa: ANN001
        self.kv[key] = value
        return True

    async def sadd(self, key, *values):  # noqa: ANN001
        self.sets.setdefault(key, set()).update(values)
        return len(values)

    async def smembers(self, key):  # noqa: ANN001
        return set(self.sets.get(key, set()))


class _FakeBackend:
    def __init__(self, recall: Recall) -> None:
        self._recall = recall
        self.recall_calls = 0

    async def recall(self, query, *, top_k, scope):  # noqa: ANN001
        self.recall_calls += 1
        return self._recall

    async def ingest_chunks(self, chunks):  # noqa: ANN001
        return (0, 0)


class _Complete:
    def __init__(self, content: str) -> None:
        self.content = content

    async def __call__(self, role, messages, *, temperature=0.0, response_format=None):  # noqa: ANN001
        return SimpleNamespace(content=self.content)


class _Embed:
    def __init__(self, vector: list[float]) -> None:
        self.vector = vector

    async def __call__(self, texts):  # noqa: ANN001
        return [list(self.vector) for _ in texts]


def _retriever(embed: _Embed) -> Retriever:
    recall = Recall(candidates=[Candidate(id="c0", text="the sky is blue")])
    return Retriever(
        backend=_FakeBackend(recall),
        cache=SemanticCache(_FakeRedis(), ttl_seconds=60, similarity_threshold=0.99),
        complete=_Complete('{"scores": [{"id": 0, "score": 5}]}'),
        embed=embed,
        config=RetrievalConfig(recall_top_k=5, final_top_k=2),
    )


@pytest.mark.asyncio
async def test_retrieve_surfaces_real_embed_dim_query_vec():
    retriever = _retriever(_Embed([0.1] * EMBED_DIM))
    result = await retriever.retrieve("why is the sky blue?", scope=_SCOPE)

    assert result.cache_hit is False
    assert result.query_vec is not None
    assert len(result.query_vec) == EMBED_DIM
    assert result.query_vec_dim == EMBED_DIM


@pytest.mark.asyncio
async def test_retrieve_leaves_query_vec_none_for_lite_256_dim():
    retriever = _retriever(_Embed([0.1] * 256))
    result = await retriever.retrieve("why is the sky blue?", scope=_SCOPE)

    # Recorded by dimension, but NOT reusable → not a recall-comparable vector.
    assert result.query_vec is None
    assert result.query_vec_dim == 256


@pytest.mark.asyncio
async def test_exact_cache_hit_leaves_query_vec_none():
    retriever = _retriever(_Embed([0.1] * EMBED_DIM))
    first = await retriever.retrieve("why is the sky blue?", scope=_SCOPE)
    assert first.query_vec is not None  # the miss surfaced it

    # The identical query is served from the exact cache — no embedding recomputed, so
    # the reusable vector is absent (the cache never stored a 3072-float blob).
    hit = await retriever.retrieve("why is the sky blue?", scope=_SCOPE)
    assert hit.cache_hit is True
    assert hit.query_vec is None
    assert hit.query_vec_dim is None
