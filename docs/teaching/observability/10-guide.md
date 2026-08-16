# Observability

The part of Aegis that lets you answer questions about a run that already finished.

---

## 1. What it is

A user says: *"I asked it to refund my order and it told me to contact support instead."*

You go to the logs. Here is the run:

```
14:02:03 INFO  POST /agent/run  session=7c1f  status=200  duration=40.2s
```

Forty seconds, and a 200. That is the whole record. Inside those forty seconds about twelve
graph nodes ran — a guardrail, a router, retrieval, a planner, a tool call, a generator.
Several were model calls. Which one produced the wrong behaviour? The 200 does not know. And
"40 seconds" does not say whether that was one hung model call or twelve slow ones.

Logging harder helps a little and then stops. Log lines have no structure, so `retrieved 6
sources` does not know it happened inside retrieval, which happened inside the run. They have
no duration — you need two lines and subtraction. They interleave with twenty other runs. And
they contain what someone thought to write, which is reliably not the thing you need at 3am.
Turning on DEBUG gives you more lines with the same holes.

Record each operation instead as a **timed, named unit with a parent**, and the same run
looks like this:

```
agent.run                              40.2s
├── node.guard_input                     0.1s
├── node.retrieve                        3.4s
│   ├── embeddings text-embedding-3-large  0.1s
│   └── chat gpt-4o-mini                   0.9s
├── node.plan                           35.6s   ←
│   └── chat gpt-4o                     35.5s   ←
├── node.act                             0.3s
│   └── tool.lookup_order                0.3s
└── node.generate                        0.6s
```

The forty seconds is one model call inside `plan`. You did not work that out. You read it.

Now the two words. A **span** is one of those units: a name, a start and end, some key/value
attributes, a status, and a parent. A **trace** is the whole tree — every span sharing one
trace id, arranged by parentage. The parent field is the load-bearing one. Without it you
have a list; with it you have a tree, and the tree is what answers questions.

---

## 2. How it works in Aegis

### Where the parent comes from

Nobody passes a parent span around as an argument. There is an ambient "current span" stored
in a `contextvars.ContextVar`, and opening a span does three things: create it with whatever
is currently ambient as its parent, make itself ambient for the block, and restore the
previous one on exit.

That is why nesting is free. Any span opened anywhere inside that block becomes a child —
including inside a library that has never heard of Aegis.

It is also why one async case bites. A `ContextVar` is copied into a task when the task is
*created*. Work started before the span opened snapshotted an older context, so its spans
attach somewhere else and show up as a separate flat trace. `asyncio.create_task` and
`asyncio.to_thread` inside the block are fine; a raw `threading.Thread` is not.

### How a node gets its span

Every graph node is wrapped by one decorator, `_timed`, in `aegis/src/aegis/agent/graph.py`.
It emits a `node_started` event, opens a span around the body, times it with a monotonic
clock, and emits `node_finished` with the duration.

The span is current while the body runs, which is the whole reason the tree above exists. The
model calls, the tool span, the retrieval attributes — none of them are handed a parent. They
open inside the body, so they land underneath it.

The root of the tree, `agent.run`, is opened by the orchestrator, not the graph. Each node is
given its kind once at wiring time: the guardrail nodes are `GUARDRAIL`, `retrieve` is
`RETRIEVER`, everything else defaults to `CHAIN`. Tool execution inside `act` opens its own
`TOOL` span per call, carrying the tool name and its risk level.

Two rules came out of getting this wrong and are worth stating flat:

> **The retry goes inside the wrapper, not around it.** Wrap a node in instrumentation and
> then wrap that in a retry, and a retried node emits two starts and one finish. The UI shows
> it running forever, and the unpaired record is dropped from the latency percentiles —
> silently excluding exactly the executions that had trouble.

> **`act` is instrumented but never retried.** A timeout on "issue refund" does not mean the
> refund did not happen. It means you did not hear back. Retrying a model call is safe because
> it is idempotent; retrying a side effect is not.

Three nodes are wired without the wrapper at all: `recall_memory` and `persist_memory`, which
are silent when memory is off, and `approval`, which re-runs from its first line when a
paused run resumes and would otherwise emit its start twice.

### Naming attributes so a tool can read them

A span's attributes are only useful if the dashboard reading them expects the same key names.
OpenTelemetry publishes agreed names for model calls, the `gen_ai.*` family, and Aegis stamps
this subset:

| Attribute | What it holds |
|---|---|
| `gen_ai.operation.name` | `chat`, `embeddings`, `text_completion`, `transcription` |
| `gen_ai.provider.name` | The provider |
| `gen_ai.request.model` / `.response.model` | Model asked for, model that answered |
| `gen_ai.usage.input_tokens` / `.output_tokens` | Token counts |
| `gen_ai.usage.cost` | USD cost. **Not a standard key** — the conventions define none |

Emit those and a GenAI-aware backend charts token use and latency with no configuration. The
cost key is flagged as ours in the source, which is the right way to carry an extension.

These conventions still move — `gen_ai.system` became `gen_ai.provider.name`, and the token
keys were renamed too. Aegis emits both spellings so old and new tools both work. The part
that generalises is not which option you pick: it is that **every key is a named constant in
one module**, `semconv.py`, so the next rename is a one-line change instead of a grep.

There is a second, complementary convention. OTel's own span kind is about network topology
— server, client, internal — so a guardrail check, a vector search and a refund are all
`internal`. **OpenInference** adds the missing axis, `openinference.span.kind`, from a
nine-value enum: `LLM`, `EMBEDDING`, `RETRIEVER`, `RERANKER`, `TOOL`, `GUARDRAIL`, `AGENT`,
`CHAIN`, `EVALUATOR`. That one string is the difference between a trace UI drawing your run
as a recognisable pipeline and drawing fifteen identical grey boxes.

