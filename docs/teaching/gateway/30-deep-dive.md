# The Gateway — deep dive: concurrency, failure modes, and the real bugs

Four bugs, each told properly. Every one was found in this codebase and every one has a
fix you can point at in the source. Learn one of these well enough to tell the story and
you will out-answer most candidates.

---

## Bug 1 — every embedding cost $0.00, so embeddings never counted against a cap

### What was happening

`complete` and `embed` both call a provider and both write a ledger row. But only
`complete` had the estimate fallback.

The gateway talks to a **custom OpenAI-compatible deployment**. The deployment ids
(`genailab-maas-text-embedding-3-large`) are not in LiteLLM's public cost map, so
`litellm.completion_cost(...)` returns `0` for them. `complete` handled that: if the
provider's map yields nothing, price the call from measured units × the configured
per-role rate.

`embed` skipped that step. It took the provider's `0` and ledgered it.

There was a second half. `record_call` — the function that feeds the in-process tally
behind the savings dashboard — was **never invoked** for embeddings at all. So
embeddings were simultaneously:

- **$0.00 in the durable ledger**, and therefore invisible to the USD budget cap that
  is computed by summing that ledger; and
- **absent from `usage_tally`**, and therefore absent from the cost dashboard.

### Why it mattered more than it looks

Embeddings are on the **hot retrieval path**. Every query embeds. Every memory recall
embeds. Consolidation embeds every candidate fact. In an agentic system, embedding
calls are among the highest-*count* calls in the whole platform — individually cheap,
collectively significant, and completely free as far as the budget was concerned.

A tenant with a strict USD cap could drive unbounded retrieval traffic and never
approach it.

### The fix

`llm.py:1047-1049` — `embed` now calls the same `_resolve_cost` that `complete` uses:

```python
cost, _cost_source = _resolve_cost(
    litellm, response, ModelRole.EMBEDDING, prompt_tokens=prompt_tokens
)
```

and `llm.py:1053-1058` calls `record_call(...)` with `role=ModelRole.EMBEDDING`.

The comments at `llm.py:1043-1052` are the postmortem, left in the source deliberately.

### The subtlety in the fix

Adding embeddings to `record_call` would have broken a *different* number. `small_model_share`
is "of the calls where routing chose between models, what fraction went to a small one".
Embeddings have exactly one deployment in the fleet — routing never chose anything.

So `_ROUTABLE_ROLES` (`routing.py:118`) exists, and `record_call` only increments
`routable_calls` / `small_calls` for a routable role (`llm.py:556-558`). Embeddings
now count toward cost and toward `total_calls`, and are excluded from the denominator
of a routing metric.

**The transferable lesson:** when you fix a metric by adding data to it, check every
*other* metric that shares its counters.

---

## Bug 2 — a 90-billion-parameter vision model counted as a "small model"

### What was happening

`is_small_model` classified a deployment id by substring markers:

```python
_SMALL_MODEL_MARKERS = ("mini", "3.5", "3-5", "llama-3.2", "phi-3.5")
```

The fleet's vision deployment is `genailab-maas-Llama-3.2-90B-Vision-Instruct`.

Lowercased, it contains `llama-3.2`. So it matched. A 90-billion-parameter vision model
was classified as small.

### Why the marker was there at all, and why it is not simply stupid

The Llama 3.2 family genuinely **spans both**: 1B and 3B are small models by any
definition; 11B and 90B are the vision variants and are not. The *generation* alone
cannot decide it. The marker was correct for half the family.

### Why this one is the most damaging kind of bug

Look at which direction it moves the numbers.

- `small_model_share` goes **up** — more calls look like they went to a small model.
- Every vision call is attributed to the cheap bucket in the per-role breakdown.
- The savings story — "we route cheap work to cheap models" — gets **better**.

It is a bug that makes the system look better at exactly the thing it claims to be good
at. Nobody investigates a metric that flatters them. This is why "does this error only
move the number in our favour?" is a question worth asking of every metric you build.

### The fix

`routing.py:90-110`. A parameter count spelled in the id is **authoritative** and vetoes
every generation marker:

```python
_SMALL_MODEL_MAX_PARAM_B: float = 10.0
_PARAM_COUNT_RE = re.compile(r"(?<![a-z0-9.])(\d+(?:\.\d+)?)b(?![a-z0-9])")

def is_small_model(model_id: str) -> bool:
    lowered = model_id.lower()
    params_b = _param_count_b(lowered)
    if params_b is not None and params_b >= _SMALL_MODEL_MAX_PARAM_B:
        return False
    return any(marker in lowered for marker in _SMALL_MODEL_MARKERS)
```

Note the regex boundaries. `(?<![a-z0-9.])` stops it matching the `2` in `3.2b`-shaped
substrings of a version number; `(?![a-z0-9])` stops it matching a `70bx` token. The
comment at `routing.py:85-89` records the reasoning so nobody re-adds the naive marker.

---

## Bug 3 — `cost_saved_usd` computed as a delta over a process-global counter, across an `await`

### What was happening

