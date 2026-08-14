# Observability — deep dive: what it proves, what it costs, and the honest gaps

This module has fewer dramatic bugs than the others, and that is itself informative:
observability is mostly **passive**, so its failures are gaps and overclaims rather than
crashes. Which makes the gaps the interesting part.

Being able to say what your observability does *not* cover is worth more in an interview
than reciting what it does.

---

## The one that was a real bug: retries emitting a second node lifecycle

### What was happening

Five model-calling nodes in the agent graph got retry policies. The obvious way to add
them is LangGraph's own `add_node(..., retry_policy=...)`.

But the node body is already wrapped by `_timed`, which emits `node_started`, times the
body, and emits `node_finished`. LangGraph's retry re-runs **the registered callable** —
which is the wrapper.

So one logical node execution that retried once produced:

```
node_started(plan)
node_started(plan)      ← the retry
node_finished(plan, 1400ms)
```

Two starts, one finish. Every downstream consumer of that stream — the live UI, the run
summary, the latency window — now sees an extra, permanently unpaired node record with
`duration_ms: None` (`aegis/src/aegis/agent/graph.py:237`).

### Why it matters beyond cosmetics

The latency window folds `run_summary(events)["nodes"]` into its samples. An unpaired
record has no duration, so `_coerce_run` (`latency.py:184-185`) drops it — correct, and it
means a retried node's *first* attempt contributes nothing to the percentile. Meanwhile the
UI has a node that started and never finished, which reads as "still running" forever.

### The fix

Move the retry **inside** the timing wrapper. `graph.py:312`:

```python
with span(kind, f"node.{node}", attributes={...}) as node_span:
    update = await _call_with_retry(body, state, node, retry)
```

`_timed` takes `retry` as a parameter (`graph.py:270-271`) and applies it to the **body**.
The docstring at `:293-297` states the invariant: *"Passing it here rather than to
`add_node(..., retry_policy=...)` is what keeps one node execution to exactly one
`node_started`/`node_finished` pair across retries."*

**The general lesson:** if you wrap something in instrumentation and then wrap *that* in a
retry, you instrument the retries. Order of composition is a correctness property for
telemetry, not just a style choice.

### And the deliberate exception

`act` — the node that executes tools — has **no** retry policy
(`aegis/src/aegis/agent/graph.py:1159` registers it plainly). Retrying tool execution could
issue a refund twice. A retry is safe on an idempotent model call and unsafe on a side
effect, and that distinction is made per node rather than globally.

---

## Gap 1 — the `gen_ai.*` conventions are partially implemented

What Aegis stamps on a model-call span (`genai.py:44-56`, `:59-83`):

`gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.system` (deprecated alias),
`gen_ai.request.model`, `gen_ai.request.temperature`, `gen_ai.request.max_tokens`,
`gen_ai.response.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, and
the non-standard `gen_ai.usage.cost`.

What it does not:

**`gen_ai.conversation.id`.** A GenAI-aware backend cannot group the turns of one
conversation without custom parsing. Aegis has the session id — memory is scoped by it —
so this is unimplemented rather than unavailable.

**`gen_ai.agent.name` / `gen_ai.agent.id`.** The router chooses a specialist and stamps
`app.router.role` and the A2A handoff attributes, but not the `gen_ai.*` agent keys. Same
information, non-standard key, so it needs a bespoke query.

**`gen_ai.tool.name` / `gen_ai.tool.call.id`.** Tool spans carry `tool.name`
(OpenInference) plus `app.tool.risk` and `app.tool.ok` (`semconv.py:133-135`) — richer than
the convention in one way, non-conforming in another.

**Message-content events.** The conventions define optional events carrying prompts and
completions. Aegis deliberately does not emit them, and that is the right call — see the
PII section below — but it means a trace cannot show you the prompt that produced a bad
answer. You have to reproduce it.

**`error.type`.** Exceptions are recorded on the span with `record_exception` and the
status set to ERROR (`genai.py:109-112`, `spans.py:64-67`), but there is no typed
`error.type` attribute to aggregate on.

**Per-attempt spans in a fallback chain.** The gateway passes the fallback list to LiteLLM
and one span wraps the whole call (`llm.py:918-923`). A fallback that fired is detectable
via `gen_ai.response.model` differing from `gen_ai.request.model` — and is surfaced
explicitly on the AG-UI event (`stream.py:70`) — but the *failed* attempt has no span of
its own. You can see that a fallback happened; you cannot see how long the failed attempt
took or why it failed.

### Why this is a fine answer rather than an embarrassing one

The core is there: operation, model, usage, cost, latency, and errors. A GenAI-aware
backend charts token usage and latency out of the box.

The honest framing is *"we emit the request/response/usage core, not the full convention;
conversation and agent identity are present as `app.*` attributes and would need a rename
to conform."* That is a specific, bounded statement — and knowing exactly which attributes
are missing is stronger evidence of understanding than claiming full compliance.

---

## Gap 2 — there are no metrics

OTel defines three signals. Aegis exports **traces** only.

There is no `MeterProvider`, no counters, no histograms, nothing exported to a metrics
backend. `latency.py` is the closest thing, and it is explicitly not that:

- **per-process** — with four workers you have four windows and four different p95s, and
  nothing merges them;
- **volatile** — `_window` is a `deque` in RAM; a restart loses everything;
- **bounded** — `maxlen=512` runs, so it is the recent past only.

The type says so. `LatencySummary` carries `source="in_process_rolling_window"` and
`window_capacity` (`latency.py:132-133`), and the docstring at `:21-24` states it: *"the
window is per-process and resets on restart — it is not a persistent metrics store."*

### Why this is the right shape of gap

Metrics answer "what is happening overall". At agent volumes — hundreds of runs a minute,
each genuinely different because a model made choices — aggregate statistics over
non-identical requests are less useful than the individual trace of the run that
misbehaved.

What you actually lose: alerting. You cannot page on "p95 doubled" without a metrics
backend, and you cannot compute a fleet-wide percentile at all.

The upgrade path is small and worth being able to describe: add a `MeterProvider`, and
record a histogram at exactly the two points that already compute the numbers —
`record_run_latency` (`latency.py:196`) and `record_call`
(`aegis/src/aegis/gateway/llm.py:524`). The instrumentation points exist; only the export
does not.

---

## Gap 3 — run duration is summed across nodes

`latency.py:274-281`:

```python
for run in window:
    run_total = 0.0
    for name, dur in run:
        per_node_samples.setdefault(name, []).append(dur)
        run_total += dur
    run_durations.append(run_total)