That enum is imported from `aegis.core.events`, not redefined here. It is the same enum that
stamps every event in the live UI stream, so the console and the trace can never disagree
about what kind of step something was.

### The gateway does not import OpenTelemetry

Every model call goes through one gateway, and that gateway knows nothing about tracing. It
declares a Protocol with three methods — `span`, `set_usage`, `trace_id` — and calls whatever
is wired in. `OtelObservabilitySink` in this package satisfies that shape without either
package importing the other. The host connects them in one line.

Two payoffs: `import aegis.gateway` never drags in an OTel SDK, and the gateway is testable
with a fake sink that records calls into a list.

### You can instrument everywhere

Instrumentation that can break the thing it observes gets deleted. So the question is what a
tracing call does when no tracer is configured. `get_tracer()` resolves against OTel's global
provider, which is a **no-op provider** until something sets one. A span from a no-op tracer
is non-recording, and every attribute write on it is a safe no-op — no network, no exception,
no config needed.

That is why every node can open a span with no `if tracing_enabled:` around it. Instrumentation
you have to guard is instrumentation people delete.

At startup, `init_observability` registers a real provider. With Phoenix enabled it launches a
local in-process Phoenix UI and exports in batches. If anything at all goes wrong — missing
package, port clash, version mismatch — it falls back to a console exporter. You lose the UI,
not the spans.

### Percentiles, and what they are not

Traces answer "what happened in this run". They do not answer "is this run typical". So when
a run finishes, its per-node timings are folded into an in-process rolling window of the last
512 runs, in `latency.py` — pure standard library, no OTel.

Three honest limits, all stated by the code about itself. It is **per-process**: run four
workers and you have four windows and four different p95s, and a restart clears them. An
**empty window reports empty**, not zero, because a tile reading `p95: 0.0ms` reads as "we
are extremely fast" when the truth was "nothing has been recorded". And a run's duration is
the **sum of its node durations**, which is close to wall clock for a sequential graph and
overstates it when nodes run in parallel. The per-node percentiles are exact; the run total
is better called total node time.

### The trace id is the join key

A trace on its own tells you what the system did. It becomes evidence when you can join it to
what the action cost and who authorised it. The trace id is stamped on the usage ledger row,
on the audit log row, and on every event in the live stream. So "what did it do", "what did
it cost" and "who approved it" are one lookup on one id.

### What is not here

Aegis exports **traces only** — no metrics, no counters, no histograms. What that costs is
alerting: you cannot page on "p95 doubled" without a metrics backend.

There is **no sampling**. Everything is kept, which is defensible for an agent, where runs are
rare and all different, and would not be at a million requests a second.

**Customer content stays off spans** — no prompts, no retrieved passages, no answers. A trace
backend is read by engineers under looser access rules than the database the data came from.
The cost is real: you cannot open a bad answer's trace and read the prompt that caused it.

---

## 3. How you use it in code

```python
from aegis.observability import (
    init_observability, get_tracer, current_trace_id,
    span, set_span_attribute, set_span_attributes,
    genai_span, set_usage, GenAIOperation, SpanKind, semconv,
    OtelObservabilitySink,
    record_run_latency, latency_summary, reset_latency_window,
)

init_observability(phoenix_enabled=True, service_name="taif", project_name="taif")
```

Call `init_observability` once at startup. Everything below works whether or not you did.

An ordinary unit of work:

```python
with span(SpanKind.RETRIEVER, "retrieve", attributes={"app.query.length": len(q)}) as s:
    hits = await search(q)
    set_span_attribute("app.retrieval.count", len(hits), span=s)
```

A model call:

```python
async with genai_span(GenAIOperation.chat, "gpt-4o", temperature=0.2) as s:
    reply = await client.complete(messages)
    set_usage(s, input_tokens=812, output_tokens=140,
              cost_usd=0.0041, response_model=reply.model)
```

Latency, after a run finishes:

```python
record_run_latency(run_summary(events)["nodes"])   # folds one run into the window
summary = latency_summary()                        # percentiles from the window
summary = latency_summary(runs)                    # or a pure computation over given runs
```

`latency_summary()` returns a `LatencySummary` with `empty`, `source`, `window_capacity`, run
percentiles and a `NodeLatency` row per node. `reset_latency_window()` clears it, for tests.

### Arguments worth changing

| Argument | Default | What it does |
|---|---|---|
| `phoenix_enabled` | `True` | Launch the local Phoenix UI. `False` exports to the console |
| `service_name` | `"taif"` | The service name on every span |
| `project_name` | `"taif"` | The Phoenix project traces land in |

Config is injected, not read from a settings module — that is what lets this package be
extracted from any host. The backend passes them in from its own settings during startup.

---

## 4. Why it helps us

**A finished run is answerable.** You can say which node was slow, which model was called,
what the guardrail decided, and whether a tool ran — without adding a print statement and
running it again.

**One node execution is one honest record.** Started and finished always pair, the duration
covers every retry attempt, and a run that paused for a human contributes nothing rather than
a fake zero.

**The trace, the console and the audit trail agree.** One span-kind enum, one attribute-key
module, one trace id joining behaviour to cost to authority.

**Instrumenting is free when tracing is off.** Which is why it is everywhere, including in
unit tests, where it costs nothing.

Without it, a forty-second run is a single log line and a 200, and every question about it
has to be answered by reproducing it.

**Next:** [`40-diagrams.md`](40-diagrams.md)