`stream_complete` reported the saving for **this** call. The original implementation:

```python
before = usage_tally()["cost_saved_usd"]
result = await complete(role, messages, **kwargs)     # ← yields to the event loop
after  = usage_tally()["cost_saved_usd"]
saved  = max(0.0, after - before)
```

Read it as a concurrency problem, not an arithmetic one.

`_tally` is **process-global** (`llm.py:466`). `await` is a **yield point**. Between
`before` and `after`, any other coroutine in the process can run — and if it made a
model call, it mutated `_tally`.

### The failure, concretely

Two concurrent `stream_complete` calls, A and B:

1. A snapshots `before = 0.00`.
2. A awaits. B runs: snapshots `before = 0.00`, awaits.
3. A's provider returns. `record_call` adds A's saving: tally is now `0.05`.
4. B's provider returns. `record_call` adds B's saving: tally is now `0.09`.
5. B computes `after − before = 0.09 − 0.00 = 0.09` — **B claims A's saving as well as
   its own.**
6. A computes `after − before = 0.09 − 0.00 = 0.09` — so does A.

Total reported saving: `$0.18` on `$0.09` of real saving. Under a many-concurrent-request
load the double counting scales with concurrency. And because the value is clamped at
zero (`max(0.0, ...)`), a call that finished *second* in an ordering where the tally
moved unfavourably could report `0.00` — a real saving silently reported as none.

### The general shape

**A before/after delta over shared mutable state, taken across an `await`, is a race.**
It is the async equivalent of non-atomic read-modify-write, and it is easy to miss
because there is no explicit lock, no thread, and the code reads perfectly
sequentially.

### The fix

`llm.py:505-521` — `call_saving_usd(usage)` derives the saving from **this call's own
`Usage` object** and reads no shared state at all:

```python
def call_saving_usd(usage: Usage) -> float:
    baseline = _baseline_cost(
        usage.cost_usd,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        audio_seconds=usage.audio_seconds,
        images=usage.images,
    )
    return max(0.0, baseline - usage.cost_usd)
```

`stream.py:58` then calls it with `result.usage`. The comment at `stream.py:51-57`
records the race.

The fix is priced identically to the cumulative tally (both go through `_baseline_cost`,
`llm.py:481`), so the per-call figures still sum to the aggregate. **Concurrency-safety
without changing the semantics** is the property you want from a fix like this.

---

## Bug 4 — the non-token billing hole: Whisper ledgered at $0.00

### What was happening

The `Usage` model had exactly three fields: `prompt_tokens`, `completion_tokens`,
`cost_usd`. Every accounting path in the system was shaped by that assumption.

Then voice arrived. `transcribe` calls a hosted Whisper deployment. Whisper **bills per
minute of audio** and reports no prompt tokens.

So a transcription produced `prompt_tokens=0`, `completion_tokens=0`, and every pricing
path multiplied zero by a rate and got `$0.00`.

### The chain of consequences

1. The ledger row said this call cost nothing.
2. The USD cap sums the ledger. A tenant at 99% of their cap could transcribe
   indefinitely.
3. The cost dashboard showed voice as free.
4. `_resolve_cost` would have reported the zero without complaint.

### Why it is a *design* bug rather than a coding slip

Nothing was mis-typed. The code did exactly what it said. The **model of the world** —
"a model call is billed in tokens" — was wrong, and every layer had inherited it.

That is the useful thing to notice: the bug lived in a shared assumption, so it appeared
identically in the usage type, the cost table, the ledger schema, the governance hook
signature and the dashboard. Fixing it in one place would have fixed nothing.

### The fix, layer by layer

**The unit becomes explicit.** `BillingUnit` (`routing.py:152`) — `TOKENS`,
`AUDIO_MINUTES`, `IMAGES` — with `_BILLING_UNIT` (`:173`) mapping `VOICE →
AUDIO_MINUTES` and everything else defaulting to tokens, so adding a role never
silently changes pricing.

**The cost formula generalises.** `billable_input_units` (`routing.py:207`) returns
`audio_seconds / 60` for `AUDIO_MINUTES`, `images` for `IMAGES`, `prompt_tokens / 1000`
otherwise. `unit_cost` (`:227`) multiplies by the input rate. A token-only call reduces
to the original formula exactly.

**The usage record carries the units.** `Usage.audio_seconds` and `Usage.images`
(`types.py:99-104`), both defaulting to zero.

**The ledger carries them.** `UsageLedger.audio_seconds` / `.images`
(`aegis/src/aegis/governance/models.py:191-193`), both with `server_default="0"`.

**The hook signature widens compatibly.** `GovernanceHook.record` gained
`audio_seconds: float = 0.0, images: int = 0` (`llm.py:174-176`) — defaulted, so an
existing hook keeps working.

**And the payload stays byte-identical for token-only calls.** `_record_usage`
(`llm.py:771`) forwards the new kwargs **only** when the call actually consumed them
(`llm.py:794-796`):

