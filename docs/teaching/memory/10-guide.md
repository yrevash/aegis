# Memory

The part of Aegis that lets an agent know on Tuesday something it was told on Monday.

---

## 1. What it is

Here is a three-turn conversation with a support agent:

```
turn 1   user: my order 88214 hasn't shipped
         bot:  I see it — it's held on a payment check.
turn 2   user: I'm on Premium, shouldn't that be next-day?
         bot:  Premium is next-day once payment clears.
turn 3   user: so when does mine arrive?
```

On turn 3, what does the model receive? Not "the conversation so far". A model call is
**stateless** — it reads the tokens you hand it and has no storage between calls. So the
application sent all four preceding messages again, plus the new one, in one prompt.

**Nothing was remembered. Something was re-sent.** Every "the agent remembered" experience
you have had is an application choosing what to paste into a prompt. Choosing what to paste
is this module.

The naive policy is to keep every turn and re-send the lot. At 200 tokens a turn, by turn
100 you are sending 20,000 tokens and have paid for turn 1 a hundred separate times. Cost
grows with the *square* of the conversation length.

Two other things break. It stops fitting — a model reads a fixed number of tokens, and past
that ceiling something gets dropped whether you chose it or not. And quality gets worse:
models reliably fail to use information buried in the middle of a long context, so padding
the prompt with all 100 turns makes the model *less* likely to use the one line that
mattered.

So more context is not more better, and that reframes the problem: **memory is not a storage
problem, it is a selection problem under a budget.**

---

## 2. How it works in Aegis

### Four kinds of thing to remember

Look at what that conversation contains. *"My order hasn't shipped"* is something that
happened. *"I'm on Premium"* is a durable fact about the person. *"Premium is next-day"* is a
policy the business wrote. The messages in the prompt are a scratchpad.

Those behave differently, so they are stored differently:

| Kind | Holds | Written | Grows |
|---|---|---|---|
| **Working** | What is in this prompt right now | Assembled each turn | No — it is a budget |
| **Episodic** | The turns that actually happened | Every turn | Fast |
| **Semantic** | Distilled facts about a subject | By consolidation | Slowly |
| **Procedural** | Skills and policies | Written by humans | Rarely |

There is also a **profile** — a small card of stable attributes that is always included
rather than searched for.

Store them all the same way and you will serve the wrong one: a similarity search over raw
turns happily returns "customer said thanks" ahead of "customer is on Premium".

### Turning turns into facts

Store only raw turns and memory is just a transcript. Asking "what tier is this customer?"
then means hoping the words *tier* and *Premium* happened to land near each other.

**Consolidation** is a background job that reads recent turns and writes out what they mean:

```
subject: customer_4821    predicate: tier    value: premium
confidence: 0.9           importance: 6
```

Now "what tier?" is a lookup instead of something the model has to work out again.

It runs as two cheap model calls. **Extract** reads the recent turns and proposes facts.
**Reconcile** takes each proposed fact, finds the closest existing ones, and picks `ADD`,
`UPDATE`, `INVALIDATE` or `NOOP`. If a proposed fact is almost identical to an existing one
*and* has the same predicate, it is treated as a duplicate and the second call is skipped.

### Two clocks on every fact

It is July. Customer 4821 says *"I dropped to Standard last week."* You already hold
`tier = premium` from March.

Keep both and recall returns two facts that contradict each other. Overwrite and you can no
longer answer *"on 10 July the agent refused this customer's expedited shipping — was that
reasonable?"*, because you no longer know what the system believed that day.

The fix is to track **two different times**:

- **Valid time** — when the fact was true in the real world.
- **Transaction time** — when we found out.

The customer downgraded on 1 July. We learned on 15 July. So nothing is deleted — the old
row is closed on both clocks and a new row is added:

| Fact | valid_at | invalid_at | created_at | expired_at |
|---|---|---|---|---|
| tier = premium | 1 Mar | **1 Jul** | 1 Mar | **15 Jul** |
| tier = standard | **1 Jul** | — | **15 Jul** | — |

Now both questions have answers from the same table. *"What tier in May?"* — Premium.
*"What did we believe on 10 July?"* — also Premium, because we did not know until the 15th.

A row counts as current when `invalid_at` and `expired_at` are both empty. That single test
is what keeps replaced facts out of recall without destroying them.

