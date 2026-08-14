# Memory — interview questions and answers

Claim first, then the reason, then a concrete detail from this system. The detail is
what separates "I read about memory" from "I built it."

---

### "How does your agent remember things between turns?"

It doesn't, in the model sense — the model is stateless, so every call gets a freshly
assembled prompt. What we call memory is a store plus a **selection policy**.

Four tiers. **Episodic** — the actual turns, appended every turn. **Semantic** —
durable facts distilled from those turns by a background consolidation pass.
**Procedural** — skills and policies, authored by humans. **Profile** — a structured
card of stable attributes. At query time we recall from all four and assemble one
working-memory block inside a token budget.

The tiers exist because they behave differently: episodic grows fast and is mostly
noise; semantic is small and dense with signal. Storing them the same way guarantees
you serve the wrong one.

---

### "Why not just send the whole conversation history?"

It fails three ways at once, and the third is the interesting one.

It stops fitting the context window. It costs more every turn — you re-pay for the same
history on turn 51 that you paid for on turn 50. And **quality actively degrades**,
because models lose information buried in the middle of long contexts. Padding the
prompt with everything makes the model *less* likely to use the one line that mattered.

So memory is a selection problem under a budget, not a storage problem.

---

### "How do you handle a fact that changes?"

Bitemporally. Every fact carries two independent time ranges:

**Valid time** — when it was true in the world. **Transaction time** — when we learned
it. A customer downgrades on 1 July and tells us on the 15th; those are different
dates, and conflating them loses information.

Nothing is deleted. When a fact is superseded, we **close** the old one — stamp when it
stopped being valid — and insert the new one as its successor. That keeps two different
questions answerable: *"what was true in May?"* and *"what did we believe on 10 July?"*
The second is the audit question — it's how you show a decision was reasonable given
what was known at the time. If you overwrite, you can never answer it.

---

### "What decides which memories get recalled?"

Three signals, weighted — the formulation from the Generative Agents paper.
**Relevance** (cosine similarity to the query), **recency** (with exponential decay),
and **importance** (how intrinsically significant the memory is).

Similarity alone is not enough. Something said this morning usually matters more than
the same sentence in March, and "customer threatened to cancel" should outlive
"customer said thanks."

**The trap worth mentioning:** if you min-max normalise those signals across the
candidate set, you destroy absolute relevance. A candidate set whose best match is a
cosine of 0.15 still yields a top-ranked memory scored 1.0 — nothing was relevant and
the ranking says otherwise. You need an absolute similarity floor *before* normalising,
and recalling nothing has to be an acceptable outcome.

---

### "How do you stop memory growing forever?"

Consolidation plus forgetting. Consolidation distils many turns into few facts, so the
dense tier stays small. A forget sweep prunes what is never used.

**The failure mode to name:** if you score by raw access *count*, memories entrench —
recalled once, so ranked higher, so recalled again. Rich-get-richer, and new memories
can't break in. Worse, if the prune only touches never-accessed rows, a memory recalled
exactly once becomes permanently immune to forgetting. Decaying *access recency*
behaves properly where a monotonic counter does not.

---

### "Walk me through consolidation."

Background job, off the request path. It loads the session's recent turns, has a cheap
model extract candidate durable facts, embeds each one, finds its nearest existing
facts for that subject, and asks a model to decide the operation: **add**, **noop**,
**update**, or **invalidate**.

Add inserts. Noop drops a duplicate. Update and invalidate supersede an existing fact
bitemporally.

**The bug we found here is the best story in this module.** The decide step returns
*which* fact to supersede, by id. If the model invents an id that doesn't exist, the
original code fell back to the **nearest neighbour** — so a hallucinated id silently
invalidated an *unrelated* memory and recorded it as a legitimate contradiction.
Concretely: extracting "tier is gold" could invalidate "prefers email for support."
Bitemporal history is then permanently wrong, and the write log claims it was
intentional.

A hallucinated id is a model failure, not a hint. It's now refused outright and
surfaced as its own count, distinct from a genuine no-op — because "the model returned
garbage" and "there was nothing to do" must not look the same in your metrics.

---

### "How do you assemble the prompt from all this?"

Order and budget. We concatenate the tiers in a fixed order and count tokens; if we're
over, we evict by policy until we fit.

Order matters because of *lost in the middle* — the start and end of a prompt get the
most attention, so the most valuable material goes at the edges rather than buried.

Eviction order is a real design decision and the intuitive answer is often wrong. It's
tempting to shed the verbatim recent turns first because they're "recoverable" — but
those turns sit closest to the query and carry the most immediate context. Shedding
them to keep a three-week-old fragment is usually the wrong trade.

---

### "How do you keep one tenant's memories away from another's?"

Every read is scoped, at two layers: an application filter and Postgres row-level
security so the database enforces it even if a query forgets.

This matters more in memory than almost anywhere else, because a leaked memory doesn't
just appear in an API response — it's **pasted into a prompt and paraphrased back to a
stranger** in fluent prose. That's a worse failure mode than a normal data leak.

**The bug pattern to name:** filtering *only when* a tenant is supplied — `if tenant_id
is not None: add the filter`. It reads as defensive coding and behaves as a leak,
because an unscoped call then matches every tenant's rows. The correct form is
symmetric: a null tenant matches null-tenant rows only. We found this on the profile
read and then discovered the same pattern on the facts tier, the raw window, episodic
recall, the session lookup and the vector-search join — so fixing only the first one
would have been an incomplete security fix.

---

### "How does memory work across sessions?"

Sessions scope *episodic* recall — the raw window is this conversation's turns. But
**semantic facts and the profile are scoped to the subject, not the session**, so what
was learned in March is available in a brand-new conversation in July.

That's the whole point of consolidation: it promotes information out of the session
that produced it. Episodic memory is per-conversation; semantic memory is per-person.

---

### "What's the hardest part of this?"

Two things.

**Knowing what to forget.** Every other part has a right answer you can test. Eviction
and importance weighting are judgement calls, and getting them wrong degrades quality
in a way that's hard to see — the system still answers, just slightly worse, forever.

**Trusting a model to mutate durable state.** Consolidation has a language model
deciding to invalidate stored facts. Every guard rail on that path exists because the
model *will* eventually return something wrong, and the difference between a good and
bad system is whether that produces a refusal or a silent corruption.

---

### "How would you test this?"

Three levels.

Unit-test the scoring maths directly — including the degenerate cases, like a
single-candidate set (where min-max normalisation produces a divide-by-zero or a
meaningless 0.0) and an all-identical-similarity set.

Integration-test the bitemporal invariants: after a supersession, assert the old fact is
*closed and still present*, that a query "as of" the earlier date returns the old value,
and that the successor links back.

And test the **failure directions explicitly**: a hallucinated target id must write
nothing; an unscoped read must not cross tenants; an embedder returning fewer vectors
than candidates must not silently produce unsearchable facts.

That last one is a real bug class — pad the embedding list with `None` and you get
facts inserted with no embedding, which no similarity search will ever return. They're
stored, they're not findable, and nothing errors.
