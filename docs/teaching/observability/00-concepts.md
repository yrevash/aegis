# Observability — the concept, from zero

No code. What a span is, why logs are not enough for an agent, and what "glass box"
actually has to mean.

---

## The problem

A user says: *"I asked it for a refund and it told me to contact support instead."*

Now find out why. A single agent turn did something like this:

1. Screened the input for prompt injection.
2. Retrieved from a vector store, a graph, and a keyword index, then fused and reranked.
3. Recalled four tiers of memory and assembled a working-memory block within a budget.
4. Made an ML prediction with a conformal interval.
5. Planned, and chose a tool.
6. Checked the tool's risk tier against the human-approval gate.
7. Generated an answer.
8. Screened the output.

That is fifteen-odd operations, several of them model calls, several of them concurrent,
each with inputs and outputs, each capable of quietly returning nothing.

**Which one produced the wrong behaviour?**

You cannot answer that from an HTTP status code. The request was a 200. You cannot answer
it from the answer text — that is the symptom. You need to see *what happened inside*.

---

## Why logs are not enough

Logs are a **flat stream of lines**. That works fine for a request/response service, and
breaks for an agent, in four specific ways:

**No structure.** A log line does not know it happened *inside* retrieval, which happened
*inside* the run. You reconstruct the tree by reading timestamps and hoping.

**No correlation under concurrency.** Twenty concurrent runs interleave their lines. You
grep for a request id — if someone remembered to put one on every line.

**No duration.** A line has a timestamp, not a duration. "Retrieval took 800 ms" requires
two lines and subtraction, and only if both were written.

**They record what someone thought to write.** Which is, reliably, not the thing you need
at 3am.

The fix is not more logs. It is a different data model.

---

## Traces and spans

A **span** is one timed operation. It has:

- a **name** (`retrieve`, `chat gpt-4o`);
- a **start and end**, hence a duration;
- **attributes** — arbitrary key/value pairs (which model, how many results, what
  verdict);
- a **status** — OK, or an error with the exception recorded;
- a **parent** — the span it happened inside.

That last field is what turns a stream into a **tree**. A **trace** is one such tree: all
the spans sharing a trace id, arranged by parentage.

```
run  (2.4s)
├── guard_input  (120ms)
├── retrieve  (890ms)
│   ├── embeddings text-embedding-3-large  (95ms)
│   ├── vector search  (140ms)
│   ├── graph search  (410ms)      ← this is your latency
│   └── chat gpt-4o-mini  (240ms)  ← the reranker
├── ml_predict  (35ms)
├── plan  (610ms)
│   └── chat gpt-4o  (600ms)
└── generate  (740ms)
    └── chat gpt-4o  (730ms)
```

Read that tree and the questions answer themselves. Where did the time go? Graph search,
then the two big model calls. Did retrieval run at all? Yes, and it returned. Did a
guardrail fire? Look at its span's verdict attribute.

**This is the difference between a black box and a glass box**, and the definition is
concrete rather than aspirational: a glass box is one where every decision the system made
is visible after the fact, without adding instrumentation. If you have to add a log line
and re-run to answer a question, it was a black box.

---

## OpenTelemetry: why a standard matters

You could invent your own span format. **OpenTelemetry (OTel)** is the vendor-neutral
standard, and adopting it buys three things:

**Portability.** The same instrumentation exports to Jaeger, Honeycomb, Datadog, Phoenix,
or a file. You are not writing to a vendor.

**Composition.** Libraries instrumented with OTel — HTTP clients, database drivers —
produce spans that nest into *your* tree automatically, because context propagates.

**Shared vocabulary.** Which is the interesting part for LLM systems.

### Semantic conventions

A trace is only useful if a tool can interpret it. If you name your attribute
`model_name` and I name mine `llm.model`, no dashboard can chart both.

**Semantic conventions** are the agreed key names. For LLMs there is a `gen_ai.*` family:

| Attribute | Meaning |
|---|---|
| `gen_ai.operation.name` | `chat`, `embeddings`, … |
| `gen_ai.provider.name` | which provider |
| `gen_ai.request.model` | the model asked for |
| `gen_ai.response.model` | the model that answered |
| `gen_ai.request.temperature` | sampling temperature |
| `gen_ai.request.max_tokens` | output cap |
| `gen_ai.usage.input_tokens` | prompt tokens |
| `gen_ai.usage.output_tokens` | completion tokens |

Emit those and any GenAI-aware tool charts your token usage with no configuration.

