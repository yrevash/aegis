# Retrieval — the deep dive

Failure modes, consistency, isolation — and the bugs. Every bug here was found by an
adversarial audit, reproduced before anything was changed, and fixed with a regression test
that failed on the pre-fix code. The commit is `c5db31b`.

The pattern running through all of them: **retrieval bugs do not raise exceptions.** They
return plausible passages, report clean provenance, and quietly degrade quality by an amount
nobody can attribute. That is what makes them worth studying.

---

## Part 1 — The properties

### Consistency: what "honest provenance" actually costs

Aegis's `RetrievalResult` carries two separate objects. The distinction matters:

- **`provenance`** (`models.py:122`) — the *claim*: which origins contributed a surviving
  candidate, and how they were fused.
- **`observability`** (`models.py:276`) — the *measurement*: per-arm candidate counts,
  whether each arm fired, whether the keyword pass was corpus- or pool-scoped, whether the
  reranker graded, whether spotlighting was applied, whether a rewrite ran and changed
  anything, how many agentic rounds ran and what each contributed.

Provenance is derived from measurement, not asserted. `collect_origins` (`fusion.py:149`)
reads the per-candidate origin tags written during fusion, so **only origins that actually
produced a surviving candidate appear** in the claim.

That is the mechanism that makes the claim checkable. Every bug in Part 2 is a case where the
claim and the measurement had drifted apart.

### Cache coherence

Three caches, three different staleness stories:

| Cache | TTL | Stale means | Invalidation |
|---|---|---|---|
| Retrieval `SemanticCache` | 3600s | Old passages for a query | TTL only |
| `AnswerCache` | 1800s | **An old answer** | TTL only |
| `MemorySemanticCache` | 900s | Old recalled memory | Explicit, on every write |

There is **no ingestion-driven invalidation** of the retrieval or answer caches. Ingest a
corrected document and, for up to an hour, a repeat of the same question can serve the
pre-correction answer. That is a real limitation with a real mitigation (short TTLs and a
near-identity threshold), and the honest way to present it is as a known bound, not to claim
coherence that does not exist.

The **thresholds** are the primary safety control here, not the TTLs: 0.985 for retrieval and
0.97 for answers. The failure is asymmetric — a missed hit costs latency, a wrong hit returns
a confidently wrong answer with no signal.

### Isolation

The answer cache is the one place retrieval holds cross-request state that could leak, and it
is scoped three ways:

1. `_index_key(scope)` (`answer_cache.py:92`) — a per-scope Redis SET, so another scope's
   entries are never even enumerated.
2. `_entry_key(scope, query)` (`answer_cache.py:97`) — scope folded into the digest, so the
   same query under two scopes cannot collide on a key.
3. On read: `if entry.get("scope") != scope: continue` (`answer_cache.py:130-131`) — defence
   in depth against a hypothetical digest collision.

The scope is built in the graph as `f"{tenant}:{persona}:{agent_role}"` (`graph.py:363-371`)
and the comment calls it a correctness and isolation requirement rather than an optimisation.

The retrieval `SemanticCache` is keyed by `(query, persona)` and is **not tenant-scoped** —
which is defensible only because it caches *corpus* passages, and the corpus is shared. If a
per-tenant corpus is ever introduced, that key becomes a leak. Worth knowing as a live
constraint rather than discovering later.

### Failure modes and the degradation ladder

| Failure | Behaviour | Reported as |
|---|---|---|
| Rerank response unparseable | Keep the fused RRF order | `graded=False` + `degraded_reason` |
| Rerank grades only some candidates | Graded first, ungraded after, keeping fused scores | `ungraded=N`, `reason` names the shortfall |
| Rewriter unparseable / empty / unchanged | Return the original query | `changed=False` + a distinct reason each |
| No rewriter configured | Return the original | `reason="no rewriter configured"` |
| Judge unparseable | Non-empty context ⇒ sufficient | reason distinguishes "unparseable" from "not configured" |
| No judge configured | Same fallback | different reason string |
| Backend has no keyword search | Pool-scoped BM25, **no origin claimed** | `scope="pool"`, `adds_recall=False` |
| Backend cannot count entities | `None`, never `0` | `IngestReport.entities is None` |
| Graph store unreadable | `knowledge_graph()` returns `None` | The caller must render "unknown", not "empty" |
| Chunk fails validation | Not written | `chunks_rejected` + a reason string |
| Stores disabled | Lite in-memory retriever | A real backend, not a stub |