```python
extra: dict[str, Any] = {}
if _has_non_token_units(audio_seconds, images):
    extra = {"audio_seconds": audio_seconds, "images": images}
```

So a host hook that has not been widened cannot break on a chat call. If it *is* called
with the new kwargs and cannot accept them, it raises `TypeError`, which is logged at
WARNING with a traceback (`llm.py:807-808`) — visible, never a silent skip.

### The honesty half

Suppose the provider reports no duration and the caller supplied none. There are no
tokens, no seconds, no images. The old code would have said `$0.00`.

`transcribe` logs a WARNING (`llm.py:1205-1210`) and calls `_resolve_cost` with
`billable_work=True` (`llm.py:1221`), which forces the `UNPRICED` branch
(`llm.py:733-753`). The result carries `cost_source=UNPRICED`.

**`$0.00 UNPRICED` and `$0.00 ESTIMATED` are different statements.** The first says *we
could not price this*; the second says *this really was free*. One enum field is the
whole difference between a cost dashboard you can defend and one you cannot.

---

## Failure modes worth being able to enumerate

### The ledger write is best-effort — and that is a knife

`_record_usage` (`llm.py:771`) swallows every exception and logs (`:807-808`). The
reasoning in its docstring is sound: *"A ledger write is a durable record, not a control
path — a failure here must never fail the model call that already succeeded."*

But "swallowed" means "silent". A ledger that stops accepting rows stops the USD cap
binding, and nothing in the request path says so. That exact failure happened — see the
[`governance/`](../governance/30-deep-dive.md) deep dive, where new ledger columns had no
migration and every INSERT raised `UndefinedColumn`, which this handler quietly ate.

The lesson is not "don't swallow". It is: **when a best-effort path is load-bearing for
a control, something else must verify it is working.** In this system that is
`reconcile_additive_columns`, which refuses to boot if the ledger table cannot be
written.

### Enforcement failure: fail closed by default

`backend/src/app/core/llm.py:158-173`. A `BudgetExceededError` propagates. Any *other*
exception from the enforcement read raises a synthetic `BudgetExceededError` with
`limit_type="enforcement_error"` and the message *"Budget enforcement unavailable;
denying the call (fail-closed)."*

Fail-open is available via `budget_fail_open` (`:159`) and logs a warning when it fires.

The argument for the default: a fail-open enforcement error disables **every cap in the
system** at exactly the moment nobody is watching. A denied call is a visible, recoverable
outage; an undetected uncapped spend is not.

### An `enforce` that yields is not atomic with the spend

`enforce` awaits a database read; the provider call happens after. Concurrent calls can
each pass the check before any of them records usage, so the cap can be overshot by up
to the in-flight concurrency. This is a **deliberate** approximation — see
[`10-theory.md`](10-theory.md#3-budget-enforcement-windows-and-the-read-modify-write-hazard)
for what a hard cap would cost. Say it plainly in an interview; do not claim a guarantee
the code does not make.

### A hung provider

Three layers. Per-attempt `timeout` passed to the client (`llm.py:896`), so each link of
the fallback chain is bounded. An outer `asyncio.wait_for` sized to the whole chain
(`llm.py:912-916`, applied in `_bounded_acompletion` at `:379`). And for `embed` /
`transcribe`, `timeout + 5.0` (`:1039`, `:1192`).

The outer ceiling exists because a library timeout is a *request* and a `wait_for` is a
*guarantee*. On expiry it raises `TimeoutError`, which propagates like any transport
failure — the run **fails closed** rather than blocking indefinitely.

### The `_ssl_configured` global

`_litellm()` (`llm.py:656`) sets `litellm.ssl_verify` once, guarded by a module global
(`llm.py:350`, `:666-670`). It is a **process-wide** setting on a third-party module,
which the comment acknowledges as *"a scoped, documented exception."* The consequence:
changing `ssl_verify` after the first call has no effect. That is a real limitation and
you should say so rather than pretend it is configurable per-call.

### Deployment ids leak into a metric

`is_small_model` is a **string heuristic over deployment names**. It is correct for the
current fleet and it is not a general classifier. A fleet whose ids do not spell their
size (`internal-model-v7`) would fall through to the markers and be classified by
accident. The mitigation is that it is one function, in one file, with a test — but
"we classify by name" is the honest description, not "we know the model sizes."

---

## The invariants worth naming

1. **Enforce before spend.** Nothing below `_governance.enforce` runs if the cap is
   breached (`llm.py:871-874`).
2. **A $0 always carries its provenance.** `CostSource` is never left to inference.
3. **Per-call figures are computed from per-call data.** No before/after deltas over
   shared state.
4. **A routing metric counts only routable calls.**
5. **The aggregate is single-sourced.** `optimization_summary` copies `usage_tally`'s
   top-line verbatim (`llm.py:614`) instead of recomputing it, so the two can never
   disagree.
6. **The no-op default is honest.** Standalone `aegis.gateway` does not enforce budgets
   and its docstring says so (`llm.py:224-226`).

**Next:** [`40-diagrams.md`](40-diagrams.md) — every path, drawn.
