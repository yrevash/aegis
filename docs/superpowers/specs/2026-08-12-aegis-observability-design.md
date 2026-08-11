# aegis.observability — OTel/Phoenix Tracing (extraction) Design Spec

- **Date:** 2026-08-12 · **Branch:** `feat/aegis-module-contract` · Module 7 of 8
- **Map:** `.superpowers/sdd/module-observability-map.md`

## 1. Goal

Extract the OpenTelemetry → Arize-Phoenix tracing into a standalone importable **`aegis.observability`**:
`gen_ai.*` + OpenInference spans, in-process Phoenix (no Docker), graceful console-exporter fallback. It
provides the concrete **`OtelObservabilitySink`** satisfying the `ObservabilitySink` Protocol `aegis.gateway`
already defines (so hosts wire it with no bespoke adapter). Small + self-contained: ONE `app.*` coupling to
sever (`config.phoenix_enabled` → injected). OTel under `aegis[observability]`; Phoenix optional under
`aegis[phoenix]`.

## 2. Design (`aegis/src/aegis/observability/`)

Move `otel.py`, `spans.py`, `genai.py`, `semconv.py` (rebinding). Preserve signatures: `init_observability`,
`get_tracer`, `current_trace_id`, `span(kind, name, *, attributes=None)`, `set_span_attribute(s)`,
`genai_span`/`genai_span_sync`, `set_usage`, `GenAIOperation`, the semconv attribute-key constants.

### Sever the one coupling
`otel.py`'s `from app.config import get_settings` (reads `phoenix_enabled`) → inject:
`init_observability(*, phoenix_enabled: bool = True, service_name: str = "taif", project_name: str = "taif", app=None)`.
No other `app.*` import.

### Reuse aegis.core.events.SpanKind (don't redefine)
`aegis.core.events.SpanKind` already has all 9 kinds (AGENT/CHAIN/TOOL/RETRIEVER/RERANKER/GUARDRAIL/LLM/
EMBEDDING/EVALUATOR) — the superset. `semconv.py` imports `SpanKind` from `aegis.core.events`; keep
`GenAIOperation` + the attribute-key strings in `aegis.observability.semconv`. `span()`/`genai_span()` stamp
`openinference.span.kind` from the shared enum.

### Provide the concrete sink (key integration)
`aegis/src/aegis/observability/sink.py`: `OtelObservabilitySink` implementing `aegis.gateway`'s
`ObservabilitySink` Protocol (`span(operation, model, *, temperature, max_tokens)` → `genai_span`;
`set_usage(...)` → `set_usage`; `trace_id()` → `current_trace_id`). Backend then does
`gateway.configure(observability=OtelObservabilitySink())` — replacing the current bespoke `_ObservabilitySink`
adapter in `app.core.llm`.

### Optional (deferred, net-new)
An AG-UI-stream→OTel bridge (`emit_spans_from_events`) — the events already carry `span_kind`/`trace_id`/
`parent_span_id`, but no projection exists today. DEFER to a followup (not part of this extraction).

## 3. Extras

`observability = ["opentelemetry-sdk>=1.27", "opentelemetry-api>=1.27"]` (mandatory eager); `phoenix =
["arize-phoenix>=5.0", "arize-phoenix-otel>=0.6"]` (optional, lazy — the module try/except-degrades to console
exporter without it). Add to `all`. `aegis.core` stays minimal (extend the dep-free guard to also ban
`opentelemetry` from core — observability is a leaf).

## 4. Strangler shim

`backend/src/app/observability/` → delegate to `aegis.observability`, injecting `phoenix_enabled` from
`app.config`. `app.main.init_observability` unchanged (calls through the shim). `agent/graph.py` +
`orchestrator.py` use `span`/`get_tracer`/`SpanKind`/`current_trace_id` through the shim (STAY app-layer —
agent not extracted yet; `SpanKind` re-exports from `aegis.core.events`). `app.core.llm`'s `_ObservabilitySink`
→ `aegis.observability.OtelObservabilitySink`.

## 5. Testing & proof

Port `backend/tests/observability/` (hermetic — `InMemorySpanExporter`, stubs `phoenix_enabled`) → verbatim,
changing import paths + stub→injected config. Add: an import guard (`import aegis.observability` pulls no
fastapi/litellm; Phoenix stays absent unless installed). A sink-conformance test (`OtelObservabilitySink`
satisfies `aegis.gateway.ObservabilitySink` structurally + a genai span round-trips usage). Backend parity:
full suite green minus 2 env failures; the agent run tree + gateway spans still emit through the shim.

## 6. Definition of done

`aegis.observability` importable + `aegis[observability]`/`aegis[phoenix]`-installable, tracing preserved,
`OtelObservabilitySink` satisfies the gateway Protocol, config injected, reuses `aegis.core.events.SpanKind`,
`aegis.core` minimal, backend green through the shim (minus 2 env failures).