```

**A run's duration is the sum of its node durations.**

For a strictly sequential graph that is very close to wall clock. For any fan-out it is
**not**: two nodes running concurrently for 500ms each contribute 1000ms to a figure that
took 500ms of wall time.

### Why it was done this way, and it is a real reason

Consistency. The comment at `:279-280`: *"identical to the `totals.duration_ms` that
`run_summary` reports, so the two never diverge."*

Two independently computed "run duration" numbers on two surfaces will eventually
disagree, and then nobody trusts either. Aegis picked one definition and used it
everywhere.

### The correct description

It is **total node time**, not wall-clock run latency. It over-states latency on
concurrent paths — which is at least the *safe* direction for a latency figure, but it is
still a different quantity.

Fixing it properly means recording the run's own start and end at the orchestrator and
propagating that through the summary, so both surfaces report the same *wall clock*. The
per-node percentiles are unaffected either way — those are real measurements of real
individual node durations.

**Say this before you are asked.** "Our run duration is summed node time, which
over-counts on fan-out; the per-node percentiles are exact" is the answer of someone who
has looked.

---

## Gap 4 — no sampling, no retention policy

Everything is exported. There is no head sampler, no tail sampler, no retention
configuration. Phoenix runs **in-process** (`otel.py:55-61`), so trace storage is bounded
by whatever Phoenix does, and a long-running process accumulates.

At agent volumes that is defensible and it is a scaling limit rather than a design.

The thing to be able to say: at high volume you want **tail** sampling — keep every error,
keep the slow tail, keep 1% of the rest — because head sampling decides before it knows
whether the trace is interesting, and therefore throws away 99% of your errors.

---

## The deliberate non-gap: PII stays off spans

No prompt bodies. No retrieved document text. No answers. `RETRIEVAL_QUERY = "input.value"`
(`semconv.py:101`) *can* carry the query text, and the graph stamps it on the retrieval
node (`graph.py:573`) — but the retrieved passages and the generated answer are not
stamped.

This is a choice, not an oversight, and it has a cost worth stating: you cannot open a bad
answer's trace and read the prompt that produced it. You have to reproduce the run.

The argument for it: a trace backend is a place data goes to be **looked at by engineers**,
usually with a much broader access-control model than the database the data came from.
Putting customer content on spans quietly re-homes PII into a system whose retention,
export and access rules nobody has reviewed for that purpose.

There is a middle ground — hashed content, or content behind a separate opt-in flag — and
knowing that the trade exists is the point.

---

## Failure modes worth being able to enumerate

**Phoenix absent.** `init_observability` catches **any** exception from the Phoenix import
or registration and falls back to a console-exporting SDK provider with a warning
(`otel.py:105-110`). Tracing still works; only the UI is gone.

**No tracer at all.** `get_tracer()` (`otel.py:113`) resolves against OTel's global no-op
provider before init. `span()` returns a non-recording span; every `set_attribute` is a
safe no-op (`spans.py:10-14`). This is what makes it acceptable to instrument
aggressively — instrumentation you must guard with `if tracing_enabled:` is
instrumentation people delete.

**The console-exporter teardown race.** `_build_fallback_provider` (`otel.py:40`) uses a
**synchronous** `SimpleSpanProcessor` rather than the batched one. The docstring at
`:41-48`: a background batch-flush thread races the interpreter's stdout at teardown,
producing a stray `ValueError: I/O operation on closed file` after a test run. Synchronous
export removes the race with no functional loss — for a dev-only exporter, that is the
right trade. **In production you want batching**, which is why the Phoenix path registers
with `batch=True` (`otel.py:101`).

**A malformed latency record.** `_coerce_run` (`latency.py:162`) skips anything with a
missing, `None`, non-numeric, NaN or infinite duration (`:184-192`) and never raises —
`record_run_latency` is *"side-effect-only telemetry"* (`:198-200`) and must never disturb
a run. The concrete case it handles: a paused `approval` node that never finished.

**An empty window.** Returns `empty=True` with `None` run percentiles and no per-node rows
(`latency.py:256-267`). Not zeros. `p95 = 0.0` reads as "very fast" on a dashboard;
`empty: true` reads as "no data yet". Same absence, opposite interpretations.

**Concurrent recording.** `_window` is guarded by a `threading.Lock` (`latency.py:158`),
and `_snapshot_window` (`:223`) copies under the lock so a summary is computed from a
consistent snapshot rather than a mutating deque.

**Span context in async tasks.** `contextvars` are copied into a task at creation. A task
created *inside* a `with span(...)` block inherits it correctly; one created *before*
snapshotted an older context and its spans attach elsewhere. That is the standard cause of
"why is my background work a separate trace", and it is worth knowing before you debug it
at 3am.

---

## What the trace lets you actually prove

Three claims that are structural, not rhetorical:

**"The image never reached the model before it was screened."** The vision pipeline is
hygiene → injection screen → PII → vision model. In the trace, the injection-screen span
either precedes the model-call span or it does not. A pipeline that called the model and
then decided cannot produce that ordering. (The corresponding load-bearing test asserts
`analyst.calls == []`.)

**"No model output reached the user before the output rail cleared it."** `stream_answer`
paces an already-guarded string, and the gateway call is non-streaming **on purpose**:
`generate → guard_output → stream`. The trace shows those three spans in that order.

**"The human gate fired because of tool risk, not model confidence."** The `gate` node's
span carries the tool's risk tier. The ML node has no edge into the gate — which the
console's served topology now reflects, because it is generated from `graph.get_graph()`
with a test that fails if the offline snapshot drifts from the real graph.

That last one was a real defect: the console **hardcoded** a 9-node DAG showing the human
gate branching off ML, which contradicted the code, and could not light 7 of the real
nodes. A diagram that disagrees with the system is worse than no diagram, because people
believe it.

---

## The invariants worth naming

1. **One enum for both surfaces.** `SpanKind` is imported from `aegis.core.events`, never
   redefined, so the AG-UI stream and the OTel trace can never disagree about what kind of
   step something was.
2. **Every attribute key is a named constant**, in one module, so a convention rename is a
   one-line change.
3. **Instrumentation is inside the retry, not outside it.** One node execution is exactly
   one `node_started`/`node_finished` pair.
4. **No tracer is a no-op, never an error.**
5. **An empty measurement reports empty, never zero.**
6. **The trace id is stamped on the ledger row and the audit row**, so cost, authority and
   behaviour are joinable.

**Next:** [`40-diagrams.md`](40-diagrams.md).
