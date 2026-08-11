"""``OtelObservabilitySink`` is the concrete ``aegis.gateway`` ``ObservabilitySink``.

Proves the sink structurally satisfies the Protocol ``aegis.gateway.llm``
already defines (so a host wires ``gateway.configure(observability=
OtelObservabilitySink())`` with no bespoke adapter) and that a real GenAI span
round-trips usage end to end through it.
"""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

import aegis.observability.otel as otel_mod
from aegis.gateway.llm import GenAIOperation as GatewayGenAIOperation
from aegis.gateway.llm import ObservabilitySink
from aegis.observability import OtelObservabilitySink, semconv


@pytest.fixture
def memory_provider(monkeypatch):
    """Bind an in-memory tracer provider to the module and return its exporter."""
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(otel_mod, "_provider", provider)
    return exporter


def test_otel_observability_sink_satisfies_gateway_protocol() -> None:
    assert isinstance(OtelObservabilitySink(), ObservabilitySink)


async def test_otel_observability_sink_round_trips_usage(memory_provider) -> None:
    sink = OtelObservabilitySink()
    async with sink.span(
        GatewayGenAIOperation.CHAT, "gpt-4o", temperature=0.2, max_tokens=256
    ) as span:
        sink.set_usage(
            span,
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.01,
            response_model="gpt-4o-2024",
        )

    spans = memory_provider.get_finished_spans()
    assert len(spans) == 1
    attrs = dict(spans[0].attributes)
    assert attrs[semconv.GEN_AI_REQUEST_MODEL] == "gpt-4o"
    assert attrs[semconv.GEN_AI_REQUEST_MAX_TOKENS] == 256
    assert attrs[semconv.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert attrs[semconv.GEN_AI_USAGE_OUTPUT_TOKENS] == 5
    assert attrs[semconv.GEN_AI_USAGE_COST] == pytest.approx(0.01)
    assert attrs[semconv.GEN_AI_RESPONSE_MODEL] == "gpt-4o-2024"


async def test_otel_observability_sink_embeddings_operation_maps(memory_provider) -> None:
    sink = OtelObservabilitySink()
    async with sink.span(GatewayGenAIOperation.EMBEDDINGS, "embed-model"):
        pass

    spans = memory_provider.get_finished_spans()
    attrs = dict(spans[0].attributes)
    assert attrs[semconv.GEN_AI_OPERATION_NAME] == "embeddings"
    assert attrs[semconv.OPENINFERENCE_SPAN_KIND] == semconv.SpanKind.EMBEDDING.value


def test_otel_observability_sink_trace_id_is_none_with_no_active_span() -> None:
    sink = OtelObservabilitySink()
    assert sink.trace_id() is None
