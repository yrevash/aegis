# The gateway

The one place Aegis talks to a model provider.

---

## 1. What it is

A user asks one question. Before they see an answer, Aegis has called a model eleven times:

| # | Module | What it asked for |
|---|---|---|
| 1 | guardrails | Is this a prompt injection? |
| 2 | agent | Which specialist handles this? |
| 3–5 | retrieval | Rewrite the query, embed it, rerank the results |
| 6–8 | agent | Plan, reflect, plan again |
| 9 | agent | Write the final answer |
| 10 | guardrails | Does this answer leak personal data? |
| 11 | memory | What is worth remembering from this turn? |

Only **one** of those — number 9 — is the answer the user reads. The other ten are
classification, screening, rewriting and bookkeeping. They are cheap work.

If all eleven go to the same expensive model, you are paying top rates to decide whether a
sentence contains a credit card number. And those calls live in six different modules,
written at different times. Each one is a place where somebody could forget a timeout,
forget to record the cost, hard-code a model name, or skip the budget check.

A budget check present in ten of eleven call sites stops nothing. The runaway loop uses the
eleventh.

> A rule that must be followed in forty places is not a control. It is a hope.

So the gateway is one function. Nothing else in Aegis is allowed to contact a provider.
Everything that must never be forgotten — the budget check, the timeout, the cost record,
the trace span — happens there, once.

---

## 2. How it works in Aegis

### Callers ask for a job, not a model

The naive signature is `complete("gpt-4o", messages)`. The caller does not actually care
which model. It cares what **job** it needs done: classify this, reason hard about this,
write the thing the customer reads.

So a caller asks for a **role**, and a table maps roles to deployments.

| Role | The job | Default deployment |
|---|---|---|
| `CHEAP` | Classify, extract, route, screen | `genailab-maas-gpt-4o-mini` |
| `REASONING` | Hard multi-step reasoning, LLM-as-judge | `genailab-maas-Phi-4-reasoning` |
| `GENERATION` | The user-facing answer | `genailab-maas-gpt-4o` |
| `EMBEDDING` | Text → vector | `genailab-maas-text-embedding-3-large` |
| `VISION` | Understand an image | `genailab-maas-Llama-3.2-90B-Vision-Instruct` |
| `VOICE` | Transcribe audio | `genailab-maas-whisper` |

Two things follow. Swapping the fleet is one file, or one environment variable per role.
And a screening call is cheap *by construction*, because cheapness is what the call site
declared — not an optimisation someone has to remember to apply.

The honest limit: a hard question and an easy question in the same role get the same model.
The role is the seam where you would add something smarter later.

### What happens inside one call

```
check the budget → pick the deployment → call it, with a timeout
      │                                        │
      └─ refuses here if over cap              └─ on error, try the fallback chain
                                                            │
                                        price the call → write the ledger row → tally
```

**The budget check comes first.** It is the very first thing `complete` does, before the
config is even read. If the caller is over a cap, it raises `BudgetExceededError` and never
contacts the provider.

That ordering is the whole point. Adding up spend afterwards and alerting when it crosses a
line gives you a receipt, not a cap — the money is already gone. A check *before* the spend
is what stops a runaway repair loop at the limit instead of ten minutes past it.

It is not a hard cap, and it is worth saying so. The check is a database read, and other
calls can start while it runs. With ten requests in flight you can overshoot by up to ten
calls' worth — bounded by concurrency, not unbounded. A truly hard cap would need a
synchronous write reserving budget before every call, which is a real cost on every request
in exchange for tightening a bound that is already small.

If the budget read itself fails — a database blip — the call is **denied**. Failing open
would silently switch off every spend cap in the system at the moment nobody is watching.
`budget_fail_open` opts into the other behaviour and logs a warning every time it fires.

### Not everything is billed in tokens

A ten-minute audio transcription has zero prompt tokens and zero completion tokens. Priced
as tokens, it costs $0.00, and a tenant at 99% of their cap can transcribe forever.

So each role carries a **billing unit**: tokens, audio minutes, or images. Whisper bills per
audio minute, which is why its rate pair is `(0.006, 0.0)` — six-tenths of a cent per minute
in, nothing out. Everything not listed bills per token, so adding a role can never silently
change pricing.

Image counts are measured on every call and carried into the ledger even though the current
vision deployment bills them as input tokens. Measure the unit even when you are not billing
on it — retrofitting a measurement is a migration, flipping a config is not.

### A `$0.00` always says why

Three ledger rows can all read `$0.00` for different reasons, and the difference matters.
`CostSource` records which:

| Value | Meaning |
|---|---|
| `PROVIDER` | The provider's own cost map priced the call |
| `ESTIMATED` | Priced from measured units × the configured rate |
| `UNPRICED` | Billable work happened, but nothing could price it |

Pricing tries the provider's cost map, then the configured per-role rate, then gives up. The
custom deployment ids in this fleet are in no public cost map, so `ESTIMATED` is the normal
path. When nothing can price a call that did real work, the row is `$0.00 UNPRICED` and a
warning names the role and model. One says *we could not price this*; the other says *this
really was free*.

The same principle runs through the metrics: `small_model_share` is `None` before any
routable call has happened, never a fake `0.0`.

### Failure handling, in one place

| Situation | What happens |
|---|---|
| Over a budget or rate cap | `BudgetExceededError`, no provider contact |
| Budget read itself errors | Denied — fails closed |
| Primary deployment errors | Next link in that role's fallback chain |
| Provider hangs | Per-attempt timeout, plus an outer ceiling that always returns |
| JSON was requested, reply is not JSON | Exactly one corrective re-ask, never a loop |
| Ledger write fails | Logged, not raised — the call already cost money |