The conventions are still **experimental**, and they have churned: `gen_ai.system` was
renamed to `gen_ai.provider.name`, and `prompt_tokens`/`completion_tokens` became
`usage.input_tokens`/`usage.output_tokens`. The pragmatic answer is to emit the new keys
**and** the deprecated alias, so tooling on either side of the rename works.

### Span kinds

OTel's own `SpanKind` is about network topology — client, server, producer, consumer. It
says nothing about *what kind of work* a span represents.

**OpenInference** is a complementary convention for AI systems, and it adds exactly that:
a `openinference.span.kind` attribute with values like `LLM`, `EMBEDDING`, `RETRIEVER`,
`RERANKER`, `TOOL`, `GUARDRAIL`, `AGENT`, `CHAIN`, `EVALUATOR`.

That one string is what lets a UI render an agent run as a **recognisable pipeline** —
retrieval nodes, model calls, tool calls — instead of a uniform list of grey boxes.

---

## Sampling, and why an agent is different

At scale you cannot store every trace. **Sampling** decides which to keep.

**Head sampling** decides at the root, before anything has happened — "keep 1%". Cheap and
stateless. It also throws away 99% of your errors, because it cannot know a trace will
fail.

**Tail sampling** buffers a whole trace, then decides — "keep everything that errored,
everything slower than 2s, and 1% of the rest". Far better selection, at the cost of
buffering in a collector.

For agent systems the calculus differs from ordinary services. An agent run is **rare,
expensive, and highly variable**: hundreds per minute, not hundreds of thousands per
second, and each one is genuinely different because a model made choices. There is
comparatively little value in aggregate statistics over identical requests and enormous
value in the individual trace of the run that misbehaved.

So: sample lightly, and keep every error. Aegis stores everything, which is defensible at
this volume and would not be at a million requests per second.

---

## Traces, metrics, logs — three signals, three jobs

OTel defines three, and mixing them up is a common confusion:

| Signal | Answers | Shape |
|---|---|---|
| **Traces** | "What happened in *this* run?" | A tree, per request |
| **Metrics** | "What is happening *overall*?" | Aggregated numbers over time |
| **Logs** | "What did the code want to tell me?" | Text lines |

They are complementary. A metric tells you p95 latency doubled at 14:00; a trace tells you
*why* one of those slow requests was slow. A metric cannot explain a single request; a
trace cannot tell you whether it is representative.

**Aegis has traces. It does not export OTel metrics.** It has an in-process rolling window
that computes latency percentiles from completed runs — which is a genuine measurement, and
is not a metrics backend: it is per-process and resets on restart. That is an honest gap,
and the right way to talk about it is to say exactly that rather than call the window
"metrics".

---

## What to put on a span, and what not to

**Attributes are indexed and queryable.** Good ones:

- identifiers: model, tool name, node name;
- counts: candidates retrieved, results returned, tokens used;
- verdicts: guardrail passed/blocked, cache hit/miss;
- decisions: which specialist the router chose, and why.

**Do not put unbounded text on spans.** Full prompts and full documents blow up trace
storage and — much worse — put customer PII into an observability backend that has a
different access-control model from your database. A trace is a place data goes to be
looked at by engineers.

**Cardinality matters.** An attribute whose value is a user id or a query string has
unbounded distinct values. For traces that is survivable; for **metrics** it is fatal —
each distinct label combination is a separate time series, and unbounded cardinality is
the classic way to take down a metrics backend.

---

## The trace is the artefact you show a jury

Two claims a trace backs that nothing else can:

**"Every step is auditable."** Not "we log a lot" — you can point at a tree and say *this
is the run, here is the guardrail verdict, here is what was retrieved, here is why the
human gate fired*.

**"The system did not do X."** Proving a negative from logs is nearly impossible. From a
trace it is a structural argument: the vision pipeline's injection screen either has a
model call span *before* it or it does not.

And the connection to the rest of the platform: the **trace id is stamped on the audit row
and on every ledger row**. So "who authorised this refund" and "what did the system
actually do" are joinable. Without a shared id those are two accounts of the same event
that can never be reconciled.

---

## What you should now be able to explain

- Why logs fail for agents on four specific axes
- What a span is, what a trace is, and why parentage is the load-bearing field
- The concrete definition of glass box: no re-run needed to answer a question
- Why OpenTelemetry rather than a bespoke format
- What semantic conventions are, and why `gen_ai.*` churn means emitting an alias
- What OpenInference span kinds add that OTel's own `SpanKind` does not
- Head vs tail sampling, and why agent traces are worth keeping
- Traces vs metrics vs logs, and which question each answers
- What belongs on a span, and why cardinality and PII both matter
- Why the trace id must appear on the audit and ledger rows

**Next:** [`10-theory.md`](10-theory.md).
