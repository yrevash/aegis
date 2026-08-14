# Memory — the theory

The algorithms and the published work the design draws on, plus the alternatives that
were rejected and why. If `00-concepts.md` told you *what* the problem is, this tells
you *how* people have solved it and which trade-off Aegis took.

---

## 1. The recall score: the Generative Agents composite

The formulation comes from **Park et al., "Generative Agents: Interactive Simulacra of
Human Behavior" (Stanford/Google, 2023)**. Their agents had thousands of observations
and a small prompt, so they needed a *retrieval function* over memory, not just a vector
search. They proposed:

```
score(m) = α_rel · relevance(m) + α_rec · recency(m) + α_imp · importance(m)
```

Aegis adds a fourth term, **frequency** (how often a memory has actually been recalled),
giving four:

```
score(m) = w_rel·rel + w_rec·rec + w_imp·imp + w_freq·freq
```

### relevance

Cosine similarity between the query embedding and the memory embedding:

```
cos(a, b) = (a · b) / (‖a‖ · ‖b‖)
```

Range for text embeddings is effectively `[0, 1]`. It is the only term that knows what
the user asked.

### recency — exponential decay, parameterised by half-life

```
recency(age_days) = 0.5 ^ (age_days / half_life_days)
```

Age 0 → 1.0. Age exactly one half-life → 0.5. Two half-lives → 0.25.

**Why half-life rather than a decay rate `λ`?** The two are equivalent —
`0.5^(t/h) = e^(−t·ln2/h)` — but a half-life is a number a human can reason about
("facts stay half-relevant for a month") and `λ = 0.0231` is not. That matters when the
parameter is a config knob someone has to tune. Aegis uses **30 days for facts** and
**3 days for episodic turns**, which encodes a real claim: a distilled fact ages slowly,
a specific conversational turn ages fast.

**Why exponential and not linear?** Linear decay hits zero and stays there, so a memory
older than the window is *gone* rather than *quiet*. Exponential decay is always
positive, so an extremely relevant old memory can still win if `w_rel` is high enough.
That is the correct behaviour: "you told me in March your account was compromised" should
still be recallable in December.

### importance (poignancy)

Generative Agents asked a language model to rate each observation 1–10 for "poignancy".
Aegis takes the same 1–10 scale but the rating comes from the **extraction** step, where
a model is already reading the turn (see `20-in-aegis.md`), so it costs nothing extra.

### frequency

`log1p(access_count)` — the log matters. A raw count lets a memory recalled 400 times
dominate everything; `log1p` compresses that so the 400th recall is worth much less than
the first. This is the same intuition as TF saturation in BM25.

### The normalisation trap, stated formally

Every component is **min-max normalised across the candidate set** before weighting:

```
norm(v_i) = (v_i − min(v)) / (max(v) − min(v))
```

This makes the four incomparable scales (a cosine, a decay in (0,1], an integer 1–10, a
log count) addable. It also **destroys absolute magnitude**: the best candidate maps to
1.0 whether its cosine was 0.95 or 0.05.

Two degenerate cases follow directly from the formula, and both are real bugs waiting
to happen:

- **Constant column** (`max == min`): the denominator is 0. Aegis maps a constant column
  to **all zeros**, not all ones. Mapping to 1.0 would let a signal that carries *no
  discriminating information* contribute its full weight to every candidate.
- **Single candidate**: `max == min` trivially, so every component is 0 and the score is
  0. The ranking is still correct (there is only one), but any code that thresholds on
  the composite score must not read that 0 as "irrelevant".

The mitigation is an **absolute floor before normalising**. Aegis's floor is structural
rather than a threshold constant: the ANN search returns only what the vector index
considers near, and the `k`/`n` fan-out (`k_fact=20 → n_fact=6`) bounds how much survives.
Understand the trap anyway — it is the single most-asked question about this scoring
scheme.

---

## 2. Consolidation: the mem0 two-phase pattern

**mem0** (2024) is the reference implementation of "extract then reconcile":

1. **EXTRACT** — a cheap model reads the recent turns and emits candidate facts.
2. **RECONCILE** — for each candidate, find its nearest existing facts and ask a model
   to choose one operation: **ADD**, **UPDATE**, **DELETE**, or **NOOP**.

Aegis keeps the two phases and renames DELETE to **INVALIDATE**, because nothing is ever
deleted (see bitemporality below).

