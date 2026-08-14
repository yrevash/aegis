# The Gateway — interview questions and answers

Claim, reason, concrete detail. The detail is what separates "I read about LLM gateways"
from "I built one."

---

### "Why does every model call go through one function?"

Because there are five things that must never be forgotten on a model call — the budget
check, the cost record, the trace span, the timeout, and the routing decision — and
"never forgotten" is not achievable if there are forty call sites.

One chokepoint turns each of those from a convention into a structural guarantee. It
also makes the underlying library replaceable: every call site imports `aegis.gateway`,
not `litellm`, so swapping the router is one module rather than a codebase-wide edit.

The cost is that a mistake in the chokepoint is a mistake everywhere. That is the right
trade — one hard thing done carefully beats forty easy things done inconsistently — but
it does mean the chokepoint needs the most tests in the system.

---

### "How do you decide which model a call goes to?"

The caller asks for a **role**, never a model id. `CHEAP` for classification and
screening, `REASONING` for hard reasoning and the LLM judge, `GENERATION` for the
user-facing answer, plus `EMBEDDING`, `VISION` and `VOICE`. A config table maps role to
deployment id, overridable per role by environment variable.

Two payoffs. Swapping the fleet is one file. And cost control becomes structural rather
than remembered — a screening call is cheap *by construction*, because that is what the
call site says.

**The honest limitation:** static role routing cannot exploit per-request difficulty. A
hard question and an easy one in the same role get the same model. The alternatives are
cascading — FrugalGPT-style, call cheap first and escalate on a low score — and a
learned router like RouteLLM. Both add latency and an extra inference; the cascade
doubles the call count on escalation. For an interactive agent I would take static
routing and add a cascade on the `GENERATION` role only if I had measured a quality gap
worth the latency.

---

### "How do you stop a runaway agent from burning the budget?"

The check happens **before** the spend, at the chokepoint. `complete` reads the
governance context, and if a tenant is bound it awaits `enforce(ctx)` before it touches
the provider. Over cap means `BudgetExceededError` and no network call at all.

That "before" is the whole design. After-the-fact reconciliation is a receipt, not a
cap — it tells you that you overspent. An agent loop is plan/act/reflect, three model
calls per iteration; with reconciliation you find out after the money is gone.

The caps are hierarchical and enforced **inward**: the effective cap is the minimum of
the user's and the tenant's, with `None` meaning uncapped so a present cap always binds
over an absent one. User caps are checked first, so when both trip the error names the
user — same refusal, better attribution.

**And the part most people miss:** enforcement needs a database read, and that read can
fail. If it fails open, a transient database blip silently disables every spend cap in
the system, at exactly the moment nobody is watching. We fail **closed** by default and
make fail-open an explicit configured opt-in that logs a warning when it fires.

---

### "Is that a hard cap?"

No, and I would not claim it is. The check and the spend are not atomic — `enforce`
awaits a database read, then the provider call happens. With *k* concurrent requests you
can overshoot by up to *k* calls' worth of spend.

That is a deliberate trade. A hard cap needs reserve-then-settle: a synchronous write
before every call plus reconciliation of unused reservations, on the hot path of every
model call in the system. The overshoot here is bounded by in-flight concurrency, not
unbounded, and for spend caps that is the right side of the trade.

If the requirement were a genuinely hard cap — a prepaid credit balance, say — I would
move to reservations and accept the write.

---

### "How do you know what a call cost?"

Three tiers, in order, and the third one is the interesting one.

First, the provider's own cost map. Second — and this is the normal path here, because a
custom gateway's deployment ids are not in any public cost map — measured units × the
configured per-role rate. Third: **neither worked, and the call did consume billable
work.**

That third case is tagged `CostSource.UNPRICED` and logged at WARNING, naming the role,
the model and every measured unit. It is still `$0.00` in the number, but it carries the
statement *we could not price this*, which is a completely different claim from *this
was free*.

One enum field is the whole difference between a cost dashboard you can defend and one
that lies quietly.

