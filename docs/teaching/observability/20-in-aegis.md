# Observability — our exact implementation

The package is `aegis/src/aegis/observability/`:

| File | Lines | What it owns |
|---|---|---|
| `__init__.py` | 64 | The public surface |
| `otel.py` | 129 | Tracer provider, Phoenix wiring, `current_trace_id` |
| `semconv.py` | 148 | Every attribute key as a named constant |
| `genai.py` | 148 | The `gen_ai.*` span helpers |
| `spans.py` | 90 | The generic non-LLM span helper |
| `sink.py` | 78 | `OtelObservabilitySink` — the gateway's Protocol, implemented |
| `latency.py` | 312 | The in-process rolling latency window |

---

## How you import it

```python
from aegis.observability import init_observability, span, SpanKind, get_tracer

init_observability(phoenix_enabled=True, service_name="taif", project_name="taif")

with span(SpanKind.RETRIEVER, "retrieve", attributes={...}) as s:
    ...
```

Public surface at `__init__.py:45-64`: `init_observability`, `get_tracer`,
`current_trace_id`, `span`, `set_span_attribute`, `set_span_attributes`, `genai_span`,
`genai_span_sync`, `set_usage`, `GenAIOperation`, `SpanKind`, `OtelObservabilitySink`,
`semconv`, plus the latency surface `latency_summary`, `record_run_latency`,
`reset_latency_window`, `LatencySummary`, `NodeLatency`.

**Dependencies.** `opentelemetry-api`/`-sdk` are required (`aegis[observability]`). Arize
Phoenix is **optional** and imported lazily, only inside `init_observability` when
`phoenix_enabled` is true (`__init__.py:23-26`). Without it, tracing still works via a
console exporter.

`latency.py` is **pure stdlib** — no OTel, no Phoenix (`latency.py:26`), so importing it
stays leaf-clean.

---

## Tracer setup — `otel.py`

`init_observability(*, phoenix_enabled=True, service_name="taif", project_name="taif",
app=None)` at `otel.py:64`.

**Config is injected** — those are call-time arguments, not reads of a host settings
module. The docstring at `otel.py:9-11` calls that out as *"the one coupling this module
had to a specific host application before extraction."*

The flow:

1. `phoenix_enabled=False` → `_build_fallback_provider` (`:90-93`).
2. Otherwise import `phoenix.otel.register`, launch a **local, in-process** Phoenix if one
   is not already running (`_launch_phoenix`, `:55`), and register the provider with
   `batch=True` (`:96-104`).
3. **Any** exception → fall back to the console provider with a warning (`:105-110`).

`_build_fallback_provider` (`otel.py:40`) uses a **synchronous** `SimpleSpanProcessor`,
and the docstring at `:41-48` gives the reason: the console exporter is a dev/offline
fallback, and a background batch-flush thread races the interpreter's stdout at teardown —
a stray `ValueError: I/O operation on closed file` after a test run. Synchronous export
removes the race with no functional loss.

`get_tracer()` (`otel.py:113`) works **whether or not init has run**: before init it
resolves against the OTel global provider, which is a no-op until one is set. That is what
makes instrumenting aggressively safe.

`current_trace_id()` (`otel.py:124`) returns the active trace id as 32-char hex, or `None`
if the span context is invalid.

---

## The constants — `semconv.py`

Every attribute key is a named constant in one file, so a convention rename is a one-line
change rather than a codebase-wide grep.

**GenAI request/response** (`semconv.py:69-75`):

```python
GEN_AI_OPERATION_NAME     = "gen_ai.operation.name"
GEN_AI_PROVIDER_NAME      = "gen_ai.provider.name"
GEN_AI_SYSTEM             = "gen_ai.system"   # deprecated alias; still emitted
GEN_AI_REQUEST_MODEL      = "gen_ai.request.model"
GEN_AI_REQUEST_TEMPERATURE= "gen_ai.request.temperature"
GEN_AI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
GEN_AI_RESPONSE_MODEL     = "gen_ai.response.model"
```