### Why two phases rather than one

A single call ("here are the turns and the existing facts, output the new fact set")
looks simpler and is much worse:

- The prompt grows with the *whole* fact set, so cost grows with memory size.
- The model rewrites facts it was not asked about, so unrelated memories drift.
- There is no per-fact audit trail — you cannot say *why* a specific fact changed.

Two phases keep each call small and give you one write-log row per decision.

### The dedup short-circuit

Before the second model call, Aegis checks: is the nearest existing fact's cosine
`>= dedup_cos` (0.97) **and** does it share the same predicate? If so, the candidate is a
duplicate — record a NOOP, bump the access count, and skip the LLM call entirely.

This is a cost optimisation with a correctness argument: at 0.97 cosine with an identical
predicate, there is no operation a model could sensibly propose other than "nothing".
Note both conditions are required. Cosine alone is not enough — `tier = gold` and
`tier = silver` embed very close together and are a *contradiction*, not a duplicate.

---

## 3. Bitemporal modelling

The idea is older than LLMs — it comes from temporal-database research, canonically
**Richard Snodgrass, "Developing Time-Oriented Database Applications in SQL" (1999)** —
and was brought into the agent-memory world by **Zep / Graphiti** (2024).

Every fact carries two independent intervals:

| Axis | Columns in Aegis | Answers |
|---|---|---|
| **Valid time** (world time) | `valid_at` → `invalid_at` | "When was this true?" |
| **Transaction time** (system time) | `created_at` → `expired_at` | "When did we believe it?" |

A row is **currently valid** iff `invalid_at IS NULL AND expired_at IS NULL`. That single
predicate is the hot-recall filter.

Two operations, and the distinction is load-bearing:

- **Refinement (UPDATE)** — the value did not change, the wording got better. Close the
  old row in *transaction* time only (`expired_at = now`) and insert a successor. World
  time is untouched, because the world did not change: we just know it better now.
- **Contradiction (INVALIDATE)** — the value changed. Close the old row in *both* axes
  (`invalid_at` = when it stopped being true, `expired_at` = now) and insert the new fact.

The successor carries `supersedes_id` pointing at the row it replaced, so the belief
timeline is a linked list you can walk.

### Why this beats the alternatives

| Alternative | What breaks |
|---|---|
| Overwrite in place | "What did we believe on 10 July?" is unanswerable. Disqualifying for anything audited. |
| Soft-delete flag | One axis only. You can say "no longer believed" but not "stopped being true on 1 July, learned on the 15th". |
| Append-only with no closure | Every historical fact stays in hot recall, so the model sees both "premium" and "standard" and picks arbitrarily. |
| Event sourcing (rebuild state from a log) | Correct, but every read replays the log. Bitemporal rows *are* the log and the state. |

---

## 4. Working memory: the assembly problem

At query time all the recalled material must become one block within a token budget.
Formally this is a **knapsack**: each item has a token cost and a value (its composite
score), and you maximise value subject to a budget.

Exact knapsack is unnecessary here. Aegis uses a **greedy fill in priority order with
per-tier caps**, which is O(n) and produces near-optimal results because the items are
already sorted by value within each tier. The relevant literature note: greedy by
value-density is a 1/2-approximation for 0/1 knapsack, and the gap is far below the noise
floor of "does the model use this line".

### Lost in the middle

**Liu et al., "Lost in the Middle: How Language Models Use Long Contexts" (2023)** showed
that retrieval accuracy against a long context follows a **U-shape**: material at the
start and the end of the prompt is used reliably, material in the middle is often missed —
and the effect is large enough to swamp a retrieval improvement.

That result determines the layout directly. High-value, low-volume material goes at the
top (profile, distilled facts, skills). Bulky, lossy material goes in the tolerant middle
(recalled earlier turns). The verbatim recent conversation goes at the bottom, nearest the
query that will be appended after it.

### Eviction order is a design decision, not an implementation detail

When the budget is exceeded, something must go. The intuitive choice — shed the recent
raw turns first because "they're recoverable" — is usually wrong: those turns sit closest
to the query and carry the most immediate context. Aegis sheds from the bottom
(`raw` → `episodic` → `summary` → `skills` → `facts` → `profile`) but evicts the *oldest*
raw turn first rather than the newest, so the raw tier shrinks toward the query rather
than away from it.