The rule visible in every row: **degrade, and say that you degraded.** The reason strings are
distinct per failure path precisely so a reader can tell which one happened.

### Performance

Cost per uncached query:

- 1 embedding call (the query)
- 1 recall round-trip per arm
- 1 BM25 pass — O(pool × query terms), trivial
- 1 fusion pass — O(total candidates)
- **1 rerank model call** (the dominant cost)
- Plus, with the intelligence flags on: 1 rewrite call + 1 judge call per round

With `agentic_retrieval_enabled` and two rounds that is **five model calls** for one answer:
rewrite, retrieve+rerank, judge, retrieve+rerank, judge. Every one of them is accrued into
the run's telemetry (`graph.py:636-637`), which is why the cost number is credible.

The cheapest possible path is an exact cache hit: zero model calls, not even the embedding
(`pipeline.py:160-162` returns before `embed`).

---

## Part 2 — The bugs

### Bug 1 — The query rewriter never received any history, twice, for two different reasons

**What it was, layer 1.** `agentic_retrieve` called `rewrite_fn(query, history=None)`,
hardcoding `None` and **overriding whatever history the caller had bound into the closure**.

**Why it mattered.** The rewriter's entire job is resolving pronouns, ellipsis and
back-references against the conversation. With no history it can only pass the query through.
It ran, cost a model call, correctly reported `changed=False` — because there genuinely was
nothing to resolve — and did nothing. Every observable signal said "working".

**The fix, layer 1.** `history` became an explicit parameter of the loop, threaded end to
end, with a docstring that says why (`agentic.py:376-380`): *"This is the whole point of the
rewriter — with no history it cannot resolve the pronouns, ellipsis and back-references it
exists to resolve — so it is an explicit parameter of the loop rather than something a caller
is left to bind into its closure."*

**What it was, layer 2 — the deeper reason.** Fixing the loop was not enough. On the graph
path the caller passed `state["messages"]`, which was **always empty at that point**.

And this is the part worth telling, because it is not a coding mistake:

> `messages` is a per-planning-round scratch buffer written by the **`plan`** node. `plan`
> runs **after** `retrieve`. There is no ordering of the graph in which `messages` could be
> populated at rewrite time.

The rewriter was **structurally unable** to do its job. No amount of debugging the rewriter
would have found it — the bug is in the data flow, not the function. And nothing errors,
because "no history" is a legitimate input.

**The fix, layer 2.** The memory layer's `AssembledMemory` gained a `conversation` field
(`memory/working.py:72-77`) — the surviving raw turns in OpenAI chat shape. `recall_memory`
(which runs *immediately upstream* of `retrieve`) writes it into `state["conversation"]`
(`graph.py:672-683`), and `retrieve` reads:

```python
# graph.py:528
history = state.get("conversation") or state.get("messages") or None
```

`messages` stays as the fallback so the single-shot / no-memory path is byte-identical (both
empty → `None`).

**The generalisable lesson.** When a component takes an optional input and its absence is a
legitimate state, "absent" and "broken" become indistinguishable. If a parameter is
load-bearing, either make it required or make its absence observable — and check *who writes
it and when* before assuming a state key is available.

---

### Bug 2 — The BM25 "arm" could never add recall while reporting itself as a firing arm

**What it was.** The pipeline claimed hybrid vector + graph + BM25 retrieval. The BM25 pass
scored the pool the vector and graph arms had **already returned** — about 20 documents.

**Why it mattered — three separate problems, and the third is the serious one:**

**It could not add recall.** Every document it ranked was already in the pool. A document
findable only by keyword — an invoice number, a rare product name — was never in the pool, so
BM25 could never surface it. That is precisely the case hybrid retrieval exists for. The arm
was solving nothing.

