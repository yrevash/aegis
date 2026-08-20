"""The agent run must emit a REAL nested OpenTelemetry span tree, not just LLM spans.

Runs one query through the whole LangGraph with fake deps (offline, no network) and
captures spans with an :class:`InMemorySpanExporter`. Asserts the tree really exists:
an AGENT root (``agent.run``) with GUARDRAIL, RETRIEVER, TOOL and CHAIN child spans
nested beneath it via ``openinference.span.kind``. If someone reverts the graph/
observability instrumentation, these assertions fail — the span tree is load-bearing.
"""

from __future__ import annotations

import aegis.observability.otel as otel_mod
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from app.agent import run_agent
from app.observability import semconv

_KIND = semconv.OPENINFERENCE_SPAN_KIND


@pytest.fixture
def memory_provider(monkeypatch):
    """Bind an in-memory tracer provider to the observability module."""
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(otel_mod, "_provider", provider)
    return exporter


async def _run(deps) -> None:
    """Drive one agent run to completion (draining the event stream)."""
    async for _event in run_agent(
        "Please resolve request R1", persona="operations_lead", role="admin", deps=deps
    ):
        pass


@pytest.mark.asyncio
async def test_agent_run_emits_nested_span_tree(memory_provider, make_deps):
    # A low-risk tool executes autonomously (no human gate) so the run completes in
    # one pass and the act node runs the tool.
    await _run(make_deps(propose_tool=True, high_risk=False))

    spans = memory_provider.get_finished_spans()
    by_name = {s.name: s for s in spans}
    kind = {s.name: dict(s.attributes).get(_KIND) for s in spans}

    # ── The root AGENT span exists ──────────────────────────────────────────
    assert "agent.run" in by_name
    run_span = by_name["agent.run"]
    assert kind["agent.run"] == semconv.SpanKind.AGENT.value
    assert run_span.parent is None

    # ── The span kinds that previously emitted NOTHING now do ───────────────
    kinds_present = {v for v in kind.values() if v is not None}
    for required in (
        semconv.SpanKind.GUARDRAIL.value,
        semconv.SpanKind.RETRIEVER.value,
        semconv.SpanKind.TOOL.value,
        semconv.SpanKind.CHAIN.value,
    ):
        assert required in kinds_present, f"missing {required} span; present: {kinds_present}"

    # ── Every node span (and the tool span) nests under the run ─────────────
    run_id = run_span.context.span_id
    for node in ("node.guard_input", "node.retrieve", "node.plan", "node.act"):
        assert node in by_name, f"missing {node} span"
        assert by_name[node].parent is not None
        assert by_name[node].parent.span_id == run_id

    # Guardrail nodes carry the stage + verdict; retrieval carries the funnel.
    guard_in = dict(by_name["node.guard_input"].attributes)
    assert guard_in[semconv.GUARDRAIL_STAGE] == "input"
    assert guard_in[semconv.GUARDRAIL_VERDICT] == "pass"
    retrieve_attrs = dict(by_name["node.retrieve"].attributes)
    assert retrieve_attrs[semconv.RETRIEVAL_CANDIDATE_COUNT] == 5
    # Two: the shared fake returns one source carrying provenance metadata and one
    # carrying none, because both are shapes the real pipeline produces and the wire has
    # to render each honestly. This is the recall funnel's K, so it tracks the fake.
    assert retrieve_attrs[semconv.RETRIEVAL_RESULT_COUNT] == 2

    # ── The tool span is a real child of the act node (tool name + ok) ──────
    tool_spans = [s for s in spans if kind[s.name] == semconv.SpanKind.TOOL.value]
    assert len(tool_spans) == 1
    tool_span = tool_spans[0]
    tattrs = dict(tool_span.attributes)
    assert tattrs[semconv.TOOL_NAME] == "update_request_status"
    assert tattrs[semconv.TOOL_OK] is True
    assert tool_span.parent.span_id == by_name["node.act"].context.span_id

    # Same trace throughout — one tree, not scattered roots.
    assert {s.context.trace_id for s in spans} == {run_span.context.trace_id}


@pytest.mark.asyncio
async def test_no_tracer_configured_is_a_noop(monkeypatch, make_deps):
    # With no provider bound (lite mode / tests), instrumentation must not crash and
    # must emit no recording spans — it degrades to a global no-op tracer.
    monkeypatch.setattr(otel_mod, "_provider", None)
    exporter = InMemorySpanExporter()
    await _run(make_deps(propose_tool=True, high_risk=False))
    assert exporter.get_finished_spans() == ()
