"""Observability: OpenTelemetry ``gen_ai.*`` + OpenInference spans → local Phoenix.

Standalone, host-agnostic extraction of the tracing stack:

- :func:`init_observability` — set up the tracer provider + local Phoenix export
  (config **injected**: ``phoenix_enabled``/``service_name``/``project_name``,
  not read from any host settings module).
- :func:`get_tracer` / :func:`current_trace_id` — the OTel tracer and active
  trace id used across a host application.
- :func:`span` — a dependency-free span helper for non-LLM units of work
  (retrieval, guardrails, tools, graph nodes, …), stamping the OpenInference
  ``openinference.span.kind`` attribute from the shared
  :class:`aegis.core.events.SpanKind` (reused here, not redefined, so the same
  enum drives both the live AG-UI event stream and the exported spans).
- :func:`genai_span` / :func:`genai_span_sync` / :func:`set_usage` — the
  ``gen_ai.*`` LLM/embedding span helpers, plus :mod:`~aegis.observability.semconv`
  for the attribute-key constants.
- :class:`OtelObservabilitySink` — the concrete implementation of
  ``aegis.gateway``'s ``ObservabilitySink`` Protocol, so a host wires
  ``gateway.configure(observability=OtelObservabilitySink())`` with no bespoke
  adapter.

``opentelemetry-api``/``-sdk`` are required (``aegis[observability]``); Arize
Phoenix is optional and imported lazily, only inside :func:`init_observability`
when ``phoenix_enabled`` is true (``aegis[phoenix]``) — without it, tracing
still works via a console exporter.
"""

from __future__ import annotations

from . import semconv
from .genai import GenAIOperation, genai_span, genai_span_sync, set_usage
from .latency import (
    LatencySummary,
    NodeLatency,
    latency_summary,
    record_run_latency,
    reset_latency_window,
)
from .otel import current_trace_id, get_tracer, init_observability
from .semconv import SpanKind
from .sink import OtelObservabilitySink
from .spans import set_span_attribute, set_span_attributes, span

__all__ = [
    "GenAIOperation",
    "LatencySummary",
    "NodeLatency",
    "OtelObservabilitySink",
    "SpanKind",
    "current_trace_id",
    "genai_span",
    "genai_span_sync",
    "get_tracer",
    "init_observability",
    "latency_summary",
    "record_run_latency",
    "reset_latency_window",
    "semconv",
    "set_span_attribute",
    "set_span_attributes",
    "set_usage",
    "span",
]
