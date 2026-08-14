# Observability — interview questions and answers

The strongest thing you can do in this module is volunteer the gaps. Anyone can say "we
use OpenTelemetry". Knowing which convention attributes you do not emit, and what that
costs, is the tell.

---

### "How do you debug an agent that misbehaved?"

I open the trace.

A single turn is fifteen-odd operations — input screening, hybrid retrieval and fusion,
four tiers of memory recall, an ML prediction, planning, a risk gate, generation, output
screening — several of them model calls, several concurrent. The HTTP response was a 200,
and the answer text is the symptom, not the cause.

Logs cannot answer it. They are a flat stream: no structure saying this line happened
*inside* retrieval, no correlation under concurrency, no duration, and they only contain
what someone thought to write.

A trace is a **tree of timed spans**. Each span has a name, a duration, attributes and a
parent. Read the tree and the questions answer themselves: where the time went, whether
retrieval returned anything, what the guardrail verdict was, why the gate fired.

That is what I mean by glass box, and I would define it concretely: **if you have to add a
log line and re-run to answer a question, it was a black box.**

---

### "Where does the parent-child relationship come from?"

Context propagation, not arguments. There is an ambient "current span" in a
`contextvars.ContextVar`. Opening a span reads it as the parent, sets itself as current
for the block, and restores the previous one on exit.

That is why nesting is automatic — anything opened inside inherits — and why a library
instrumented with OTel nests into *your* tree without knowing you exist.

**The async subtlety worth knowing:** a `ContextVar` is copied into a task at creation. A
task created *inside* a span block gets the right parent. A task created *before* the span
opened snapshotted an older context, so its spans attach elsewhere — which is the standard
reason background work shows up as a separate flat trace.

Across a process boundary it is the W3C `traceparent` header.

---

### "What are semantic conventions and why do you care?"

A trace is only useful if a tool can interpret it. If I call an attribute `model_name` and
you call it `llm.model`, no dashboard charts both.

OTel's `gen_ai.*` family is the agreed vocabulary for LLM calls: operation name, provider,
request model, response model, temperature, max tokens, input and output tokens. Emit
those and a GenAI-aware backend charts your usage with no configuration.

**They are experimental and they have churned.** `gen_ai.system` became
`gen_ai.provider.name`; `prompt_tokens`/`completion_tokens` became
`usage.input_tokens`/`usage.output_tokens`. We emit the new keys **and** the deprecated
alias, so tooling on either side of the rename works.

The part that generalises: every key is a named constant in one module. A convention you
spell inline at each call site cannot be migrated; a constant can be renamed once.

We also stamp OpenInference's `openinference.span.kind` — `LLM`, `EMBEDDING`, `RETRIEVER`,
`RERANKER`, `TOOL`, `GUARDRAIL`, `AGENT`, `CHAIN`, `EVALUATOR`. OTel's own `SpanKind` is
about network topology and says nothing about what kind of *work* a span is. That one
string is what makes a UI render an agent run as a recognisable pipeline instead of grey
boxes.

---

### "What does your instrumentation not cover?"

Four things, and I would rather say them than be caught on them.

**The `gen_ai.*` conventions are partially implemented.** We emit the request, response
and usage core. We do not emit `gen_ai.conversation.id`, so a backend cannot group the
turns of one conversation without custom parsing. We do not emit `gen_ai.agent.name` — the
router's choice is there as `app.router.role`, right information, non-standard key. We
stamp `tool.name` plus our own `app.tool.risk` and `app.tool.ok` rather than the
`gen_ai.tool.*` keys. And there is no typed `error.type` to aggregate on, only the recorded
exception.

**There are no metrics.** OTel defines three signals and we export traces only. There is no
`MeterProvider`. What we have is an in-process rolling window over the last 512 completed
runs that computes real p50/p95 from real samples — which is a genuine measurement and is
explicitly not a metrics store: it is per-process, so four workers means four different
p95s, and it resets on restart. The type carries `source="in_process_rolling_window"` and
`window_capacity` so nobody mistakes it. What we actually lose is alerting — you cannot
page on "p95 doubled" without a backend.

**Run duration is the sum of node durations.** For a sequential graph that is close to wall
clock; on any fan-out it over-counts, because two nodes running concurrently for 500ms each
contribute 1000ms. It was chosen so it matches the `totals.duration_ms` the run summary
reports — two independently computed "run duration" numbers eventually disagree and then
nobody trusts either. But the honest name is **total node time**, not run latency. The
per-node percentiles are exact.

**No sampling and no retention policy.** Everything is exported to an in-process Phoenix.
Defensible at agent volumes; a scaling limit, not a design.

---

### "Given no metrics, how would you add them?"

The instrumentation points already exist; only the export does not.

Add a `MeterProvider` and record histograms at exactly the two places that already compute
the numbers: `record_run_latency`, which folds a completed run's node timings, and
`record_call` in the gateway, which already accumulates per-role tokens and cost.

The thing to be careful about is **cardinality**. Traces tolerate high-cardinality
attributes; metrics do not — each distinct label combination is a separate time series, and
an unbounded label is the classic way to take down a metrics backend. So the metric labels
would be role, model and node name — bounded — never user id, tenant id or query text.

---

### "How do you handle sampling?"

We do not sample, and I can defend both the choice and its limit.

**Head sampling** decides at the root before anything happened — "keep 1%". Cheap,
stateless, and it throws away 99% of your errors, because it cannot know a trace will fail.
**Tail sampling** buffers the whole trace and then decides — keep every error, keep the
slow tail, keep 1% of the rest — at the cost of buffering in a collector.

For agents the calculus differs from an ordinary service. Runs are rare, expensive and
**highly variable** — hundreds a minute, and each genuinely different because a model made
choices. There is little value in aggregate statistics over non-identical requests and
enormous value in the individual trace of the run that misbehaved.

