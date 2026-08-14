# The Gateway — the concept, from zero

No code in this file. Just why a system that calls language models needs exactly one
place where those calls happen.

---

## The problem

A language model call is not a function call. It is:

- **money leaving your account**, per token, every time;
- **a network request to someone else's server**, which can hang, rate-limit, or die;
- **a choice of model**, and the wrong choice costs 15× more than the right one;
- **an event someone will eventually ask you to account for** — who spent this, on
  what, under whose authority.

Now imagine those calls scattered across forty files. The retrieval module calls a
model to rerank. The guardrail module calls one to classify. The memory consolidator
calls one to extract facts. The agent calls one to plan, one to answer, one to
reflect. The eval harness calls one to judge.

Every one of those is a separate place where you could forget the timeout, forget the
cost accounting, hard-code the wrong model, or skip the budget check.

**The gateway is the answer to "where do I put the thing that must never be
forgotten?"** One function. Every model call goes through it. Nothing else is allowed
to talk to a provider.

---

## What a chokepoint buys you

The word "chokepoint" sounds like a bottleneck. It is really a **single point of
control**, and that is the whole value:

**Cost accounting becomes possible.** If there is one place calls happen, there is one
place to write down what each one cost. If there are forty, your cost dashboard is a
guess.

**Budgets become enforceable.** You cannot stop spending you cannot see. A budget check
in one function stops every call; a budget check in thirty-nine of forty places stops
nothing, because the fortieth is where the runaway loop lives.

**Tracing becomes complete.** One span-opening wrapper covers every model call in the
system, including the ones written next year by someone who never read the tracing
docs.

**Swapping the model fleet becomes a config change.** Because callers ask for a *job*,
not a model name.

**Failure handling becomes uniform.** One timeout policy, one fallback chain, one
retry rule — not thirty-nine subtly different ones.

The cost of a chokepoint is that it must be *very* well designed, because every
mistake in it is a mistake everywhere. That is a good trade: one hard thing done once
beats forty easy things done inconsistently.

---

## Routing by role, not by name

The naive design is `call_model("gpt-4o", messages)`. It is wrong for a reason that
takes a moment to see.

The caller does not care *which* model. The caller cares what **job** it needs done:
"classify this, it's easy" or "reason hard about this" or "write the final answer."
Those are different jobs with different price/quality trade-offs.

So the caller asks for a **role**:

| Role | The job | Why a different model |
|---|---|---|
| `CHEAP` | Classify, extract, route, screen | Trivial work; a big model is pure waste |
| `REASONING` | Hard multi-step reasoning, LLM-as-judge | Worth the price for correctness |
| `GENERATION` | The user-facing answer | Quality is visible to the customer |
| `EMBEDDING` | Turn text into vectors | A completely different kind of model |
| `VISION` | Understand an image | Multimodal |
| `VOICE` | Transcribe audio | Speech-to-text |

Two payoffs. **Swapping the fleet is one file.** When the provider changes, or a
cheaper model appears, you edit the role→model map and every call site follows.
**Cost control becomes structural.** Routing screening and classification to a cheap
model is not a micro-optimisation you remember to apply — it is what the call site
*says*.

This is the origin of the efficiency claim you can actually defend: *most* calls in an
agent run are not the final answer. They are classification, routing, screening,
reranking. Sending those to a frontier model is how demos become expensive products.

---

## Budgets: why "before" is the whole idea

Here is the design decision that separates a real cost control from a dashboard.

You can check a budget in two places:

**After the call.** Record what was spent, add it up, and alert when the total crosses
a line. This is *reconciliation*. It tells you that you have overspent. It does not
stop you overspending.

**Before the call.** Read what has been spent so far; if it is already at the cap,
refuse the call and never contact the provider.

Only the second is a cap. The first is a receipt.

The scenario that makes this concrete: an agent enters a loop. Plan, act, reflect,
plan, act, reflect. Each iteration is three model calls. With after-the-fact
accounting, you find out at the end of the run — or the end of the hour, or the end of
the billing period — that a single tenant burned through the month's budget in eleven
minutes. With before-the-spend enforcement, call number *N* is refused, the run
terminates with an honest "budget exceeded", and the damage stops at the cap.

**The corollary nobody likes:** enforcement needs a *read* before every call, and that
read can fail. If your budget database blips, do you allow the call (fail open) or
deny it (fail closed)? Failing open means a transient database problem silently
disables every spend cap in the system — precisely when nobody is watching. The
defensible default is **fail closed**, with fail-open as a deliberate, configured
opt-in.