---

### "Why does your usage model have audio seconds and image counts in it?"

Because tokens are not the universal billing unit and assuming they are creates an
uncapped spend path.

Whisper bills per **minute of audio** and reports zero prompt tokens. With a
tokens-only usage model, a transcription ledgers `prompt_tokens=0` → `$0.00`. It is a
real charge from the provider and an invisible one to us, so a tenant sitting at 99% of
their USD cap can transcribe indefinitely.

The fix generalises the cost formula: a role knows its **billing unit** — tokens, audio
minutes or images — and the input rate is charged per that unit. `VOICE` is priced
`(0.006, 0.0)`: six-tenths of a cent per audio minute, zero output-token rate, because
Whisper produces no billable output tokens. `Usage`, the ledger row and the governance
hook all carry `audio_seconds` and `images`, defaulted to zero so nothing token-only
changed.

**And the honesty case:** if the provider reports no duration and the caller supplied
none, there is nothing to price. That logs a warning and returns `UNPRICED` rather than
a comfortable zero.

---

### "Tell me about a bug you found in this."

The best one is a concurrency bug in the savings metric.

`stream_complete` reported the cost saved on *this* call. The original implementation
snapshotted the process-global tally, awaited the model call, snapshotted again, and
subtracted.

`await` is a yield point and the tally is process-global. Two concurrent calls: A
snapshots zero, awaits; B snapshots zero, awaits; both complete; both compute
`after − before` over a tally that now contains **both** savings. Each reports the
combined figure. The double counting scales with concurrency. And because the value is
clamped at zero, a call finishing in an unfavourable order could report `0.00` for a
real saving.

The general shape is worth naming: **a before/after delta over shared mutable state,
taken across an `await`, is a race.** It is read-modify-write without a lock, and it is
easy to miss because there is no thread and the code reads perfectly sequentially.

The fix derives the saving from that call's own `Usage` object and reads no shared state
at all. It goes through the same pricing function as the cumulative tally, so per-call
figures still sum to the aggregate — concurrency-safe without changing the semantics.

---

### "Tell me another one."

A 90-billion-parameter vision model was counted as a small model.

Small-model classification was substring markers on the deployment id, and one of them
was `llama-3.2`. The fleet's vision deployment is
`genailab-maas-Llama-3.2-90B-Vision-Instruct`. It matched.

The marker was not stupid — the Llama 3.2 family genuinely spans 1B/3B (small) and
11B/90B (vision, not small), so the generation alone cannot decide it.

**What makes this the dangerous kind of bug is the direction.** It pushes
`small-model share` up, attributes vision spend to the cheap bucket, and improves the
savings story. It makes the system look better at exactly the thing it claims to be good
at, and nobody investigates a metric that flatters them.

The fix makes a parameter count in the id **authoritative** and a veto over every
generation marker: anything at or above 10B is not small, whatever else the name says.
The regex has explicit boundaries so it does not match a version number.

Ever since, my first question about any metric is "does an error here only ever move it
in our favour?"

---

### "You mentioned embeddings had a bug too."

Same family. `complete` had the estimate fallback for unpriced deployments; `embed` did
not — it took the provider's zero and ledgered it. And `record_call` was never invoked
for embeddings at all, so they were absent from the dashboard too.

Embeddings are on the hot retrieval path: every query, every memory recall, every
consolidated fact. Individually cheap, collectively significant, and completely free as
far as the budget was concerned.

**The interesting part is the fix.** Simply adding embeddings to `record_call` would
have broken a different number. `small_model_share` means "of the calls where routing
chose between models, what fraction went small" — and embeddings have exactly one
deployment in the fleet, so routing never chose. Including them dilutes a metric about a
decision that was never made.

So there is now an explicit `routable roles` set, and only routable roles count toward
that denominator. Embeddings count toward cost and total calls, and are excluded from the
routing metric. The lesson: when you fix a metric by adding data, check every other
metric that shares its counters.

