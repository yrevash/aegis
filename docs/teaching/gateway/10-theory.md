# The Gateway — the theory

Routing as a decision problem, cost models, rate limiting, timeout composition, and the
alternatives we did not choose.

---

## 1. Model routing is a constrained assignment problem

You have a stream of requests $r_1 \dots r_n$ and a fleet of models $m_1 \dots m_k$.
Each pairing has a cost $c(r, m)$ and a quality $q(r, m)$. You want to maximise total
quality subject to a spend budget:

$$\max \sum_i q(r_i, m_{a(i)}) \quad \text{s.t.} \quad \sum_i c(r_i, m_{a(i)}) \le B$$

where $a(i)$ is the assignment. This is a knapsack-shaped problem, and in the general
online form (requests arrive one at a time, you must decide immediately) it is not
optimally solvable — you do not know the future request stream.

The literature splits into three families:

**Predictive routing.** Train a model to predict, per request, whether a small model
will produce an answer as good as a large one. *RouteLLM* (Ong et al., 2024) does this
with preference data and reports large cost reductions at near-frontier quality.
**Cost:** you now have a router model to train, serve, and keep calibrated — a
second-order ML problem inside your first-order one.

**Cascading.** Call the cheap model first; score its answer; escalate to the expensive
model only if the score is low. *FrugalGPT* (Chen, Zaharia & Zou, 2023) formalises this
and shows real savings. **Cost:** latency is now the *sum* of the models you tried,
and the scorer is itself a model call. Cascades are excellent for high-volume batch
work and poor for interactive latency budgets.

**Static role routing.** The call site declares the *job class*; a config table maps
job class → model. **Cost:** the classification is made by the developer at write time,
not by a model at run time — so it is only as good as the call sites.

Aegis uses **static role routing**. The defensible reasons:

- The assignment is *known at the call site*. A guardrail screening call is cheap work
  by construction; you do not need a learned router to discover that.
- It adds **zero latency** and **zero extra model calls**. A cascade doubles the call
  count on escalation; a predictive router adds an inference before every call.
- It is **auditable**. "Why did this go to the cheap model?" has an answer you can read
  in the source, not a router model's logits.
- It composes with everything else. A role is a stable key, so budgets, cost tables,
  fallback chains and metrics can all be indexed by it.

The honest limitation: static routing cannot exploit *per-request* difficulty. A hard
question and an easy question in the same role get the same model. If you had high
request volume and a measurable quality gap, a cascade on the `GENERATION` role is the
natural next step — and the role abstraction is exactly the seam where you would add it.

---

## 2. The cost model

Provider pricing for a chat model is affine in tokens:

$$c = \frac{p}{1000}\rho_{\text{in}} + \frac{g}{1000}\rho_{\text{out}}$$

with $p$ prompt tokens, $g$ completion tokens, and $\rho$ the per-1k rates. Output
tokens are typically 3–4× the price of input tokens, which is why bounding
`max_tokens` is a cost control and not just a safety rail.

**The generalisation that matters.** Not every model bills in tokens. Write the input
term as *units × rate*, where the unit depends on the model:

$$c = u(\text{call}) \cdot \rho_{\text{in}} + \frac{g}{1000}\rho_{\text{out}}$$

$$
u = \begin{cases}
p/1000 & \text{billing unit is tokens} \\
s/60 & \text{billing unit is audio minutes} \\
n_{\text{img}} & \text{billing unit is images}
\end{cases}
$$

This single change is what lets a transcription (which reports $p = 0$) produce a real
cost. Whisper's public pricing at ~\$0.006/minute has $\rho_{\text{out}} = 0$ — it
produces no billable output tokens — which the pair $(0.006, 0.0)$ states exactly.

### Where the price comes from, in priority order

1. **The provider's own cost map.** If the SDK knows the model, use its number. It is
   authoritative.
2. **A configured per-role rate × measured units.** Custom or self-hosted deployment
   ids are *not* in any public cost map, so this is the normal path for a private
   gateway.
3. **Neither.** The call consumed billable work and cannot be priced.

Case 3 is the interesting one. The tempting implementation returns `0.0`. But `$0.00`
is a claim — it says *this was free*. If a transcription happened and no duration was
reported, the truthful statement is *we could not price this*. Carrying a
provenance tag on every cost (`provider` / `estimated` / `unpriced`) is what keeps a
zero from lying, and it costs one enum field.

---

## 3. Budget enforcement: windows and the read-modify-write hazard

A cap is defined over a **rolling window**: "100,000 tokens per day" means the sum of
usage in the last 86,400 seconds, not since midnight. Rolling windows have no reset
cliff (nobody games the boundary) at the cost of a range scan per check.

Aegis's enforcement is a *sum over the ledger*:

```
SELECT sum(prompt_tokens + completion_tokens), sum(cost_usd), count(*)
FROM usage_ledger
WHERE <scope> = :id AND ts >= now() - window
```

with an index on `(scope, ts)` making it a range scan rather than a table scan.

### The concurrency reality

The check and the spend are not atomic. Between "read the sum" and "the provider
charges you", other concurrent calls can start. With $k$ concurrent requests you can
overshoot the cap by up to $k$ calls' worth of spend.

Three options:

| Approach | Guarantee | Cost |
|---|---|---|
| Read-then-spend (this system) | Approximate cap, overshoot bounded by concurrency | One read per call |
| Reserve-then-settle (two-phase) | Hard cap | A write before every call + reconciliation of unused reservations |
| Serialised token bucket | Hard cap | A single point of contention for every call |

For **spend** caps, approximate is the right trade: the overshoot is bounded by
in-flight concurrency, not unbounded, and the cost of a hard cap is a synchronous write
on the hot path of every model call. For **rate** caps (rpm/tpm) the same summation
over a 60-second window gives the same approximation with the same reasoning.

Be able to say this out loud in an interview. "It is approximate, the overshoot is
bounded by concurrency, and here is what a hard cap would cost" is a much stronger
answer than claiming a guarantee the code does not provide.

### Hierarchical caps and inward enforcement

A user belongs to a tenant. Both can carry caps. The effective cap is the **tighter**
of the two — a user cannot be granted more than their tenant has:

$$\text{effective} = \min(\text{cap}_{\text{user}}, \text{cap}_{\text{tenant}})$$

with `None` meaning *uncapped*, so a present cap always binds over an absent one. This
is "nearest-binding" or "inward" enforcement, and it is the same semantics as
filesystem quotas.

Order matters for *attribution*: check the user's caps first, so that when both are
breached the error names the user rather than the tenant. Same refusal, better
diagnosis.

---

## 4. Timeout composition

Give one call a timeout $T$. Now give it a fallback chain of length $f$. What is the
worst-case wall clock?

If each attempt gets $T$: $(f+1) \cdot T$.
If the whole chain gets $T$: the last attempt may get $\approx 0$.

Aegis takes the first: each attempt is bounded by $T$ (passed to the client library),
and an **outer** ceiling of $(f+1) \cdot T$ wraps the whole await. The outer one is a
backstop, not the primary control — its job is to guarantee the coroutine returns even
if the library's own timeout is ignored, which is a real failure mode with pooled HTTP
clients and connection-level hangs.

The general principle: **a timeout you delegate to a library is a request; a timeout you
enforce yourself is a guarantee.** Systems that block forever almost always do so
because every layer assumed a lower layer's timeout would fire.

---

## 5. Structured output and the single corrective re-ask

When you need JSON, `response_format={"type":"json_object"}` improves compliance but
does not guarantee it. Models still emit fences, preambles, or prose.

Design space:

- **Retry until valid.** Unbounded cost and latency on a model that has decided prose
  is the answer.
- **Parse leniently.** Strip fences, find the first balanced `{...}`. Cheap and often
  right — this is what the eval judge does.
- **Constrained decoding / grammars.** A hard guarantee, but requires provider support
  you may not have through a proxy.
- **Exactly one corrective re-ask.** Ask again with an explicit "that was not valid
  JSON" nudge, once. If it fails twice, it is failing.

Aegis's gateway takes the last for generation calls. The bound is the point: the cost
of a bad reply is capped at exactly 2× rather than unbounded. Note that a reply which
is *empty by design* — a tool-call response carries no content — must not trigger the
re-ask, or every tool call pays double.

---

## 6. Why a facade over a routing library, rather than raw SDKs

LiteLLM (the library underneath) already normalises many providers behind one call
shape, handles fallbacks, and knows public cost maps. Why wrap it at all?

Because the library gives you *provider normalisation*, and what you need is
*organisational policy*: budgets, ledgers, tenant attribution, tracing, role routing,
and the honesty rules about unpriced calls. Those are yours, not the library's.

The wrapper is also what makes the library **replaceable**. Every call site in Aegis
imports `aegis.gateway`, not `litellm`. Swapping the underlying router is one module.

---

## 7. Dependency injection as the decoupling mechanism

The gateway needs three things it must not *own*:

- **config** (base url, key, timeouts) — belongs to the deployment;
- **governance** (budgets, ledger) — belongs to the host's database;
- **observability** (spans, trace ids) — belongs to the host's tracing stack.

If the gateway imported them, it would drag a database, a settings module and an OTel
SDK into every process that merely wants to call a model.

So they are **injected**, each behind a `Protocol` (structural typing — an object with
the right method shapes satisfies it, no inheritance and no import of the gateway
required), and each defaults to a **documented no-op**.

The design rule here is worth stating precisely: *the no-op default must be honest.*
The default governance hook does not enforce anything, and says so — it does not
pretend to enforce and silently allow. A hook that looked like enforcement and did
nothing would be the worst possible default.

---

## What you should now be able to explain

- Routing as constrained assignment, and the three families of solution
- Why static role routing was chosen over cascading or predictive routing, and its
  honest limitation
- The generalised cost formula with a per-role billing unit
- The three-tier cost resolution order and why "unpriced" must be distinguishable
- Rolling-window budgets, the read-then-spend hazard, and what a hard cap would cost
- Inward (nearest-binding) hierarchical caps, and why user caps are checked first
- Timeout composition across a fallback chain, and per-attempt vs total budgets
- Why the corrective re-ask is bounded at one, and why tool calls must be excluded
- Why the gateway injects config/governance/observability behind Protocols

**Next:** [`20-in-aegis.md`](20-in-aegis.md) — the exact implementation, with line
numbers.
