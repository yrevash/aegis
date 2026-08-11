"""Tests for streaming memory recall's `memory_recall` event (à la carte)."""

from __future__ import annotations

import json

from aegis.core import stream_names
from aegis.core.stream import AegisEmitter
from aegis.memory.stream import stream_assemble
from aegis.memory.working import AssembledMemory


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
        "tokens_used": 42,
    }

    # The assembler received the forwarded args, and the full block is returned.
    assert assembler.calls[0]["subject_id"] == "user:1"
    assert assembler.calls[0]["query_vec"] == [1.0, 0.0]
    assert result.tokens_used == 42