---

## The ledger: one durable row per call

The budget check needs to know what has been spent. That number has to come from
somewhere durable, because a process-local counter resets when the process does — and
because you need per-tenant, per-user attribution, not one global total.

So every governed call writes a **ledger row**: which tenant, which user, which model,
how many tokens in, how many out, what it cost, which trace it belonged to.

The ledger is both the input to the budget check and the source of the cost dashboard.
That is deliberate: a dashboard computed from a *different* source than the enforcer
will eventually disagree with it, and then nobody trusts either.

**The ledger write is best-effort.** A model call that already succeeded must not be
failed because the accounting row could not be written. That is the right call — and
it creates a trap covered in the deep dive, because "best-effort" means "silent when
it breaks", and a silently-broken ledger is a silently-disabled budget cap.

---

## Not everything is billed in tokens

This is the subtlety that catches most implementations.

The mental model everyone builds is: cost = tokens × rate. It is true for chat models.
It is false in general:

- **Whisper (speech-to-text) bills per minute of audio.** A ten-minute recording
  reports zero prompt tokens.
- **Some image deployments bill per image**, not per token of image encoding.

If your usage type only has `prompt_tokens` and `completion_tokens`, a transcription
ledgers `0` tokens and therefore `$0.00`. It is a real charge from the provider and an
invisible one to you. A tenant with a strict USD cap can transcribe without limit,
because none of it counts.

The fix is to make the **billing unit** an explicit, first-class concept: a role knows
whether its input rate is per 1000 tokens, per audio-minute, or per image, and the
usage record carries audio seconds and image counts alongside token counts. Then a
per-minute charge lands in the ledger, and the USD cap bites.

**And a $0 must never be ambiguous.** There are three different reasons a call can cost
zero: the provider priced it at zero, we estimated zero from measured units, or *we
could not price it at all*. The third is a warning, not a free call. Collapsing them
into one number is how a cost dashboard lies quietly.

---

## Fallback chains

Providers fail. A deployment goes down, hits a capacity limit, or returns a 500.

A **fallback chain** says: if the primary model for this role errors, try these others,
in this order. `GENERATION` falls back to `REASONING` then `CHEAP` — degrade the
quality rather than fail the request.

Two things to be careful about:

**Timeouts must bound each attempt, not the whole chain.** If your only timeout is
"60 seconds total" and the chain has three links, the third one may get no time at all.
Give each attempt a budget, then put an outer ceiling over the chain so the await
always returns even if an attempt ignores its own timeout.

**A fallback that fired is information.** If a call routed to `CHEAP` because
`GENERATION` was down, the answer's quality profile is different and your "small model
share" metric just moved for a reason that has nothing to do with routing policy.
Measure what actually responded, not what you asked for.

---

## The savings metric, and how it flatters you

If you route cheap work to cheap models, you save money. Quantifying it means answering
"what would this have cost otherwise?"

The honest construction: for each call, price the same work at a **frontier baseline**
model, and report `baseline − actual` summed over real calls. Every input is measured
from real usage.

Three traps live here, and all three are ways the number gets *better* than reality:

1. **Classifying the wrong models as "small."** If a 90-billion-parameter vision model
   is counted as a small model, your small-model share and your savings both jump, in
   your favour, for free.
2. **Counting non-routable calls in a routing metric.** Embeddings and transcriptions
   have exactly one deployment each — routing never chose anything. Including them in
   the denominator of "share of calls that went to a small model" dilutes or inflates a
   metric about a decision that was never made.
3. **Pricing work the baseline cannot do.** A frontier chat model cannot transcribe
   audio. Pricing an audio minute against its token rate produces a fictional number in
   whichever direction the arithmetic happens to fall.

The general rule: **when a metric can only move one way when you get it wrong, assume
you got it wrong until you have checked.**

---

## What you should now be able to explain

- Why a single chokepoint for model calls is a control, not a bottleneck
- Why callers ask for a role and never for a model id
- The difference between a budget enforced before spend and reconciliation after it,
  and why only one of them is a cap
- Why enforcement failing open silently disables every cap
- What the usage ledger is, why it is best-effort, and what that costs you
- Why tokens are not the universal billing unit, and what breaks when you assume so
- Why an unpriceable call must be visibly unpriced rather than quietly $0
- How fallback chains interact with timeouts and with your own metrics
- Three specific ways a cost-savings metric flatters the system that computes it

**Next:** [`10-theory.md`](10-theory.md) — routing theory, cost models and the
published work behind them.