One distinction matters: rewording a fact more precisely is an `UPDATE` and closes only
transaction time. An actual change in the world is an `INVALIDATE` and closes both.

### Choosing what to recall

You hold thousands of memories and room for about a dozen. Rank by similarity alone and an
old general chat about delivery times beats *"order 88214 held on payment check"* from twenty
minutes ago.

So candidates are scored on four things and added together:

| Signal | What it measures | Weight |
|---|---|---|
| Relevance | How close the memory is to the question | 1.0 |
| Recency | How recent it is | 0.5 |
| Importance | A 1–10 rating written when the fact was extracted | 0.5 |
| Frequency | How often it has been used | 0.1 |

Recency uses a **half-life** rather than a decay constant, because "facts stay half-relevant
for a month" is something a human can argue with. Facts use 30 days; conversational turns use
3 — a distilled fact ages slowly, a specific turn ages fast.

One trap worth knowing: the four signals are rescaled against each other before being added,
so the best candidate always scores 1.0 for relevance even when nothing in the store is
actually relevant. Never read the composite score as "this is relevant".

### Building the prompt

Recall returns far more than fits, so the assembler fills a token budget in priority order:

```
ctx_token_cap − answer_reserve − the query
```

`answer_reserve` exists because a prompt that fills the whole window leaves the model no room
to reply.

Order is not cosmetic. Models attend reliably to the start and end of a prompt and less so to
the middle, so high-value material goes at the top, bulky material in the middle, and the
recent conversation at the bottom next to the question:

```
profile → facts → skills → summary → episodic → raw turns
```

When it does not fit, the bottom is dropped first, oldest turn first, so the section shrinks
towards the question rather than away from it.

Recalled turns are text a user wrote, so each one is wrapped with a marker telling the model
it is quoted data and not an instruction.

### Where it is stored

Six tables on the shared Aegis ORM base:

| Table | Holds |
|---|---|
| `MemorySession` | The thread — turn count and rolling summary |
| `MemoryMessage` | One row per turn, with its embedding |
| `MemoryFact` | The two-clock facts above |
| `MemoryProfile` | The structured card, one per subject |
| `MemoryWriteLog` | Append-only audit of every change |
| `MemoryConsolidationJob` | The background queue |

Comparing a question against every stored embedding gets slow, so Aegis runs **Chroma
embedded** — a nearest-neighbour index that runs in-process with no server to install.

The rule that matters more than the index choice: **the database is the source of truth and
the index holds only ids.** Every index hit is looked up in SQL, where the tenant and
two-clock filters are applied. So a stale entry in the index costs a wasted lookup, and there
is no path where the index can surface a row that should not exist.

Tenant filtering is never conditional. `if tenant_id is not None: ...` reads as careful
coding and behaves as a leak — with no tenant, no filter is added and the query returns every
tenant's rows. One helper is used everywhere, and `tenant_id=None` means *the null-tenant
scope*, never *any tenant*.

### Forgetting

Memory that only grows gets slower, dearer and worse, because noise crowds out signal.

Aegis archives a fact when it has been invalidated, or when its value has decayed below a
floor **and** it has never been used **and** it is older than a minimum age. Archiving is
soft: the row leaves recall but stays queryable for audit. Forgetting here means "stops being
recalled", never "ceases to exist".

### The domain seam

`aegis.memory` does not know what a "fact" is. A support platform distils tier and shipping
preference; a claims platform distils policy number and incident date.

That vocabulary is injected as a `MemorySpec` — the extraction prompt, the profile fields,
the fact types, and a couple of functions. Point Aegis at a new domain by rewriting that one
module; nothing inside `aegis.memory` changes.

---

## 3. How you use it in code

```python
from aegis.memory import (
    MemoryConfig,
    assemble_working_memory,     # the read path
    consolidate,                 # the write path
    enqueue_consolidation, sweep_pending, prune_forgotten,
    set_default_spec,            # the domain seam
)
```

Importing this pulls SQLAlchemy and nothing heavier — no retrieval stack, no model gateway.

### Reading