**Its IDF was not a corpus statistic.** `IDF(t) = ln(1 + (N − df + 0.5)/(df + 0.5))` only
means anything when `N` is the corpus size. Over 20 documents, a term in 3 of them looks rare
and might appear in 90% of the corpus. The arithmetic ran and produced numbers that were not
IDF.

**It reported itself as a firing retrieval origin.** `provenance.origins` said
`["vector", "graph", "bm25"]`. That is a claim about *where the evidence came from*, and it
was false. Worse, the list it fused was ~100% correlated with its own inputs — so RRF, which
treats each list as an independent opinion, was reinforcing the existing order while
believing it had corroboration.

**The fix, in three parts:**

1. **A real optional capability.** `KeywordBackend` (`protocols.py:91`) declares
   `keyword_recall(query, *, top_k, persona)` — a **corpus-wide** search. A backend either
   implements it or does not.
2. **Two honest branches.** `_keyword_signal` (`pipeline.py:266`) picks:
   - Corpus-capable → `RankedList(origins=(BM25,), ...)` + `KeywordReport(scope="corpus",
     adds_recall=True)`. A real arm, counted in `arms`, present in provenance.
   - Not capable → `RankedList(origins=(), ...)` + `KeywordReport(scope="pool",
     adds_recall=False)`. **Still fused** — reordering the pool is worth doing — but it
     claims no origin and is not counted as an arm (`pipeline.py:254`).
3. **The empty origins tuple is documented as meaningful** (`fusion.py:47-52`): a list may
   fuse without ever appearing in provenance as a source of recall.

The shared BM25 arithmetic stays in one place (`bm25_ranked`, `pipeline.py:500`), with the
docstring stating the distinction explicitly: *"What differs is what a caller may claim from
the result, not the arithmetic."*

**And the lite backend implements it.** `InMemoryKnowledgeBackend.keyword_recall`
(`memory.py:530`) is a genuine corpus search, so the databaseless path's `scope="corpus"` is
true rather than a convenience.

---

### Bug 3 — Round 2 of the agentic loop was structurally unable to contribute

**What it was.** The merge capped the result at **round 1's** source count.

**Why it mattered.** Work through it concretely. Round 1 returns 2 sources graded 9 and 8.
The judge says insufficient. Round 2 retrieves and returns 6 sources graded 7 and below. The
merge unions all 8, sorts by score, and caps at **2** — so it keeps the two round-1 sources
and discards every round-2 source.

The loop paid for a retrieval, a rerank and a judge call, ran two rounds, and produced
**byte-identical output to a single-shot run**.

This is not an edge case. It is the default outcome whenever round 1 finds the better
material — which is most of the time, since round 1 retrieved against the original question.
Meanwhile the observability reported `used_rounds=2`, implying two rounds' worth of evidence.

**The fix, in two parts:**

`_merge_cap` (`agentic.py:163`) is now `max(len(base.sources), len(incoming.sources))`, with
the reasoning in the docstring (`agentic.py:166-173`): the larger size *"leaves room for
later rounds to earn their place on score while still bounding the context to what one round
would naturally have produced."*

And **per-round accounting**: `RetrievalRound.new_sources` (`agentic.py:328`) records how many
sources each round actually added, computed by diffing source ids before and after the merge
(`agentic.py:418, 431`). A round that contributed zero now says so, in
`AgenticReport.round_new_sources`. *"The honest record that the round cost two model calls
and changed nothing."*

**The lesson.** A loop whose merge step cannot admit later results is not a loop — it is a
single-shot with extra billing. When you build an iterative refinement, the first thing to
test is whether iteration `n+1` can change the output at all.

---

### Bug 4 — The merge silently overrode the caller's spotlighting and threw away round 2's evidence

Two defects in the same function, both of the "quietly discards information" family.

**Defect A: spotlighting.** `_merge_results` rebuilds `answer_context` from the merged
sources. It always rebuilt it **spotlighted**, ignoring `RetrievalConfig.spotlight_enabled`.

Both directions are bad. A caller who turned spotlighting off got it back on, silently. And —
worse — if the default had been the other way, a caller relying on the injection defence
would have had it silently **removed** by the merge, on exactly the path (multi-round
retrieval on a hard question) where the most untrusted content is in play.

