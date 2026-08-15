# Memory — the deep dive

Consistency, concurrency, tenant isolation, failure modes — and the bugs actually found
and fixed in this codebase, each told as a story. The bugs are the most valuable thing in
this file. Learn one well enough to tell it.

---

## Part 1 — The properties the design has to hold

### Consistency: two stores, one truth

Memory writes to three places: SQL rows (authoritative), a vector-store collection (derived
index), and a semantic cache (derived answer store). Only the first is truth.

**SQL ← → vector store.** The index holds ids and scope, never validity. Every ANN hit is joined
back to SQL with the same subject/tenant predicates plus `valid_only`/`predicate`
(`aegis/src/aegis/memory/vector_ops.py:255-269`). Consequences:

- A **stale point** (a row invalidated after it was mirrored) costs a wasted fetch. It can
  never surface, because SQL drops it.
- A **missing point** (a row written but not yet mirrored) is a recall miss until the next
  sync. Bounded, because `_sync_subject` runs on every search.
- There is no path where the index can surface a row that should not exist. That
  asymmetry is the whole point of making the index dumb.

**SQL ← → cache.** Ordering is fixed in `stream_add`
(`aegis/src/aegis/memory/stream.py:225-249`): consolidate → `session.commit()` → invalidate.
Committing first means a concurrent read that repopulates the cache reads post-write rows.
Invalidating first would leave a window where a read repopulates from pre-write rows and
the stale entry then survives the whole TTL.

### The high-water mark, and why it is sound

`_sync_subject` (`vector_ops.py:116`) only reads rows with `id > watermark`. That is a
correctness claim, not just an optimisation, and it rests on one property: **the memory
tables are append-only in the embedding**.

- A fact is never re-embedded in place. A refinement or contradiction *inserts a
  superseding row*; invalidation only writes the bitemporal columns.
- Messages are immutable once written.

Because ids are monotonic, rows another process wrote after this one's last sync still sit
above the mark and get picked up on the next call (`vector_ops.py:135-138`). The mark
advances past every row *considered*, including dimension-mismatched ones
(`vector_ops.py:180-182`) — otherwise a 256-dim row in a 3072-dim scope would be re-read
forever.

**If you ever add in-place embedding mutation, this breaks silently.** That is the kind of
invariant worth saying out loud in an interview.

### Concurrency: three distinct races and how each is handled

**1. Two consolidators superseding the same fact.** Handled by the guarded UPDATE
(`consolidate.py:439-451`): the `WHERE` clause includes `invalid_at IS NULL AND
expired_at IS NULL`, so exactly one writer's `rowcount` is 1. The loser gets 0, counts a
`noop`, returns `False`, and — critically — its candidate is *not* added to `applied`, so
it cannot move the profile either.

**2. Two sweepers claiming the same job.** Handled by the guarded claim
(`consolidate.py:997-1010`): `UPDATE ... WHERE id = ? AND status = 'pending'`. `rowcount == 0`
means the race was lost; the sweeper skips that job and continues.

**3. The recall-path access bump.** `_bump_recall_access` (`recall.py:322`) issues two
bulk `UPDATE ... SET access_count = access_count + 1` statements. The increment happens in
the database, not read-modify-write in Python, so concurrent recalls of the same fact both
count.

### Transaction boundaries — who commits what

| Function | Commits? |
|---|---|
| `recall()` | **Yes**, but only the access bumps (`recall.py:366`), and only because nothing else shares that session on the hot path |
| `consolidate()` | **No** — documented at `consolidate.py:842-843`; the caller owns the boundary |
| `enqueue_consolidation()` | **Yes** (`consolidate.py:807`) — it is the durability seam |
| `prune_forgotten()` | **No** |
| `sweep_pending()` | **Yes**, per job, and separately for the prune |
| `forget_fact()` | **No** — `stream_forget` commits |

The rule: pure logic never commits; the durable seams do.

### Tenant isolation

Two layers. The **application filter** is primary because it is NULL-safe and works
identically on SQLite and Postgres. Postgres RLS is an additive belt a host wires itself
(`stores.py:11-15`).