At a million requests per second I would want tail sampling. At this volume, keeping
everything is the right answer.

---

### "Do you put prompts on spans?"

No, deliberately, and there is a cost.

The `gen_ai.*` conventions define optional events carrying prompt and completion content.
We do not emit them, and we do not stamp retrieved passages or the generated answer.

The reason: a trace backend is where data goes to be **looked at by engineers**, usually
with a much broader access-control model than the database the data came from. Putting
customer content on spans quietly re-homes PII into a system whose retention, export and
access rules nobody reviewed for that purpose.

**The cost is real** — you cannot open a bad answer's trace and read the prompt that
produced it, you have to reproduce the run. The middle grounds are hashed content, or
content behind a separate opt-in flag with its own retention. Knowing the trade exists is
the point.

---

### "Tell me about an observability bug."

Retries emitted a second node lifecycle.

Five model-calling nodes got retry policies, and the obvious way is LangGraph's
`add_node(..., retry_policy=...)`. But the node body is already wrapped by a `_timed`
decorator that emits `node_started`, times the body, opens the span, and emits
`node_finished`. LangGraph's retry re-runs the **registered callable** — which is the
wrapper.

So one logical execution that retried once produced two `node_started` events and one
`node_finished`. Every downstream consumer — the live UI, the run summary, the latency
window — saw a permanently unpaired node record with no duration. The UI showed a node that
started and never finished; the latency window correctly dropped it, so the first attempt
contributed nothing to the percentile.

The fix moves the retry **inside** the timing wrapper, so one node execution is exactly one
`node_started`/`node_finished` pair across retries.

**The general lesson:** if you wrap something in instrumentation and then wrap that in a
retry, you instrument the retries. Composition order is a correctness property for
telemetry, not a style choice.

And a deliberate exception worth mentioning: the tool-execution node has **no** retry at
all. Retrying a model call is safe; retrying tool execution could issue a refund twice.
That distinction is made per node, not globally.

---

### "What happens if the tracing backend isn't there?"

Nothing breaks, at three levels.

If Phoenix is disabled, we install a console-exporting SDK provider. If Phoenix or its OTel
bridge cannot be imported or fails to register, **any** exception falls back to the same
console provider with a warning.

And if `init_observability` never ran at all — tests, offline mode, import ordering —
`get_tracer` resolves against OTel's **global no-op provider**. `span()` returns a
non-recording span and every `set_attribute` on it is a safe no-op. No network, no error.

**That property is what makes it acceptable to instrument aggressively.** Instrumentation
you have to guard with `if tracing_enabled:` is instrumentation people delete.

One detail I like: the console fallback uses a **synchronous** span processor rather than
the batched one. A background batch-flush thread races the interpreter's stdout at
teardown, producing a stray "I/O operation on closed file" after a test run. For a dev-only
exporter, synchronous export removes the race with no functional loss. The Phoenix path
registers with `batch=True`, because production wants amortised export.

---

### "How does the gateway emit spans without depending on OpenTelemetry?"

The gateway defines an `ObservabilitySink` Protocol — `span`, `set_usage`, `trace_id` — and
the concrete OTel implementation lives in the observability package. Structural typing, so
the sink satisfies the Protocol **without importing the gateway**, and the gateway calls
OTel **without importing OTel**. The host wires them.

Two payoffs: `import aegis.gateway` does not pull an OTel SDK, and the whole gateway is
testable with a fake sink that just records calls.

One detail that shows the seam is real: the two packages each have their own
`GenAIOperation` enum, and the sink maps them **by value** — `"chat"`, `"embeddings"`,
`"transcription"` — rather than by a shared type, precisely so neither imports the other's
enum. A deliberate small duplication buying a clean dependency graph, and it makes those
string values a contract that must stay byte-identical.

---

### "What can a trace actually prove?"

Three things that are structural arguments rather than assertions.

**"The image was screened before the model saw it."** The vision pipeline is hygiene →
injection screen → PII → model. In the trace, the screen span either precedes the
model-call span or it does not. A pipeline that calls the model and then decides cannot
produce that ordering.

**"No model output reached the user unguarded."** Generation is non-streaming *on purpose*
— `generate → guard_output → stream`, and the stream node paces an already-guarded string.
The trace shows those three spans in that order.

**"The human gate fired on tool risk, not model confidence."** The gate span carries the
tool's risk tier, and there is no edge from the ML node into the gate.

That last one is worth a caveat, because it was a live defect: the **console** hardcoded a
9-node DAG drawing the human gate branching off ML — contradicting the code — and could not
light 7 of the real nodes. The topology is now served from the real compiled graph, with a
test that fails if the offline snapshot drifts. A diagram that disagrees with the system is
worse than no diagram, because people believe it.

---

### "How does observability connect to the rest of the platform?"

The trace id is stamped on the usage-ledger row and on the audit-log row, both indexed, and
on every event in the live stream.

So "who authorised this refund", "what did it cost", and "what did the system actually do"
are one join. Without a shared id those are separate accounts of the same event that can
never be reconciled — and reconciling them is exactly what an auditor asks for.

---

### "What would you improve first?"

Three things, in order of value per unit of work.

**Wall-clock run duration.** Record the run's own start and end at the orchestrator and
propagate it through the summary, so both surfaces report the same number and it is
actually latency. Small change, removes an overclaim.

**The missing convention attributes.** `gen_ai.conversation.id` and the agent keys are
information we already have under `app.*` names — it is a rename, and it unlocks
out-of-the-box grouping in any GenAI-aware backend.

**A metrics signal.** Two histograms at points that already compute the numbers, with
bounded labels. That is what turns observability from "I can investigate an incident" into
"I get paged before a customer notices."
