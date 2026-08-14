# Observability — the theory

Context propagation, the span data model, percentile estimation, exporter mechanics, and
the design decisions behind each.

---

## 1. Where the tree actually comes from

A span's parent is not passed as an argument. It comes from **context propagation**.

The mechanism is an implicit, ambient "current span" that a new span reads at creation
time. In Python that is a `contextvars.ContextVar`. `start_as_current_span` does three
things: create the span with the current span as parent, set it as the new current span
for the block, and restore the previous one on exit.

Two consequences worth understanding:

**Nesting is automatic and unavoidable.** Any span opened inside a `with` block becomes a
child. You do not thread anything. A library instrumented with OTel produces spans that
nest into *your* tree without knowing you exist.

**`ContextVar` semantics govern async correctness.** A `ContextVar` is copied into a task
when the task is created and is *not* shared afterwards. So:

- `await` inside a `with` block: correct — same task, same context.
- `asyncio.create_task(...)` inside a `with` block: the task copies the current context at
  creation, so the span *is* its parent. Correct.
- A task created *before* the span opened: it snapshotted an older context. Its spans
  attach to whatever was current then, not to your span. This is the standard way traces
  come out "flat" for background work.
- `asyncio.to_thread` / a thread pool: OTel's Python context uses `contextvars`, which do
  propagate into `to_thread` (it copies the context). A raw `threading.Thread` does not.

**Distributed propagation** uses the W3C Trace Context standard: a `traceparent` HTTP
header carrying version, trace id, parent span id and flags. That is how a trace survives
a process boundary. Within one process it is pure `contextvars`.

---

## 2. The span data model

An OTel span is:

| Field | Notes |
|---|---|
| trace id | 16 bytes, shared by every span in the trace |
| span id | 8 bytes, unique per span |
| parent span id | absent for the root |
| name | should be **low cardinality** — `chat gpt-4o`, not `chat about invoice 2291` |
| start / end | nanosecond timestamps |
| kind | OTel's own: server, client, producer, consumer, internal |
| attributes | key/value; scalars and homogeneous arrays only |
| events | timestamped points *within* a span (an exception, a retry) |
| links | references to other traces (batching, fan-in) |
| status | unset / ok / error |

**Names are low cardinality; attributes are high cardinality.** That split is deliberate:
backends group and index by name, and a name containing a user id produces one group per
user, which destroys aggregation.

**Events vs child spans.** An event is a point in time with no duration. A child span has
a duration. "Exception thrown" is an event; "the retry attempt" is arguably a span. The
practical rule: if you want to know how long it took, it is a span.

---

## 3. Why `gen_ai.*` conventions matter, and how to survive their churn

The conventions are **experimental**, which in OTel terms means the key names can change
between releases. They have:

- `gen_ai.system` → `gen_ai.provider.name` (semconv v1.37.0)
- `prompt_tokens` / `completion_tokens` → `gen_ai.usage.input_tokens` /
  `gen_ai.usage.output_tokens`

Three strategies:

1. **Emit only the new keys.** Clean; breaks tooling that has not caught up.
2. **Emit only the old keys.** Works today; rots.
3. **Emit new keys plus the deprecated alias.** Both work. Costs a handful of extra bytes
   per span.

Aegis takes (3), and centralises every key as a named constant in one module. That is the
part that generalises: **a convention you spell inline at each call site cannot be
migrated.** A constant can be renamed once.

### What Aegis does not emit, and what it costs

The current GenAI conventions define more than Aegis stamps. Notably absent:

- **`gen_ai.conversation.id`** — would let a backend group the turns of one conversation.
- **`gen_ai.agent.name` / `gen_ai.agent.id`** — would identify which specialist ran.
- **`gen_ai.tool.name` / `gen_ai.tool.call.id`** — Aegis stamps `tool.name` (OpenInference)
  and its own `app.tool.risk` / `app.tool.ok`, but not the `gen_ai.*` tool attributes.
- **Message-content events** — `gen_ai.*` defines optional events carrying prompts and
  completions. Aegis deliberately does not emit them; see the PII argument below.
- **`error.type`** on failed model calls, beyond the recorded exception.
- **Token-usage on a fallback attempt** is stamped from the final response only, so an
  attempt that failed mid-chain contributes no span of its own.

The consequence is concrete: a GenAI-aware backend can chart Aegis's token usage and
latency out of the box, but cannot group by conversation or by agent without custom
parsing of the `app.*` attributes. That is a real gap, and the honest framing is *"we
emit the request/response/usage core, not the full convention"*.

---

## 4. Percentiles, and the estimator actually used

p50 and p95 over a sample. Two families:

**Exact, over retained samples.** Sort and index. Exact, memory linear in samples,
requires keeping them.

**Streaming sketches** — t-digest, HDR histogram, DDSketch. Bounded memory, mergeable
across processes, approximate with bounded relative error.

Aegis keeps a bounded rolling window and computes exactly. Reasonable at this volume; a
sketch is what you reach for at high throughput or when you need to merge across workers.

