"""Tests for streaming the memory lifecycle — recall, cache, add, and forget events."""

from __future__ import annotations

import json

from sqlalchemy import select

from aegis.core import stream_names
from aegis.core.stream import AegisEmitter
from aegis.memory.cache import MemorySemanticCache
from aegis.memory.config import MemoryConfig
from aegis.memory.stores import MemoryFact, MemorySession, MemoryWriteLog, WriteOp
from aegis.memory.stream import stream_add, stream_assemble, stream_forget
from aegis.memory.working import AssembledMemory


async def _embedder(texts: list[str]) -> list[list[float]]:
    return [[1.0, 0.0] for _ in texts]


class _Reply:
    def __init__(self, content: str = "") -> None:
        self.content = content


async def _complete(*_args, **_kwargs) -> _Reply:
    return _Reply("")  # empty extraction/summary → a no-op consolidation


class CaptureSink:
    """Sink that captures encoded SSE frames."""

    def __init__(self) -> None:
        self.frames: list[str] = []

    async def __call__(self, frame: str) -> None:
        self.frames.append(frame)


def _payloads(frames: list[str]) -> list[dict]:
    return [json.loads(f[len("data: ") :].strip()) for f in frames]


class FakeAssembler:
    """An assembler satisfying `AssembleLike`, returning a fixed assembled block."""

    def __init__(self, assembled: AssembledMemory) -> None:
        self.assembled = assembled
        self.calls: list[dict] = []

    async def assemble(
        self, *, subject_id, session_id, persona, query, query_vec
    ) -> AssembledMemory:
        self.calls.append(
            {
                "subject_id": subject_id,
                "session_id": session_id,
                "persona": persona,
                "query": query,
                "query_vec": query_vec,
            }
        )
        return self.assembled


async def test_stream_assemble_emits_step_then_memory_recall_then_finished():
    sink = CaptureSink()
    emitter = AegisEmitter(thread_id="t", run_id="r", sink=sink)
    assembler = FakeAssembler(
        AssembledMemory(
            text="## Durable facts\n- Customer prefers email.",
            recalled_fact_ids=[1, 2, 3],
            recalled_message_ids=[10, 11],
            tokens_used=42,
        )
    )

    result = await stream_assemble(
        assembler,
        emitter,
        subject_id="user:1",
        session_id="sess-1",
        persona="ops",
        query="how do I contact you?",
        query_vec=[1.0, 0.0],
    )

    payloads = _payloads(sink.frames)
    assert [p["type"] for p in payloads] == ["STEP_STARTED", "CUSTOM", "STEP_FINISHED"]
    assert payloads[0]["stepName"] == "recall_memory"
    assert payloads[2]["stepName"] == "recall_memory"

    event = payloads[1]
    assert event["name"] == stream_names.MEMORY_RECALL
    assert event["value"] == {
        "recalled_fact_count": 3,
        "recalled_message_count": 2,
        "recalled_fact_ids": [1, 2, 3],
        "recalled_message_ids": [10, 11],
        "tokens_used": 42,
    }

    # The assembler received the forwarded args, and the full block is returned.
    assert assembler.calls[0]["subject_id"] == "user:1"
    assert assembler.calls[0]["query_vec"] == [1.0, 0.0]
    assert result.tokens_used == 42


