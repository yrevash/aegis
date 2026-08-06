"""Unit tests for the observability module (tracer setup + gen_ai spans).

No Phoenix / Docker / network: spans are captured with an in-memory exporter and
Phoenix is disabled via a stub settings object.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

import app.observability.otel as otel_mod
from app.observability import (
    GenAIOperation,
    genai_span,
    get_tracer,
    init_observability,
    semconv,
    set_usage,
)
from app.observability.otel import current_trace_id


@pytest.fixture
def memory_provider(monkeypatch):
    """Bind an in-memory tracer provider to the module and return its exporter."""
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(otel_mod, "_provider", provider)
    return exporter


async def test_genai_span_sets_request_and_usage_attributes(memory_provider):
    async with genai_span(GenAIOperation.CHAT, "gpt-4o", temperature=0.3) as span:
        set_usage(span, input_tokens=10, output_tokens=5, cost_usd=0.01)

    spans = memory_provider.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "chat gpt-4o"
    attrs = dict(span.attributes)
    assert attrs[semconv.GEN_AI_OPERATION_NAME] == "chat"
    assert attrs[semconv.GEN_AI_REQUEST_MODEL] == "gpt-4o"
    assert attrs[semconv.GEN_AI_REQUEST_TEMPERATURE] == pytest.approx(0.3)
    assert attrs[semconv.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert attrs[semconv.GEN_AI_USAGE_OUTPUT_TOKENS] == 5
    assert attrs[semconv.GEN_AI_USAGE_COST] == pytest.approx(0.01)
    # Deprecated alias emitted for backward compatibility.
    assert attrs[semconv.GEN_AI_SYSTEM] == semconv.DEFAULT_PROVIDER


async def test_genai_span_records_exception(memory_provider):
    with pytest.raises(RuntimeError):
        async with genai_span(GenAIOperation.CHAT, "gpt-4o"):
            raise RuntimeError("boom")

    span = memory_provider.get_finished_spans()[0]
    assert span.status.status_code.name == "ERROR"


async def test_current_trace_id_inside_span_is_hex(memory_provider):
    async with genai_span(GenAIOperation.EMBEDDINGS, "embed-model"):
        trace_id = current_trace_id()
    assert trace_id is not None
    assert len(trace_id) == 32
    int(trace_id, 16)  # valid hex


def test_init_observability_fallback_when_phoenix_disabled(monkeypatch):
    monkeypatch.setattr(
        otel_mod,
        "get_settings",
        lambda: SimpleNamespace(phoenix_enabled=False),
    )
    init_observability(app=None)
    tracer = get_tracer()
    assert hasattr(tracer, "start_as_current_span")


def test_get_tracer_returns_usable_tracer(memory_provider):
    tracer = get_tracer()
    assert hasattr(tracer, "start_as_current_span")