**The fix.** `_spotlight_on(base)` (`agentic.py:177`) reads
`base.observability.spotlight_applied` — the pipeline's own *measured* answer for the result
being merged. With no sources it falls back to the package default (defence ON), because
`False` there means "nothing to spotlight", not "spotlighting is disabled"
(`agentic.py:186-190`).

**Defect B: discarded evidence.** The merge carried round 1's `provenance`, `arms`,
`fused_candidates`, rerank verdict and `graph_delta` through unchanged. Round 2's were
dropped.

So a two-round retrieval reported round 1's evidence with a bigger source list. Concretely:
the live knowledge-graph visualisation showed only the first hop; provenance omitted any
signal that only round 2 contributed; and the rerank verdict could say `graded=True` when
round 2's rerank had in fact failed.

**The fix.** `_merge_observability` (`agentic.py:220`) folds everything: arms sum their
candidate counts (`_merge_arms`, `:193`), fused pool sizes add, the graph delta is unioned by
node id and edge triple (`_merge_graph_delta`, `:207`), and — the sharpest line —
`graded = base.rerank.graded and incoming.rerank.graded`, because *"a merged list is only as
graded as its weakest contributor"* (`agentic.py:230-233`).

`cache_hit` gets the same treatment: `base.cache_hit and incoming.cache_hit`
(`agentic.py:285`) — a merged result is only "served from cache" if every round was.

---

### Bug 5 — Chunk `word_start` counted overlap twice, so citation offsets drifted past the end of the document

**What it was.** Chunks overlap by 60 words. The document word offset advanced by the full
window length each time.

**Why it mattered.** The shared words belong to two consecutive chunks. Advancing by the full
`word_count` counts them twice, so the error accumulates:

```
drift after n chunks = (n − 1) × overlap
```

With 60 words of overlap and 50 chunks, that is **2,940 words** of drift. The last chunks
claim to start well past the end of the document.

`word_start` is chunk provenance — it is what lets a citation point at a location in the
source. Every offset was wrong, increasingly so with document length, and **nothing errored**.
The numbers were plausible integers.

**The fix.** `_pack_units` now returns `(window, carried)` pairs, where `carried` is how many
leading words the window re-used from the previous one (`chunker.py:193-198`), and:

```python
# chunker.py:271, 282
word_start = max(0, running_words - carried)
running_words = word_start + word_count
```

The window starts `carried` words *before* the previous one ended, which is where it actually
starts. The comment above it states the failure it prevents — *"offsets that cannot locate the
chunk they claim to cite"* — and `ChunkPiece.word_start`'s docstring now states the invariant:
`word_start + word_count` never exceeds the body's word count (`chunker.py:96-101`).

---

### Bug 6 — Section-blind dedup silently destroyed a whole section's content

**What it was.** In-batch dedup keyed on the **raw body**. The downstream idempotency ledger
keyed on **section + body** (via `ChunkPiece.content_id()`, which hashes the *contextualized*
text).

**Why it mattered.** Consider a document with:

```
## Refunds
... policy text ...
Contact support.

## Returns
... different policy text ...
Contact support.
```

The bare body `"Contact support."` appears twice. In-batch dedup sees one duplicate and drops
the second. But the two chunks are **not** the same to the ledger, to the embedding, or to a
user asking about returns — they carry different section paths and answer different questions.

If those chunks were the section's only content, the **Returns section ends up with nothing
indexed**, and the ingest report says "1 duplicate skipped" — a benign-looking number for
silent data loss.

**The fix, two parts** (`chunker.py:325-370`):

1. **Exact dedup keys on the contextualized text** — `piece.content_id()`
   (`chunker.py:358`), the same key the ledger uses. The two now agree by construction.
2. **Near-duplicate detection is scoped to the section** — `kept_shingles` is a dict keyed by
   `piece.section` (`chunker.py:354, 363`), so a chunk is only compared against others under
   the same heading path.

The docstring states the principle (`chunker.py:330-338`): *"That agreement is the point"* —
and *"two sections repeating boilerplate are distinct answers to distinct questions, not one
passage seen twice."*