```python
assembled = await assemble_working_memory(
    session,
    subject_id="customer_4821",
    session_id=session_id,
    persona="support",
    query="so when does mine arrive?",
    query_vec=query_embedding,   # None falls back to recency-only fact recall
    config=MemoryConfig(),
    tenant_id=7,
)

assembled.text            # the assembled block to paste above the query
assembled.conversation    # surviving raw turns, in OpenAI chat shape
assembled.tokens_used
```

This never calls a model. It is fully deterministic — the running summary is regenerated by
a background job rather than inline.

`conversation` is the field to know about. It is derived from the *assembled* raw section
rather than from the raw turns, so it inherits the token budget for free, then is capped at
the last 12 turns. It exists because the retrieval query rewriter needs conversation history
and runs after `recall_memory` and before `retrieve`.

### Writing

```python
result = await consolidate(
    session,
    subject_id="customer_4821",
    session_id=session_id,
    config=config,
    complete=my_complete,        # injected — offline-testable with fakes
    embed=my_embed,
    tenant_id=7,
)
# result.added / .updated / .invalidated / .noop / .rejected
```

`consolidate` does **not** commit; the caller owns the transaction boundary. `rejected` is a
separate counter from `noop` on purpose — "the model returned garbage" and "there was nothing
to do" must not look the same in your metrics.

In production you do not call it inline. `enqueue_consolidation` inserts a `PENDING` row and
commits synchronously on the request path — that commit is the durability seam, so the job
survives a crash. A sweeper then claims each job with a guarded update
(`SET status='running' WHERE id=:id AND status='pending'`), so `rowcount == 0` means another
sweeper won and this one skips.

```python
await enqueue_consolidation(session, subject_id=..., session_id=..., tenant_id=7)
await sweep_pending(session_factory, config=config, complete=..., embed=...)
```

Errors are caught per job, so one bad job cannot wedge the queue, and each cycle also runs
`prune_forgotten` in its own transaction so a prune failure cannot break consolidation.

### Settings worth changing

| Setting | Default | What it does |
|---|---|---|
| `ctx_token_cap` | `8000` | Hard ceiling on the assembled block |
| `answer_reserve` | `1200` | Tokens held back for the model's answer |
| `raw_window_turns` | `40` | Verbatim recent turns kept in hot recall |
| `n_fact` / `n_epi` | `6` / `4` | How many facts and episodic turns survive |
| `half_life_days_fact` | `30.0` | How fast a distilled fact ages |
| `half_life_days_epi` | `3.0` | How fast a conversational turn ages |
| `consolidation_every_n` | `4` | Turns between background consolidation runs |
| `tau_extract` | `0.55` | Minimum extractor confidence to admit a candidate |
| `dedup_cos` | `0.97` | Same-predicate cosine at which a candidate is a duplicate |
| `w_rel` / `w_rec` / `w_imp` / `w_freq` | `1.0 / 0.5 / 0.5 / 0.1` | The recall composite weights |
| `forget_floor` / `forget_min_age_days` | `0.05` / `90.0` | The archival test |

### When things break

Memory never fails a run. The graph's memory nodes are best-effort: if the store is
unreachable or the embedder fails, the node logs it and the run continues with less memory
or none. Two things deliberately fail loudly instead — an unusable vector-store directory
stops startup rather than silently falling back to memory-only, and a missing cache in full
mode raises rather than pretending to be a cache.

---

## 4. Why it helps us

**Conversations stay coherent without paying quadratic cost.** A fixed budget of the
highest-value material beats re-sending everything, on price and on answer quality at the
same time.

**The system can answer what it believed, not just what is true.** Two clocks on every fact
mean an auditor's question about a decision made last July has an answer, and no correction
ever destroys the record it corrected.

**Recall returns what matters, not just what is similar.** Relevance, recency, importance
and frequency together beat cosine alone, and the half-lives make the ageing policy something
a human can argue with.

**A leak cannot happen by omission.** Tenant scoping is NULL-symmetric everywhere, and SQL —
not the vector index — decides what a subject is allowed to see.

**Point it at a new domain by rewriting one module.** The vocabulary of facts, profiles and
skills is injected, so nothing in the memory core is support-specific.

Without it, every conversation starts from zero, the customer repeats their plan tier in
every session, and the one thing you would want to prove in an audit — what the system knew
and when — is gone.

**Next:** [`40-diagrams.md`](40-diagrams.md)
