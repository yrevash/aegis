"""Observability: OpenTelemetry ``gen_ai.*`` spans → local Arize Phoenix.

Public surface (per the shared contract in ``docs/AGENT_BRIEF.md``):

- :func:`init_observability` — set up the tracer provider + local Phoenix export.
- :func:`get_tracer` — the OTel tracer used across the app.

Convenience helpers for instrumenting model calls: :func:`genai_span` (async
context manager emitting a GenAI span), :func:`set_usage`, and the
:mod:`~app.observability.semconv` attribute constants.
"""

from __future__ import annotations

from . import semconv
from .genai import GenAIOperation, genai_span, genai_span_sync, set_usage
from .otel import current_trace_id, get_tracer, init_observability
from .semconv import SpanKind
from .spans import set_span_attribute, set_span_attributes, span

__all__ = [
    "GenAIOperation",
    "SpanKind",
    "current_trace_id",
    "genai_span",
    "genai_span_sync",
    "get_tracer",
    "init_observability",
    "semconv",
    "set_span_attribute",
    "set_span_attributes",
    "set_usage",
    "span",
]