---

## 5. Semantic caching

A **semantic cache** keys on meaning rather than on an exact string: embed the query,
find the nearest cached entry, serve it if the cosine clears a threshold.

Two parameters decide whether it is safe:

- **Threshold.** Aegis uses a cosine **distance** threshold of 0.05, i.e. similarity
  ≥ 0.95. That is deliberately near-identity. A loose threshold turns a cache into a
  wrong-answer generator: "what is our refund window for EU customers" and "…for US
  customers" are extremely close in embedding space and have different answers.
- **Scope.** The cache key must include the tenant and the subject. A cache that is
  keyed on the query text alone is a cross-tenant leak with extra steps.

**The consistency contract.** The durable SQL rows are authoritative; the cache is
derived. Any write to a subject's facts must invalidate that subject's cache entries, or
the cache serves pre-write memory for the whole TTL. This is the classic cache-invalidation
problem and it has exactly one correct ordering: **commit the authoritative write first,
then invalidate**. Invalidating first leaves a window in which a concurrent read repopulates
the cache from the *old* rows.

---

## 6. Forgetting

Memory that only grows gets slower, more expensive, and worse. But eviction policies have
distinct failure modes, and picking the wrong one is subtle:

| Policy | Failure mode |
|---|---|
| **LRU** (least recently used) | Fine for caches. Wrong for memory: an important fact nobody asked about this month is not garbage. |
| **LFU** (least frequently used) | **Rich-get-richer.** Recalled once → ranks higher → recalled again. New memories can never break in. Worse: if the prune only removes never-accessed rows, a memory recalled exactly once becomes permanently immune. |
| **TTL** | Facts do not have uniform lifetimes. A password reset is stale in a week; a legal name is not. |
| **Decayed value** | Combines age with intrinsic worth. This is what Aegis uses. |

Aegis's archival test is:

```
archivable  ⟺  invalidated
            ∨  (confidence · 0.5^(age/half_life) < forget_floor
                ∧ access_count == 0
                ∧ age > forget_min_age_days)
```

Note it is **soft archival**, not deletion: the row gets `expired_at = now`, so it leaves
hot recall but stays queryable for audit — exactly like a supersession. "Forgetting" in
this system means "stops being recalled", never "ceases to exist".

---

## 7. Vector search: why ANN, and where the source of truth lives

Comparing a query against every stored embedding is O(n·d) — for 3072 dimensions and
100k memories that is 300M float operations per recall. **Approximate nearest neighbour**
indexes (Aegis uses Qdrant, which implements **HNSW** — Malkov & Yashunin, 2016) get this
to roughly O(log n) by descending a layered proximity graph.

The architectural decision that matters more than the index choice: **the relational row
is the source of truth and the vector index only holds ids**. Every ANN hit is joined back
to SQL, where the bitemporal predicate (`invalid_at IS NULL AND expired_at IS NULL`) and
the tenant predicate are applied.

Why not mirror validity into the index? Because then two systems hold the same truth and
can desync — an invalidated fact that failed to update in Qdrant would keep surfacing. By
keeping the index dumb ("which of this subject's rows are near?") and letting SQL decide
eligibility, a stale index point can cost you a wasted fetch but can never surface a row
that should not exist.

This has one consequence you must handle: SQL will drop some of the ANN hits, so the ANN
search must **over-fetch**. Aegis asks for `k*4 + 16` when a validity or predicate filter
will run.

---

## 8. Where each idea came from

| Idea | Source |
|---|---|
| Relevance + recency + importance composite | Park et al., *Generative Agents* (2023) |
| Extract → reconcile with ADD/UPDATE/DELETE/NOOP | mem0 (2024) |
| Bitemporal fact versioning for agent memory | Zep / Graphiti (2024); Snodgrass (1999) for the underlying model |
| Lost-in-the-middle prompt ordering | Liu et al. (2023) |
| Virtual context / paging metaphor | MemGPT / Letta (2023) |
| HNSW ANN index | Malkov & Yashunin (2016) |
| RRF for fusing incomparable ranked lists | Cormack et al. (2009) |
| Datamarking / delimiting untrusted context | Microsoft *Spotlighting*, arXiv 2403.14720 |

**Next:** [`20-in-aegis.md`](20-in-aegis.md) — how all of this is actually built here.