**Usage** (`:78-80`): `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, and
`gen_ai.usage.cost` — explicitly flagged in the source as a **non-standard extension**,
because the conventions define no cost attribute.

The module docstring (`semconv.py:7-11`) names both renames and states the strategy: emit
the new keys **and** the deprecated `gen_ai.system` alias for tooling still reading it.

**`DEFAULT_PROVIDER = "tcs.genailab"`** (`:84`) — the real gateway, *"not a fabricated
Azure provider."*

**OpenInference** (`:92`): `OPENINFERENCE_SPAN_KIND = "openinference.span.kind"`. Set as a
plain string attribute — the comment at `:88-91` notes the `openinference-*`
instrumentation packages are deliberately **not** a dependency.

**Application attributes** (`:97-135`), namespaced `app.*` except where an OpenInference-
compatible key exists:

- graph: `app.graph.node`, `app.graph.node.label`, `app.graph.node.duration_ms`
- retrieval: `input.value` (OpenInference, the query text), `app.retrieval.result_count`,
  `.candidate_count`, `.cache_hit`, `.rounds`, `.rewritten`
- rerank: `app.rerank.input_count`, `.output_count`
- router: `app.router.role`, `.reason`, `.used_llm`
- **A2A handoff**: `app.a2a.from`, `.to`, `.reason`, `.protocol` (`:116-119`) — emitted as
  a dedicated span so the tree reads as an explicit agent-to-agent handoff rather than a
  routing attribute on a node
- answer cache: `app.answer_cache.hit`, `.similarity`
- guardrails: `app.guardrail.stage`, `.verdict`, `.layer`
- tools: `tool.name` (OpenInference), `app.tool.risk`, `app.tool.ok`

**`GenAIOperation`** (`semconv.py:138`): `CHAT`, `EMBEDDINGS`, `TEXT_COMPLETION`, and
`TRANSCRIPTION`. The comment at `:144-148` is worth reading: the GenAI conventions have no
audio operation yet, so `"transcription"` is Aegis's own stable value, *"matched
byte-for-byte by `aegis.gateway.llm.GenAIOperation.TRANSCRIPTION` so the sink maps the two
enums by value with no cross-package import."*

**`SpanKind` is not redefined here.** It is imported from `aegis.core.events`
(`semconv.py:23`), which carries all nine kinds — `LLM`, `EMBEDDING`, `RETRIEVER`,
`RERANKER`, `TOOL`, `GUARDRAIL`, `AGENT`, `CHAIN`, `EVALUATOR`
(`aegis/src/aegis/core/events.py:17-28`). One enum drives **both** the live AG-UI event
stream and the exported OTel spans, so the stream and the trace can never disagree about
what kind of step something was.

---

## The GenAI spans — `genai.py`

`_OPERATION_SPAN_KIND` (`genai.py:25-32`) maps a GenAI operation onto an OpenInference
span kind: `CHAT`/`TEXT_COMPLETION` → `LLM`, `EMBEDDINGS` → `EMBEDDING`, and
`TRANSCRIPTION` → `LLM` with an explicit comment that Phoenix has no audio kind and a
transcription is a model call.

`_stamp_request(span, ...)` (`genai.py:35`) sets the span kind, operation name, provider
(**both** `gen_ai.provider.name` and the deprecated `gen_ai.system` at `:50-51`), the
request model, and — only when supplied — temperature and max tokens.

`set_usage(span, *, input_tokens, output_tokens, cost_usd, response_model)`
(`genai.py:59`) stamps each only when not `None`.

`genai_span_sync(...)` (`genai.py:86`) opens `tracer.start_as_current_span(f"{operation}
{model}")` — the GenAI naming convention — stamps the request attributes, and on exception
calls `record_exception` and `set_status(ERROR)` **and re-raises** (`:109-112`).
`genai_span(...)` (`genai.py:115`) is the async wrapper over it.

---

## The generic span — `spans.py`

`span(kind, name, *, attributes=None)` (`spans.py:34`) is the counterpart for the
**non-LLM** units of a run: retrieval, reranking, guardrails, per-node work, tool calls.

It opens `start_as_current_span(name)`, stamps `openinference.span.kind`, applies
attributes, and records-and-re-raises exceptions (`:57-67`).

**Everything degrades to a no-op.** The module docstring (`spans.py:10-14`): before
`init_observability` runs — or in offline/lite mode and in tests — `get_tracer` resolves
against OTel's global no-op provider, so `span` returns a non-recording span and never
touches the network. `set_span_attribute(s)` on a non-recording span is a safe no-op too.

`set_span_attribute(key, value, *, span=None)` (`spans.py:70`) skips `None` values and
defaults to the current span. `set_span_attributes` (`:83`) is the bulk form.

---

## The gateway seam — `sink.py`

`OtelObservabilitySink` (`sink.py:28`) structurally satisfies
`aegis.gateway.llm.ObservabilitySink` — a `@runtime_checkable` Protocol — with **no
inheritance and no import of the gateway package** (`:31-34`).

Three methods:

- `span(operation, model, *, temperature, max_tokens)` (`:36`) → `genai_span(...)`. The
  gateway's operation enum is mapped by **value**: `GenAIOperation(operation.value)`
  (`:52`), which works because the two enums' string values are identical by contract.
- `set_usage(...)` (`:58`) → this package's `set_usage`.
- `trace_id()` (`:76`) → `current_trace_id()`.

The host wires it in one line — `sink.py:10-13` shows the usage, and the real call is
`backend/src/app/core/llm.py:219`:

```python
gateway.configure(..., observability=OtelObservabilitySink())
```

*"with no bespoke adapter of its own."*

---

## The latency window — `latency.py`

This is **not** a metrics backend, and the module is careful to say so.

`percentile(values, q)` (`latency.py:58`) — linear interpolation between closest ranks,
matching numpy's default / `PERCENTILE.INC` / `statistics.quantiles(method="inclusive")`.
Raises on an empty sequence rather than returning a fake number (`:77-78`).

`_window` (`latency.py:159`) is a `deque(maxlen=512)` — `DEFAULT_WINDOW_CAPACITY` at `:55`
— guarded by a `threading.Lock` (`:158`) because a host may record from concurrent
request tasks.

`record_run_latency(nodes)` (`latency.py:196`) folds one completed run's node timings in.
It is **side-effect-only telemetry**: it never raises on malformed input and a run with no
timed nodes records nothing. `_coerce_run` (`:162`) accepts the shape `run_summary` emits
(dicts with `node`/`duration_ms`), attribute-bearing models, or raw pairs, and **skips**
entries with a missing/`None`/non-numeric/NaN/inf duration (`:184-192`) — a paused
`approval` node that never finished contributes nothing rather than a zero.

`latency_summary(runs=None)` (`latency.py:229`) computes from the window, or from
caller-supplied runs for a pure deterministic computation. It returns per-node p50/p95/max
counts and run-level percentiles.

**The honesty properties, in the type itself.** `LatencySummary` (`latency.py:115`)
carries `source` and `window_capacity` — `_SOURCE_WINDOW = "in_process_rolling_window"`
(`:50`) vs `_SOURCE_SUPPLIED = "supplied_runs"` (`:52`) — and an `empty` flag. An empty
window returns `empty=True` with `None` run percentiles and no per-node rows
(`:256-267`), *"never fabricated zeros."*

**The caveat that is a real gap.** `latency.py:279-281`:

```python
# A run's duration is the sum of its node durations — identical to the
# ``totals.duration_ms`` that ``run_summary`` reports, so the two never diverge.
run_durations.append(run_total)
```

Summed node time, not wall clock. On any fan-out path the two differ. It is consistent
with the other surface by construction, which is why it was chosen — and it is still
"total node time", not "run latency".

`slowest_node` (`latency.py:297-300`) is the highest p95, tie-broken by max then total —
tail latency, not mean.

---

## Where the spans actually come from

**Every graph node.** `aegis/src/aegis/agent/graph.py:270` — `_timed(node, label, kind,
*, retry)` wraps a node body to emit `node_started`/`node_finished` **and** open one span
of the given OpenInference kind (`graph.py:283-287`).

The wrapper body at `graph.py:299-334`:

```python
writer(events.node_started(node, label))
start = time.perf_counter()
with span(kind, f"node.{node}", attributes={
        semconv.GRAPH_NODE: node,
        semconv.GRAPH_NODE_LABEL: label}) as node_span:
    update = await _call_with_retry(body, state, node, retry)
    node_span.set_attribute(semconv.GRAPH_NODE_DURATION_MS, ...)
duration_ms = int(round((time.perf_counter() - start) * 1000))
```

Three things to notice:

1. **The span is current while the body runs**, so retrieval, rerank, guardrail, tool and
   LLM spans opened inside nest beneath it and the trace reads as a tree
   (`graph.py:281-284`).
2. **The retry lives inside the timing wrapper** (`_call_with_retry` at `:312`). That is
   deliberate: passing the retry to LangGraph's `add_node(retry_policy=...)` would re-run
   the whole wrapper, emitting a second `node_started`/`node_finished` pair for one
   logical node execution (`graph.py:294-297`, and `:237` documents the stray unpaired
   record it would produce).
3. **Timing is `time.perf_counter()`** — a monotonic clock, immune to wall-clock
   adjustment.

Node kinds are assigned at wiring time (`graph.py:1123-1169`): `guard_input` and
`guard_output` are `GUARDRAIL`, `retrieve` is `RETRIEVER`, tool execution is `TOOL`
(`:896`), the run root is `AGENT` (`:415`), everything else defaults to `CHAIN`.

**Memory nodes are wired plain** (`graph.py:1139-1141`, and `:643`, `:688`) — deliberately
*not* through `_timed`, so they emit nothing at all when memory is inactive rather than
an empty node record.

**Every model call.** Through the gateway's injected sink — see
[`gateway/20-in-aegis.md`](../gateway/20-in-aegis.md#complete--the-chat-path-llmpy835).

---

## The backend shim

`backend/src/app/observability/__init__.py` is a **strangler shim** delegating to
`aegis.observability`, injecting `phoenix_enabled` from `app.config.get_settings()` — *"the
only host coupling the extraction had to sever."*

`init_observability(app)` is called first thing in the FastAPI lifespan
(`backend/src/app/main.py:146`), before anything else.

---

## The joins to the rest of the platform

The trace id is the correlation key across three otherwise-unrelated stores:

- **The usage ledger.** `aegis/src/aegis/governance/models.py:195` — `UsageLedger.trace_id`,
  indexed. Written by the gateway from `_observability.trace_id()`
  (`aegis/src/aegis/gateway/llm.py:804`).
- **The audit log.** `aegis/src/aegis/governance/models.py:218` — `AuditLog.trace_id`,
  indexed.
- **The live event stream.** `aegis/src/aegis/core/events.py:37` — every event carries an
  optional `trace_id`.

So "who authorised this refund", "what did it cost", and "what did the system actually do"
are all joinable on one id.

**Next:** [`30-deep-dive.md`](30-deep-dive.md) — including the honest gaps.