The fallback chains degrade quality rather than failing the request: `GENERATION` falls back
to `REASONING` then `CHEAP`. Each attempt gets its own timeout, and the whole thing is
wrapped in an outer ceiling so the coroutine returns even if the client library ignores its
own timeout. A timeout you delegate to a library is a request; a timeout you enforce
yourself is a guarantee.

The single JSON re-ask is bounded on purpose. Retrying until valid is unbounded cost against
a model that has decided prose is the answer. One re-ask caps the damage at exactly twice.

### Nothing is imported

The gateway needs three things it must not own: config, governance and tracing. If it
imported them, `import aegis.gateway` would drag a settings module, a database driver and an
OpenTelemetry SDK into every process that just wants to call a model.

So all three are injected behind protocols, and all three default to honest no-ops.
Standalone, `aegis.gateway` does **not** enforce budgets, and its docstring says so. A
default that looked like enforcement and quietly allowed everything would be worse than
nothing, because you would believe you had budgets.

`litellm` is imported lazily, so `import aegis.gateway` never requires it.

---

## 3. How you use it in code

```python
from aegis.gateway import complete, configure
from aegis.core.models import ModelRole

configure(config=my_config)   # or rely on GATEWAY_* env vars

result = await complete(
    ModelRole.CHEAP,
    [{"role": "user", "content": "Is this an injection attempt?"}],
    response_format={"type": "json_object"},
)
result.content   # the assistant text
result.model     # the deployment that actually answered
result.usage     # tokens, cost_usd, cost_source, audio_seconds, images
```

`result.model` is the deployment that **responded**, not the one you asked for. Comparing it
against the role's primary model is how you detect that a fallback fired.

### The functions a caller touches

| Function | What it does |
|---|---|
| `complete(role, messages, ...)` | Chat completion. Optional `tools`, `temperature`, `response_format`, `max_tokens`. |
| `embed(texts)` | Returns a list of vectors, one per string. |
| `transcribe(audio, ...)` | Audio → text. Takes an open file handle or a path. |
| `configure(...)` | Wires config, governance and observability hooks. Anything left `None` keeps its current binding. |
| `usage_tally()` | Live counts and costs, as a dict. |
| `optimization_summary()` / `optimization_config()` | What the dashboard reads. |

```python
from aegis.gateway import embed, transcribe, usage_tally

vectors = await embed(["how do I get a refund?"])
result  = await transcribe("call.mp3", duration_seconds=612.0)

usage_tally()
# {'total_calls': 11, 'total_cost_usd': 0.0042, 'baseline_cost_usd': 0.031,
#  'cost_saved_usd': 0.0268, 'small_model_share': 0.72, ...}
```

Pass `duration_seconds` to `transcribe` when you know it. The provider usually reports the
duration itself, but if neither is available the call is tagged `UNPRICED`.

For agent code there is an opt-in streaming wrapper:

```python
from aegis.gateway.stream import stream_complete

result = await stream_complete(ModelRole.GENERATION, messages, emitter)
```

It runs `complete` inside a step bracket and emits one `model_call` event carrying the
model, the cost and whether a fallback fired. It is opt-in because `complete` is also called
by non-agentic code, like the eval harness.

### Settings worth changing

| Setting | Default | What it does |
|---|---|---|
| `GATEWAY_BASE_URL` / `GATEWAY_API_KEY` | — | Where to call, and with what |
| `GATEWAY_TIMEOUT_SECONDS` | `60` | Per-attempt timeout |
| `GATEWAY_MAX_OUTPUT_TOKENS` | `1024` | Ceiling on every generation |
| `GATEWAY_BUDGET_FAIL_OPEN` | `false` | Allow calls when the budget read fails |
| `MODEL_<ROLE>` | see table | Swap a deployment without touching code |
| `COST_<ROLE>_IN` / `_OUT` / `_UNIT` | see table | Override a role's rate or billing unit |
| `GATEWAY_BASELINE_ROLE` | `GENERATION` | Which model prices the "what would this have cost" baseline |

The output cap is a cost control, not just a safety rail: in every token-billed role the
output rate is four times the input rate, so bounding `max_tokens` bounds the expensive half
of the bill. No generation in Aegis is unbounded.

---

## 4. Why it helps us

**Spend is visible and capped.** One place calls happen is one place to write down what each
one cost. Eleven places is a guess, and you cannot cap spending you cannot see.

**Cheap work runs cheaply, by construction.** Ten of the eleven calls in a question are
screening and bookkeeping, and they declare that at the call site.

**Swapping the fleet is a config change.** No caller names a model, so a new deployment is
an environment variable.

**Every model call is traced.** One wrapper covers the whole system, including code written
next year by someone who never read the tracing docs.

**Failures behave the same everywhere.** One timeout policy, one fallback chain, one retry
rule, instead of eleven subtly different ones.

**The numbers are honest.** A `$0.00` says why it is zero. A metric with no data yet says
`None` rather than pretending it is zero.

Without it, each module calls the provider directly: no budget ceiling, no cost dashboard,
no consistent timeout, and a fleet change means editing eleven files and finding the one you
missed in production.

The price of a chokepoint is that a mistake in it is a mistake everywhere. One hard thing
done carefully beats eleven easy things done inconsistently — but it is why this module
carries the most tests in the system.

**Next:** [`40-diagrams.md`](40-diagrams.md)