**The generalisable lesson.** Any pipeline with several identity checks must agree on what
identity *means*. When they disagree, the disagreement is silent, and the symptom (missing
content) shows up somewhere far from the cause.

---

### Bug 7 — A failed rerank dressed RRF scores as relevance grades

**What it was.** When the rerank response did not parse, the code fell back to the recall
order — correct behaviour — and returned a plain list, with `ran=True` and the RRF scores
sitting in the `score` field.

**Why it mattered.** Downstream, an ungraded fallback list is **byte-shaped exactly like a
graded one**. Nobody could tell "the model judged this a 9" from "this happened to be first in
the fused list". Two consequences:

- **Quality attribution is impossible.** If answers get worse after a prompt change, you
  cannot tell whether the reranker stopped working.
- **The scores are meaningless numbers presented as judgements.** An RRF score of 0.0164 is
  not a relevance grade, and the UI showed it as one.

**The fix.** `RerankOutcome` (`reranker.py:46-68`) carries the verdict alongside the
survivors:

```python
candidates: list[Candidate]
graded: bool
ungraded: int
reason: str | None
```

And the pipeline turns that into a `RerankReport` (`pipeline.py:181-189`) carrying
`ran`/`graded`/`input_candidates`/`kept`/`ungraded`/`degraded_reason`/`top_scores`.

**The sharpest sub-decision: an ungraded candidate is never assigned `0.0`.** It keeps the
score it arrived with, sorts after every graded candidate, and is counted in `ungraded`
(`reranker.py:147-159`). Fabricating a `0.0` looks harmless and is a lie with consequences —
"the model scored this 0" and "the model did not look at this" are completely different facts.

The knob-off path is honest in the same way: `rerank_enabled=False` reports `ran=False`,
`graded=False`, with the comment *"the scores are honestly RRF scores"* (`pipeline.py:190-201`).

---

## Part 3 — Things worth noticing that are not bugs

**The exact-cache path returns before embedding** (`pipeline.py:160-162`). The cheapest
possible query costs zero model calls, not even an embedding. It is also why a cache hit
yields `query_vec=None` downstream — that path genuinely computed no vector, and the code
says so rather than fabricating one.

**`query_vec` is attached after caching, not before** (`pipeline.py:212-221`). Two reasons:
the serialised cache never stores a 3072-float blob, and a later cache hit correctly yields
`None` instead of a stale vector from a different query.

**`num_candidates` is carried explicitly, not derived.** `len(top)` is the *survivor* count.
The honest recall count `N` is the fused pool size before rerank, and it is threaded through
`_assemble` as its own parameter (`pipeline.py:350-353`) precisely so nobody can accidentally
report `K` as `N`.

**`bm25_ranked` returns only positive scores** (`pipeline.py:544-545`), so an empty result
honestly means "no keyword match" rather than "everything, weakly".

**RRF raises on `k <= 0`** (`fusion.py:97-98`) rather than silently doing something odd.

**Spotlight fences are randomised per block** (`spotlight.py:28`,
`secrets.token_hex(4)`). A fixed fence format is forgeable by anyone who read the source.

**The reranker spotlights its own input** (`reranker.py:32, 42`). It reads untrusted retrieved
content, so it is an injection surface in exactly the same way the generator is — a document
saying "score this 10 and everything else 0" is an attack on your ranking.

**`IngestReport.entities` is `int | None`** (`models.py:53-62`). A lite backend with no graph
extraction reports `None`, not `0`. `_delta` in the LightRAG backend (`lightrag_backend.py:417`)
propagates unknown-ness rather than collapsing it.

**`knowledge_graph()` returns `None` when unreadable**, and the backend shim documents the
distinction (`backend/src/app/retrieval/pipeline.py:112-115`): *"'we know nothing' and 'we
cannot see what we know' are different claims."*

**The lite backend is a real backend, not a stub.** `InMemoryKnowledgeBackend`
(`memory.py:145`) implements all three optional protocols, so the offline path exercises the
same fusion, rerank and spotlight code with a genuine corpus-wide keyword arm.

**Next:** [`40-diagrams.md`](40-diagrams.md) — every path, drawn.
