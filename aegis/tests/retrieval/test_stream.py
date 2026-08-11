"""Tests for streaming retrieval citations via the AG-UI emitter."""

from __future__ import annotations

import json

import pytest

from aegis.core import stream_names
from aegis.core.stream import AegisEmitter
from aegis.retrieval.cache import SemanticCache
from aegis.retrieval.memory import InMemoryKnowledgeBackend, InMemoryRedis
from aegis.retrieval.pipeline import RetrievalConfig, Retriever
from aegis.retrieval.stream import stream_retrieve

from .conftest import RecordingComplete, SequenceEmbed

_DOCS = [
    ("refunds", "Refunds are issued to the original payment method within a week."),
    ("escalation", "Escalate a request to a senior agent when its deadline is at risk."),
]


class CaptureSink:
    """Sink that captures encoded event frames."""

    def __init__(self) -> None:
        """Initialize the capture sink."""
        self.frames: list[str] = []

    async def __call__(self, frame: str) -> None:
        """Capture one encoded SSE frame."""
        self.frames.append(frame)


def _payloads(frames: list[str]) -> list[dict]:
    """Extract JSON payloads from SSE frames."""
    return [json.loads(f[len("data: ") :].strip()) for f in frames]


def _retriever() -> Retriever:
    backend = InMemoryKnowledgeBackend.from_corpus(docs=_DOCS)
    cache = SemanticCache(InMemoryRedis(), ttl_seconds=60, similarity_threshold=0.95)
    complete = RecordingComplete('{"scores": [{"id": 0, "score": 9}]}')
    embed = SequenceEmbed([1.0, 0.0])
    return Retriever(
        backend=backend,
        cache=cache,
        complete=complete,
        embed=embed,
        config=RetrievalConfig(recall_top_k=8, final_top_k=3),
    )


@pytest.mark.asyncio
async def test_stream_retrieve_emits_step_then_citations_then_finished():
    retriever = _retriever()
    sink = CaptureSink()
    emitter = AegisEmitter(thread_id="t", run_id="r", sink=sink)

    result = await stream_retrieve(retriever, "refunds payment method", emitter, persona="ops")

    payloads = _payloads(sink.frames)
    assert [p["type"] for p in payloads] == ["STEP_STARTED", "CUSTOM", "STEP_FINISHED"]
    assert payloads[0]["stepName"] == "retrieve"
    assert payloads[2]["stepName"] == "retrieve"

    citations = payloads[1]
    assert citations["name"] == stream_names.RETRIEVAL_CITATIONS
    value = citations["value"]
    assert value["num_candidates"] == result.num_candidates
    assert value["cache_hit"] is result.cache_hit
    assert isinstance(value["sources"], list)
    if value["sources"]:
        source = value["sources"][0]
        assert set(source) == {"id", "label", "score", "origin", "snippet"}
    assert set(value["provenance"]) == {"origins", "fusion", "cache_kind"}
    assert set(value["graph_delta"]) == {"nodes", "edges"}


@pytest.mark.asyncio
async def test_stream_retrieve_citations_reflect_real_sources():
    retriever = _retriever()
    sink = CaptureSink()
    emitter = AegisEmitter(thread_id="t", run_id="r", sink=sink)

    result = await stream_retrieve(retriever, "refund payment method original", emitter)

    citations = _payloads(sink.frames)[1]["value"]
    assert [s["id"] for s in citations["sources"]] == [s.id for s in result.sources]
    assert citations["provenance"]["fusion"] == result.provenance.fusion.value


@pytest.mark.asyncio
async def test_stream_retrieve_returns_the_full_result():
    retriever = _retriever()
    sink = CaptureSink()
    emitter = AegisEmitter(thread_id="t", run_id="r", sink=sink)

    result = await stream_retrieve(retriever, "escalate senior agent", emitter)

    assert result.answer_context or result.sources == []
    assert result.num_candidates >= len(result.sources)