The predicate is single-sourced in two places that must agree — `recall._tenant_clause`
(`recall.py:45`) for reads and `consolidate._tenant_clause` (`consolidate.py:148`) for
writes — and the write-side docstring explicitly says it mirrors the read side, because a
read and a write that disagree about what "no tenant" means is a leak in one direction and
a lost write in the other.

**One asymmetry survives.** `crud.py` still uses the conditional form:

```python
# aegis/src/aegis/memory/crud.py:55-56, 74-75
if tenant_id is not None:
    stmt = stmt.where(MemoryFact.tenant_id == tenant_id)
```

This is the explicit operator/CRUD path, always scoped by a required `subject_id`, and it
does not feed a prompt. It is still the pattern that caused the leak on the recall path,
and if you are asked "is there anywhere this is not fixed", this is the honest answer.

### Failure modes and the degradation ladder

| Failure | Behaviour |
|---|---|
| No `query_vec` | Facts fall back to `ORDER BY valid_at DESC` (`recall.py:161-182`). Recall still serves — but semantic recall is no longer semantic |
| Embedder raises | `MemoryDeps._embed_query` logs and returns `None` (`backend/src/app/agent/deps.py:247-254`) → the recency ladder above |
| Vector store directory unusable | Construction fails loud at startup (`main.py`'s lifespan). Not a silent RAM fallback |
| Whole memory store unreachable | `recall_memory` catches, logs, returns `{}` (`graph.py:661-664`). The run continues with no memory |
| Persist fails | Logged, never raised (`graph.py:709-710`). The stream still finishes cleanly |
| Extractor returns unparseable JSON | `_extract_candidates` returns `[]` (`consolidate.py:305-308`) — no candidates, no writes |
| Decide-op unparseable | Defaults to `noop` (`consolidate.py:347-349`) — the safe direction |
| Consolidation job raises | Rolled back, job marked `ERROR` with the message (`consolidate.py:1024-1032`); the sweep continues |
| Prune raises | Rolled back in its own try (`consolidate.py:1046-1051`); consolidation is unaffected |
| Redis cache down (full mode) | `from_config(require_redis=True)` raises rather than silently degrading (`cache.py:441-448`) |

Note the deliberate split: **safety-relevant failures fail closed and loud** (the vector store at
boot, Redis in full mode), **quality-relevant failures degrade and say so** (no vector →
recency).

---

## Part 2 — The bugs

Every one of these was found by an adversarial audit of this codebase, reproduced before
being changed, and fixed with a regression test that failed on the pre-fix code. The
commits are `c5db31b` and `7d3c436`.

---

### Bug 1 — A hallucinated id corrupted an unrelated memory, and the audit log said it was intentional

**What it was.** The reconcile step asks a cheap model which existing fact to supersede,
by id. The model sometimes returns an id it was never shown. The original code fell back
to the **cosine-nearest neighbour**.

**Why it mattered.** Extracting "tier is gold" could invalidate "prefers email for
support". The write-log row recorded a legitimate `INVALIDATE` with a plausible reason.
Bitemporal history — the thing whose entire value proposition is that it is a truthful
audit trail — was now permanently wrong, and nothing anywhere said so. This is the worst
class of bug in the system: a silent, durable, *audited* corruption.

**Why the fallback looked reasonable.** "The model meant *something*; the nearest fact is
probably what it meant" is the kind of helpful repair that reads as robustness. It is not.
A hallucinated id is a **model failure**, and repairing a model failure by guessing turns
one wrong answer into a permanent wrong record.

**The fix.** `_resolve_target` (`consolidate.py:159-197`) resolves only against the
neighbours the model was actually shown. Anything else is refused with a reason naming the
failure, a `NOOP` audit row is written carrying that reason, and `ConsolidationResult.rejected`
is incremented (`consolidate.py:631-649`). `rejected` is a *separate counter* from `noop`
because "the model returned garbage" and "there was nothing to do" must not be
indistinguishable in metrics.

One nuance worth quoting: an *omitted* `target_id` is defaulted only when there is exactly
one neighbour, where the referent is unambiguous. With several plausible neighbours, an
omitted id is exactly as unresolvable as an invented one and is refused the same way.

---

### Bug 2 — `if tenant_id is not None` was a cross-tenant leak on six read paths

**What it was.** Every tenant-scoped query used the conditional form:

```python
if tenant_id is not None:
    stmt = stmt.where(Model.tenant_id == tenant_id)
```

**Why it mattered.** With `tenant_id=None`, no predicate is emitted, so the query matches
**every tenant's rows**. Whether that returns another tenant's data depends only on whether
a `subject_id` collides across scopes — and subject ids are things like user ids, which
absolutely do collide across tenants in a shared deployment.

And memory is the worst place for this. A leaked memory does not appear as a row in a JSON
response that someone might notice. It is **pasted into a prompt and paraphrased back to a
stranger in fluent prose**.

**The scope of it.** The audit found it on the profile read. On inspection the same pattern
was on the **facts tier**, the **raw window**, **episodic recall**, the **session lookup**
and the **vector-search SQL join**. Fixing only the first would have been an incomplete
security fix that looked complete.

**The fix.** A single NULL-symmetric helper, `_tenant_clause` (`recall.py:45-53`), used by
every read, mirrored on the write side (`consolidate.py:148-156`). `tenant_id=None` now
means `tenant_id IS NULL` — the null-tenant scope — not a wildcard. The docstring says
explicitly that it is single-sourced "so no recall query can drift back to the leaky form".

**The lesson to state.** The conditional form *reads* as defensive coding. It looks like
you thought about the None case. It behaves as a leak. Any time a security predicate is
optional, ask what the absent case matches.

---

### Bug 3 — Cache invalidation never worked, and could not have

**What it was.** `MemorySemanticCache.invalidate()` on the RedisVL backend enumerated a
subject's entries by calling `acheck` — the cache's *vector search* — with a placeholder
vector.

**Why it mattered.** `acheck` is a KNN range query. Two things go wrong:

1. It **validates the probe against the index dimensionality**. A placeholder `[0.0]`
   raises `ValueError` on any real 1536- or 3072-dim cache. Verified against redisvl —
   it raises, it does not return 0.
2. Even correctly sized, a range query only returns entries inside a cosine radius. Entries
   outside it survive.

Either way, a subject's cache entries survived invalidation and kept serving **pre-write
memory for the whole TTL** (900 seconds by default). The module docstring calls invalidation
a MUST for the consistency contract; the implementation could not honour it.

**The fix.** `invalidate()` now issues a RedisVL **`FilterQuery`** over the `subject_id` and
`tenant_id` tag fields (`cache.py:332-345`). A filter query carries no vector at all, so it
returns the scope exactly. Any Redis error **propagates** — a failed invalidation must be
visible, never a silent `0`. The synchronous `SearchIndex.query` round-trip runs under
`asyncio.to_thread` to stay off the event loop.

**The generalisable lesson.** A method whose failure mode is "returns fewer results than it
should" is a terrible fit for a delete-everything-in-this-scope operation. Match the query
shape to the semantics you need, not to whatever API is nearest.

---

### Bug 4 — The profile was updated from candidates that were never written

**What it was.** `_update_profile` was fed the raw extractor output.

**Why it mattered.** Three ways a candidate can fail to reach the store: the reconcile step
rules it a `noop`, the decide-op is refused for a hallucinated id, or the concurrency guard
finds the row already moved. In all three cases **no fact was written** — yet the candidate
still rewrote the structured profile. The profile is the always-injected "human block" at
the very top of the prompt, so it then disagreed with the bitemporal facts it is supposed
to summarise, and it disagreed *at the position the model attends to most*.

**The fix.** `_reconcile` now **returns the applied candidates** — ADD, applied UPDATE,
applied INVALIDATE only (`consolidate.py:586-593`) — and `consolidate` passes that list to
`_update_profile` (`consolidate.py:884-892`). The concurrency-guard functions return `bool`
specifically so the caller can tell an applied write from a lost race
(`consolidate.py:434-437`).

A second, smaller correctness fix rides along: within a batch, several applied facts can map
to the same profile field. They are merged in **ascending confidence** (`consolidate.py:760`)
so the most confident value wins, rather than whichever happened to sit last in the
extractor's list. Equal confidences fall back to application order, because `sorted` is
stable.

---

### Bug 5 — Episodic "hybrid RRF" fused a list that was guaranteed to be discarded

**What it was.** Episodic recall claims to be hybrid: fuse a **recency** list and a
**vector** list with RRF. The recency list was built from the **raw window** — the last 40
turns of this session.

**Why it mattered.** Immediately after fusion there is a dedup filter that drops anything
already in the raw window (`recall.py:279-280`), because those turns are already injected
verbatim in the bottom tier. So *every member of the recency list was guaranteed to be
dropped*. RRF ranked a set of items that could not survive, and the surviving order
collapsed to **pure vector rank**.

Two real consequences: the recency signal never reached the output at all, and a
recent-but-unembedded turn could never be recalled by any path.

This is a particularly instructive bug because nothing errors, nothing logs, and the code
reads exactly like a working hybrid retriever. The provenance would have said "fused two
lists" and been arithmetically true and substantively false.

**The fix.** The recency list is now drawn from the turns **outside** the raw window — the
subject's newest turns that the raw window does not already carry, which is exactly the
population episodic recall is allowed to contribute from
(`recall.py:238-249`, `.not_in(raw_ids)`). That is what makes the fusion genuinely hybrid.

One honest wrinkle recorded in the code: the recency list is tagged `RetrievalOrigin.BM25`
because the enum has no "recency" member (`recall.py:225-227`). The docstring calls it "a
known label compromise, not a second signal" rather than letting a reader assume BM25 ran.

---

### Bug 6 — Every ANN search re-read and re-indexed the subject's entire memory

**What it was.** Each call to `search_rows` ran a full `SELECT` of the subject's embedded
rows and a full re-upsert into the vector store, then searched.

**Why it mattered.** Consolidation calls the ANN search **once per candidate**. For an
8-candidate batch that is eight full scans and eight full re-index passes. The "real vector
index" was therefore *strictly more expensive* than the in-Python cosine loop it had
replaced — a performance regression dressed as an upgrade.

**The fix.** The per-scope high-water mark (`vector_ops.py:145-146`), with the append-only
argument spelled out in the docstring (`vector_ops.py:135-143`) so a future reader knows
exactly which invariant the optimisation depends on. In the same pass, every synchronous
vector-store call moved under `asyncio.to_thread` (`vector_ops.py:178-179, 236`) — a
server-mode search is a network round-trip and it was blocking the event loop on the hot
recall path.

---

### Bug 7 — The query rewriter got no conversation history, and the deeper reason why

This one belongs to retrieval but its **fix lives in memory**, so it is worth telling here.

**What it was.** The pre-retrieval query rewriter exists to resolve pronouns and ellipsis
("what about *its* refund window?") against the conversation. On the graph path it was
handed `state["messages"]`, which was always empty.

**The deeper reason.** `messages` is a **per-planning-round scratch buffer written by the
`plan` node**, and `plan` runs *after* `retrieve`. There is no ordering of the graph in
which `messages` could be populated at rewrite time. The rewriter was structurally unable
to do its job, and there is no amount of debugging the rewriter that would have found it —
the bug is in the data flow, not the function.

**The fix, in memory.** `AssembledMemory` gained a `conversation` field
(`working.py:72-77`) — the surviving raw turns in OpenAI chat shape. `recall_memory` writes
it into `state["conversation"]` (`graph.py:672-683`), and `retrieve` reads
`state.get("conversation") or state.get("messages") or None` (`graph.py:528`), keeping
`messages` as the fallback so the single-shot path is byte-identical.

Two design decisions in that field are worth naming:

- It is derived from the **assembled** raw section, not from `raw_turns` directly
  (`working.py:276-292`), so it inherits the token budget for free. A turn the budget
  evicted is not exposed here either.
- It is capped at **12 turns** and filtered to user/assistant roles
  (`working.py:47-58`). A 40-turn window is far more than a rewriter needs to resolve a
  pronoun, and it would be re-sent every turn.

---

### Bug 8 — Both memory branches always passed `query_vec=None`

**What it was.** `recall_memory` runs **upstream** of `retrieve`, and `retrieve` is the
only node that sets `state["query_vec"]`. `answer_memory` sits on a branch that never
reaches `retrieve` at all. So both memory branches always called `assemble(query_vec=None)`.

**Why it mattered.** With no query vector, `_recall_facts` takes the recency-only fallback
(`recall.py:161-182`). Semantic recall silently became "the six newest facts" — which
*looks* like it is working, returns plausible content, and is not semantic at all.

**The fix, in two layers.** `_recall_vector` (`graph.py:1278-1300`) prefers a vector already
in state and otherwise calls the injected `deps.embed_query` hook, wired host-side to the
gateway embedder (`backend/src/app/agent/deps.py:467-483`). And `MemoryDeps.assemble` keeps
its own fallback (`deps.py:119-124`). That is defence in depth, not duplicated work: exactly
one embedding is computed either way, because `assemble` skips its own call when a vector
arrives.

---

### Bug 9 — Padding the embedding list with `None` created unfindable facts

**What it was.** The batched embedder can return fewer vectors than candidates. The code
pads:

```python
# consolidate.py:869-871
embeddings = list(raw_embeddings) + [None] * (len(candidates) - len(raw_embeddings))
```

**Why it mattered.** A fact inserted with `embedding=None` is never mirrored into the vector store
(`_sync_subject` filters on `embedding.is_not(None)`, `vector_ops.py:150`), so **no
similarity search will ever return it**. It is stored, it is not findable, and nothing
errors. It will surface only via the recency fallback, and only until six newer facts exist.

**Where it stands.** The padding is retained deliberately — dropping the candidate entirely
would be worse — but the trade is now documented at `consolidate.py:864-868` alongside the
related mixed-dimension case. The generalisable point for an interview: **"stored but
unfindable" is a real and under-appreciated failure class in vector systems**, and it is
exactly the sort of thing to write an explicit test for.

---

### Bug 10 — Background consolidation left the job PENDING forever

**What it was.** `persist_memory` enqueues a durable `PENDING` job and then fires a
background task. That task originally called `consolidate` directly.

**Why it mattered.** `consolidate` does not touch the job row. So the job stayed `PENDING`,
and the interval sweeper picked it up later and consolidated the *same session again* — a
duplicate extract, decide and summary pass, i.e. duplicate model spend and a second chance
to write conflicting facts.

**The fix.** `_run_consolidation` calls **`sweep_pending`** instead
(`backend/src/app/agent/deps.py:256-278`), which claims the job it just enqueued
(`PENDING → RUNNING`), consolidates, and marks it `DONE`. The docstring says exactly why.

---

## Part 3 — Things worth noticing that are not bugs

**The commit inside `recall()`.** It looks alarming for a read path. It is justified in the
docstring (`recall.py:333-339`) and the justification is conditional: it is safe *because*
nothing else shares that session on the hot path. If someone later reuses that session, the
justification evaporates. Assumptions that are only true given a caller's behaviour deserve
to be written down, and this one is.

**`per_tier_caps` sums to 1.25.** Deliberate (`config.py:34-37`). They are independent
ceilings in a priority-ordered greedy fill, not a partition. If they summed to exactly 1.0,
a turn with no profile and no skills would leave 20% of the budget permanently unusable.

**The eviction loop, not a single eviction.** `working.py:253` loops because dropping one
item may still leave you over budget once separators are counted. Textbook off-by-one
territory.

**`get_default_spec` raises rather than returning a stub.** `spec.py:104-116`. A memory
subsystem with no domain contract cannot extract facts. Returning a no-op spec would give
you a system that runs, consolidates nothing, and reports success.

**Next:** [`40-diagrams.md`](40-diagrams.md) — all of this, drawn.