---

### "How do you handle a provider outage?"

Per-role fallback chains. `GENERATION` falls back to `REASONING` then `CHEAP` — degrade
the quality rather than fail the request.

Timeouts are the subtle part. Each attempt gets its own timeout budget, and an outer
`asyncio.wait_for` sized to `timeout × (chain length + 1)` wraps the whole await. If the
only timeout were for the total, the last link in the chain could get no time at all.
The outer one is a backstop for a genuinely hung coroutine — a library timeout is a
request; a `wait_for` you enforce is a guarantee.

On expiry it raises `TimeoutError`, which propagates like any transport failure. The run
fails closed rather than blocking indefinitely.

**And a fired fallback is measured, not assumed.** The stream event compares the
deployment that actually responded against the role's intended primary. That matters
because a fallback moves your routing metrics for a reason that has nothing to do with
routing policy.

---

### "What happens when a model won't return valid JSON?"

Exactly **one** corrective re-ask, with an explicit "that was not valid JSON, return only
a JSON object" nudge. Never a loop.

The bound is the point: the worst case is 2× the cost, not unbounded. A model that has
decided prose is the answer will keep deciding that.

**The guard that is easy to get wrong:** the re-ask must not fire when the reply has tool
calls. A tool-call response has empty content by design — it is not a failed JSON reply —
and without that condition every tool call in the system pays for a second round trip.

---

### "Why inject config, governance and observability instead of importing them?"

Because otherwise `import aegis.gateway` drags in a settings module, a database and an
OpenTelemetry SDK, and the package stops being importable by anything that just wants to
call a model.

Each is a `Protocol` — structural typing, so a host object with the right method shapes
satisfies it without inheriting from or importing the gateway. The concrete OTel sink in
`aegis.observability` implements the gateway's `ObservabilitySink` without importing the
gateway at all.

**The design rule that matters:** every default must be an *honest* no-op. The default
governance hook does no enforcement and its docstring says so. A default that looked
like enforcement and quietly allowed everything would be the worst possible failure —
you would believe you had budgets.

---

### "Your ledger write is best-effort. Isn't that dangerous?"

Yes, and it is still the right call — with a condition.

It is right because a model call that already succeeded must not be failed by an
accounting write. The provider has already charged you; failing the request loses the
work and does not recover the money.

It is dangerous because "swallowed" means "silent", and the ledger is what the USD cap is
computed from. A ledger that quietly stops accepting rows is a budget cap that quietly
stops binding.

That exact thing happened here: two new columns were added to the ledger model with the
`ALTER TABLE` written only in a docstring. `create_all` never alters an existing table,
so on any pre-existing database every ledger INSERT raised `UndefinedColumn` — and this
handler ate it. Rows vanished and the caps stopped binding.

**So the rule is not "don't swallow."** It is: when a best-effort path is load-bearing
for a control, something else must verify it works. We added an additive schema
reconciliation at bootstrap that refuses to start the API if the ledger table cannot be
brought into shape.

---

### "How would you test this module?"

Three layers.

**Unit, with a fake `litellm`.** The whole gateway is testable offline because the
library is imported lazily inside one function and the three hooks are injected. Assert
the ordering directly: a budget-exceeded hook must produce **zero** provider calls — that
is a `calls == []` assertion, which an implementation that calls first and checks after
cannot satisfy.

**Pricing tables as pure functions.** `unit_cost` and `billable_input_units` take no I/O.
Test the audio-minute path, the image path, the token path, and specifically that a
token-only call reduces to the original formula so nothing regressed.

**The failure directions explicitly.** An unpriceable call must come back `UNPRICED`, not
`ESTIMATED`. A 90B deployment id must classify as not-small. An embedding must not
change `small_model_share`. And the concurrency one: two concurrent `stream_complete`
calls must each report their own saving, which is a test the delta implementation fails
and the per-call implementation passes.

That last shape — a regression test confirmed *failing* on the pre-fix code — is what
makes a fix credible.
