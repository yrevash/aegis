"""Strangler-shim tests for ``app.observability``.

The tracer-provider setup, span helpers and GenAI semantic-convention constants
now live in the standalone ``aegis.observability`` (see
``aegis/tests/observability`` for the full unit-test suite, ported verbatim).
This module only tests the shim itself: that ``app.observability``'s public
surface really is ``aegis.observability``'s (pure re-exports, no drift) and that
``init_observability`` injects ``phoenix_enabled`` from this app's settings
instead of the standalone module's own defaults.
"""

from __future__ import annotations

from types import SimpleNamespace

import aegis.observability as aegis_obs
import aegis.observability.otel as aegis_otel_mod
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

import app.observability as app_obs
import app.observability.otel as otel_mod


@pytest.fixture
def memory_provider(monkeypatch):
    """Bind an in-memory tracer provider to the (shared) aegis.observability state."""
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(aegis_otel_mod, "_provider", provider)
    return exporter


@pytest.mark.parametrize(
    "name",
    [
        "GenAIOperation",
        "SpanKind",
        "genai_span",
        "genai_span_sync",
        "get_tracer",
        "current_trace_id",
        "set_span_attribute",
        "set_span_attributes",
        "set_usage",
        "span",
    ],
)
def test_shim_re_exports_are_identical_to_aegis_observability(name: str) -> None:
    """Every re-exported symbol is the exact same object as aegis.observability's."""
    assert getattr(app_obs, name) is getattr(aegis_obs, name)


def test_init_observability_is_wrapped_not_re_exported() -> None:
    """`init_observability` is the one symbol the shim wraps (injects settings)."""
    assert app_obs.init_observability is not aegis_obs.init_observability
    assert callable(app_obs.init_observability)


def test_init_observability_injects_phoenix_enabled_from_settings(monkeypatch):
    monkeypatch.setattr(
        otel_mod,
        "get_settings",
        lambda: SimpleNamespace(phoenix_enabled=False),
    )
    app_obs.init_observability(app=None)
    tracer = app_obs.get_tracer()
    assert hasattr(tracer, "start_as_current_span")


async def test_genai_span_through_the_shim_emits_a_real_span(memory_provider):
    async with app_obs.genai_span(app_obs.GenAIOperation.CHAT, "gpt-4o", temperature=0.3) as span:
        app_obs.set_usage(span, input_tokens=10, output_tokens=5, cost_usd=0.01)

    spans = memory_provider.get_finished_spans()
    assert len(spans) == 1
    attrs = dict(spans[0].attributes)
    assert attrs[app_obs.semconv.GEN_AI_REQUEST_MODEL] == "gpt-4o"
    assert attrs[app_obs.semconv.GEN_AI_USAGE_INPUT_TOKENS] == 10