The estimator matters and is worth being precise about. Aegis uses
**linear interpolation between closest ranks** — the same method as numpy's default,
Excel's `PERCENTILE.INC`, and `statistics.quantiles(method="inclusive")`:

$$r = \frac{q}{100}(n-1), \qquad P = x_{\lfloor r \rfloor} + (r - \lfloor r \rfloor)\left(x_{\lceil r \rceil} - x_{\lfloor r \rfloor}\right)$$

over the sorted values. Deterministic and exact, so a known set has a known percentile —
which is what makes it unit-testable.

Different tools use different estimators (nearest-rank, exclusive) and will disagree on
small samples. If you compare percentiles across systems, compare the estimators first.

### Two honest caveats about a rolling window

**It is per-process and resets on restart.** It is telemetry, not a metrics store. If four
workers serve traffic, each has its own window and its own p95, and nothing merges them.

**An empty window must return an honest empty**, not zeros. `p95 = 0.0` reads as "very
fast" on a dashboard; `empty: true` reads as "no data". Same absence, opposite
interpretations.

### And a third, which is a genuine modelling choice

**Run duration is the sum of node durations.** For a strictly sequential graph, sum ≈ wall
clock. For a graph with concurrency, sum **over-counts** — two nodes running in parallel
for 500ms each contribute 1000ms to a "run duration" that took 500ms.

The reason Aegis does it this way is consistency: the same summation backs the
`totals.duration_ms` a run summary reports, so the two surfaces never disagree. The right
description is *"total node time"*, not *"wall-clock run latency"*, and conflating them
would overstate latency on any fan-out path. It is documented in the source; it is still
a caveat you should state rather than let someone find.

---

## 5. Exporters and the batching trade

A **span processor** decides how finished spans reach an exporter:

**`SimpleSpanProcessor`** — exports synchronously on span end. Every span costs its export
on the request path. No loss.

**`BatchSpanProcessor`** — queues spans and flushes on a background thread, by size or
timer. Amortised, and it drops spans if the queue overflows or the process dies before a
flush.

Production wants batching. But there is a specific reason a console/dev exporter should
*not* batch: a background flush thread races the interpreter's stdout at teardown, which
surfaces as a stray `ValueError: I/O operation on closed file` after a test run. For a
dev-only console exporter, synchronous export removes that race with no functional loss.

**Graceful degradation.** If the tracing backend is absent, the correct behaviour is a
**no-op tracer**, not a crash. OTel's API is designed for this: before a provider is
installed, `get_tracer` returns a no-op that produces non-recording spans, and every
`set_attribute` on one is a safe no-op.

That property is what makes it acceptable to instrument aggressively. Instrumentation you
must guard with `if tracing_enabled:` is instrumentation people delete.

---

## 6. Why the tracing dependency is injected into the gateway

The gateway defines an `ObservabilitySink` Protocol — `span`, `set_usage`, `trace_id` —
and the OTel implementation lives in the observability package.

Structurally: the concrete sink satisfies the Protocol **without importing the gateway**,
and the gateway calls OTel **without importing OTel**. Neither package depends on the
other; the host wires them.

Practically: `import aegis.gateway` does not pull an OTel SDK, and the whole gateway is
testable with a fake sink that records calls.

The two enums are mapped **by value** (`"chat"`, `"embeddings"`, `"transcription"`) rather
than by a shared type, precisely so neither package imports the other's enum. That is a
deliberate small duplication buying a clean dependency graph — and it means the two enums'
string values are a contract that must be kept byte-identical.

---

## 7. Deciding what to instrument

**Instrument a boundary.** Anything that crosses a process, a network, or a subsystem:
model calls, database queries, vector searches, tool executions.

**Instrument a decision.** Anything where the system chose: which specialist to route to,
whether a guardrail passed, whether the cache hit, whether the human gate fired. These
produce few spans and answer most questions.

**Do not instrument pure computation** inside a single function. It adds spans without
adding information.

The reason those are the rules: the point of a span is to let you answer a question later
without re-running. A boundary answers "was it slow / did it fail"; a decision answers "why
did it do that". Everything else is noise you pay to store.

---

## What you should now be able to explain

- Context propagation via `contextvars`, and exactly when a task's span attaches wrongly
- The span data model, and why names are low cardinality and attributes are not
- Events vs child spans, and the duration test for choosing
- Why `gen_ai.*` churn argues for centralised constants and a deprecated alias
- Which convention attributes Aegis does *not* emit, and the concrete cost
- The percentile estimator used, and why estimator choice makes tools disagree
- Why an empty window must not report zeros
- Why summed node time is not wall-clock run latency
- Simple vs batch span processors, and the specific dev-mode stdout race
- Why a no-op tracer is what makes aggressive instrumentation acceptable
- Why the gateway injects an observability sink rather than importing OTel

**Next:** [`20-in-aegis.md`](20-in-aegis.md).