async def test_stream_assemble_cache_miss_then_hit_runs_expensive_path_once():
    sink = CaptureSink()
    emitter = AegisEmitter(thread_id="t", run_id="r", sink=sink)
    assembler = FakeAssembler(
        AssembledMemory(
            text="## Durable facts\n- Customer prefers email.",
            recalled_fact_ids=[1],
            recalled_message_ids=[],
            tokens_used=7,
        )
    )
    cache = MemorySemanticCache.in_memory(MemoryConfig(), embedder=_embedder)

    kw = dict(subject_id="user:1", session_id="s", query="prefs?", query_vec=[1.0, 0.0])

    # 1st call → cache MISS → the expensive assembler runs and the result is written back.
    first = await stream_assemble(assembler, emitter, cache=cache, **kw)
    events = [json.loads(f[len("data: ") :])["value"] for f in sink.frames
              if json.loads(f[len("data: ") :])["type"] == "CUSTOM"
              and json.loads(f[len("data: ") :])["name"] == stream_names.MEMORY_CACHE]
    assert events[0]["event"] == "miss"
    assert len(assembler.calls) == 1

    # 2nd identical call → cache HIT → assembler NOT re-run; block reconstructed from cache.
    sink.frames.clear()
    second = await stream_assemble(assembler, emitter, cache=cache, **kw)
    hit_events = [json.loads(f[len("data: ") :])["value"] for f in sink.frames
                  if json.loads(f[len("data: ") :])["type"] == "CUSTOM"
                  and json.loads(f[len("data: ") :])["name"] == stream_names.MEMORY_CACHE]
    assert hit_events[0]["event"] == "hit"
    assert hit_events[0]["backend"] == "in-memory"
    assert len(assembler.calls) == 1  # PROOF: the expensive path ran exactly once
    assert second.text == first.text
    assert second.recalled_fact_ids == [1]


async def test_stream_add_emits_memory_write_and_evicts_cache(db):
    sink = CaptureSink()
    emitter = AegisEmitter(thread_id="t", run_id="r", sink=sink)
    cache = MemorySemanticCache.in_memory(MemoryConfig(), embedder=_embedder)
    # Warm the subject's cache so we can prove the write invalidates it.
    await cache.store(subject_id="cust:1", query="q", value={"text": ""}, query_vec=[1.0, 0.0])

    async with db() as s:
        s.add(MemorySession(id="sess-1", subject_id="cust:1"))
        await s.commit()
        await stream_add(
            s,
            emitter,
            subject_id="cust:1",
            session_id="sess-1",
            config=MemoryConfig(),
            complete=_complete,
            embed=_embedder,
            cache=cache,
        )

    payloads = _payloads(sink.frames)
    writes = [p for p in payloads if p.get("name") == stream_names.MEMORY_WRITE]
    assert writes and writes[0]["value"]["op"] == "consolidate"
    evicts = [p for p in payloads if p.get("name") == stream_names.MEMORY_CACHE]
    assert evicts and evicts[0]["value"]["event"] == "evict"
    assert await cache.check(subject_id="cust:1", query="q", query_vec=[1.0, 0.0]) is None


async def test_stream_forget_emits_delete_and_evicts_cache(db):
    sink = CaptureSink()
    emitter = AegisEmitter(thread_id="t", run_id="r", sink=sink)
    cache = MemorySemanticCache.in_memory(MemoryConfig(), embedder=_embedder)
    await cache.store(subject_id="cust:1", query="q", value={"text": ""}, query_vec=[1.0, 0.0])

    async with db() as s:
        s.add(
            MemoryFact(
                subject_id="cust:1",
                fact_type="preference",
                subject="customer",
                predicate="prefers_channel",
                object="email",
                text="Customer prefers email.",
                embedding=[1.0, 0.0, 0.0, 0.0],
            )
        )
        await s.commit()
        fact_id = (await s.execute(select(MemoryFact.id))).scalar_one()

    async with db() as s:
        forgotten = await stream_forget(
            s, emitter, subject_id="cust:1", fact_id=fact_id, cache=cache
        )
    assert forgotten is True

    payloads = _payloads(sink.frames)
    writes = [p for p in payloads if p.get("name") == stream_names.MEMORY_WRITE]
    assert writes[0]["value"] == {
        "op": "delete",
        "subject_id": "cust:1",
        "fact_id": fact_id,
        "hard": False,
        "forgotten": True,
    }
    evicts = [p for p in payloads if p.get("name") == stream_names.MEMORY_CACHE]
    assert evicts and evicts[0]["value"]["event"] == "evict"
    assert await cache.check(subject_id="cust:1", query="q", query_vec=[1.0, 0.0]) is None

    # The forget was audited as a DELETE write.
    async with db() as s:
        log = (await s.execute(select(MemoryWriteLog))).scalar_one()
        assert log.op is WriteOp.DELETE
