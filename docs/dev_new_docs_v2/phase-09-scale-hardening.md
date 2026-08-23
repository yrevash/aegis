# Phase 9 — Scale and hardening

> **Kept as a record, 2026-08-23.** This phase shipped. It survives the documentation
> clean-up because `docs/adr/0009` names its §9.1 as the decision that
> superseded the embedded vector store with Qdrant. The rest of the v2 plan — the
> master plan, the roadmap, the other phases, six research plans and five technology
> surveys — was deleted and is in git history (last full set: `2d8b84d`). **Links from
> here into `plans/` and `research/` are therefore dead**; the bodies are intact, only
> the cross-references are broken. See [`README.md`](README.md).



**Last, because it hardens what the earlier phases build.**

Research: `plans/05-modularity-scale.md` ·
`plans/04-enterprise-substrate.md` §6 ·
[`backlog-post-hackathon.md`](backlog-post-hackathon.md)

---

## Amendments of 2026-08-19 — these override the sections below

**1. The "$100 model bill binds first" framing is withdrawn.** That figure is the
hackathon *credit ceiling*, not a property of Aegis. It constrains our development and
rehearsal, not the architecture, and stating it to a jury undersells the system. The
binding architectural limit is storage, addressed by 9.1.

**2. Qdrant is the vector store, for both consumers.** Decided by the user on
2026-08-19, after verifying against the pinned versions:

* Qdrant **v1.19.0** publishes `qdrant-x86_64-pc-windows-msvc.zip` — Apache 2.0, a zip
  with a binary, **no Docker and no installer**. Same operational shape as Superset.
* LightRAG **1.5.6** ships `QdrantVectorDBStorage` (`lightrag/kg/qdrant_impl.py`,
  registered in `kg/__init__.py`), with batched upserts, payload-size limits and a
  `QDRANT_WORKSPACE` namespace override. `qdrant_client` is already installed.
* It reads **`QDRANT_URL`**, which is already in `backend/.env` — the intent was
  recorded and never wired.
* **Chroma was considered and rejected for LightRAG because it cannot work**: LightRAG
  1.5.6's vector implementations are NanoVectorDB, Milvus, PGVector, Faiss, Qdrant,
  Mongo and OpenSearch. There is no `chroma_impl.py`. Chroma-in-server-mode would have
  solved only Aegis's half and left two vector systems to run.

**3. Therefore 9.1 is rewritten and grows from 0.5d to ~1.0d.** It is no longer "add a
mode flag"; it is **no JSON- or SQLite-backed vector storage in the server profile**:

* LightRAG `vector_storage` -> `QdrantVectorDBStorage`, replacing NanoVectorDB, whose
  own docstring calls it "a brute-force cosine scan held in memory" written back as a
  whole-file JSON rewrite.
* `aegis.retrieval` vectors -> Qdrant. **Chroma is removed entirely**, not demoted to a
  second mode: the `chromadb` dependency, `ChromaVectorStore`, and every construction
  site (`main.py`, `build_lite_retriever` x2, `evals/harness.py`,
  `scripts/eval_goldset.py`). Keeping a Chroma path would leave the SQLite metadata lock
  reachable by configuration — and it is that lock which makes `uvicorn --workers 2`
  fail today, looking like corruption rather than a clear error. A ceiling you can still
  configure your way back into is not removed.
* **Tests and dev do not need a Qdrant server.** `qdrant_client` supports an in-process
  mode, so the ephemeral choice the 8.4 lane made explicit stays available and stays
  named out loud. The two seams 8.4 made *raise* rather than silently degrade must keep
  raising — this task changes which store they configure, never whether a forgotten call
  is silent.
* LightRAG graph stays on Neo4j; KV moves off files to Postgres or Redis, both of which
  already run.
* Refuse to boot with `--workers>1` while any embedded store is configured.

**The accepted cost:** existing vectors are re-ingested, not migrated. The user has
explicitly accepted this. Do it before a demo corpus exists, not after.

**One store, one operational story.** After this task Aegis runs exactly one vector
engine, in one mode, on one Windows binary — which is also one fewer thing to install,
explain and have fail on 30 August.

**What this buys:** 9.1 stops *documenting* the single-process ceiling and removes it.
"Scaling later is a deployment change" becomes true rather than aspirational.

---

## What is actually wrong

### Which limit binds first, and it is not architecture

**The model bill, by an order of magnitude.**

| | |
|---|---|
| Cost per 4-agent fan-out | **~$0.13–0.20** |
| Total fan-out queries on $100 | **~650** — covering all remaining development, rehearsal *and* the day |
| Five concurrent users at 1 query/min | **~$0.75/min — the whole balance in ~2 hours** |

Nothing in this phase changes that. The controls that do are Phase 5's depth classifier, Phase
6's manual-escalation pre-flight, and the caching already in place. **Say this plainly to a jury
rather than claiming an architecture that the budget would never let you exercise.**

### The first *architectural* limit: embedded stores are single-process

This is the one thing that forecloses "scaling later is a deployment change".

- Chroma `PersistentClient` is **SQLite-locked** and reloads the whole HNSW index on any foreign
  write.
- LightRAG's NanoVectorDB is a **whole-file JSON rewrite**.
- Therefore `uvicorn --workers 2` **cannot work today**, and it fails in a way that looks like
  corruption rather than a clear error.

Measured: 50k vectors at 3072-dim float32 brute force = **614 MB and 13.5 ms per query**, ×3
LightRAG stores, **on the event loop**.

### Three things spend or stall with nothing bounding them

- **Background jobs spend money with no enforcement.** `backend/src/app/main.py:99-112` binds the
  live completer and the real embedder to a sweeper running every 60 seconds;
  `enforce_governance` is on the request path only.
- **Nothing limits concurrent model calls.** Five users × four agents is twenty simultaneous
  gateway calls.
- **The Postgres pools are unconfigured** — SQLAlchemy's default 15, then a 30-second stall with
  no diagnostic, across two engines plus a worker pool.

---

## Tasks

| # | Task | Days |
|---|---|---|
| 9.1 | `VECTOR_STORE_MODE=embedded\|server` + refuse to boot with `--workers>1` while embedded | 0.5 |
| 9.2 | Budget enforcement on every model-calling job | 0.5 |
| 9.3 | Model-call concurrency limiter shared across the worker pool | 0.5 |
| 9.4 | Configure the Postgres pools | 0.25 |
| 9.5 | RLS fail-closed on an unset scope | 2.0 |
| 9.6 | Per-tenant admission control, end to end | 0.5 |
| 9.7 | Move vector search off the event loop | 0.25 |

**Total: 4.5 days.** Under 20 lines of change removes four of seven ranked limits from the
"needs code" column — 9.1, 9.4 and 9.7 are small and disproportionately valuable.

### 9.1 — The vector-store mode seam

Chroma's `.server()` mode already exists and heartbeats. The work is the seam plus the guard:
booting with `--workers>1` while embedded must **refuse with the reason**, not corrupt an index.

That guard is what turns "we could scale" from a claim into a property.

### 9.2 — Budget enforcement on background jobs

**Every consumer of the Phase 3 substrate spends money**: ingestion embeds, memory consolidation
makes live cheap-model calls, trace eval grades with a model, report generation may.
`enforce_governance` runs on the **request path only** (`backend/src/app/main.py:99-112` binds
the live completer and the real embedder to a sweeper that runs every 60 seconds, entirely
outside it).

As ingestion moves onto the substrate in Phase 4, the volume of unbudgeted spend **grows**. This
is a correctness requirement, not an optimisation.

**The fix:** every job payload carries the enqueuer's governance context — tenant, user, budget
scope — and spends through the same enforcer. `BudgetExceededError` becomes a first-class job
outcome (dead-letter with a clear reason), not a surprise on the invoice. A payload field and a
`with` block.

### 9.3 — The shared model-call limiter

Five concurrent users × four agents is **twenty simultaneous gateway calls**. Nothing bounds
that today.

The limiter lives with the worker pool and covers both request-path and job-path calls, because
a rate limit does not care which one exhausted it. **This is not premature** — it is what
prevents a rate-limit failure during a live demo.

### 9.4 — Postgres pools

**Entirely unconfigured** — SQLAlchemy's default 15, then a 30-second stall with no diagnostic.
With two engines (serving + admin) and a worker pool, the arithmetic needs doing once and
writing down.

### 9.5 — RLS fail-closed on an unset scope

The largest task here and the one with the most ways to go wrong.

The predicate deliberately fails **open** on an unset scope because the auth path reads `users`
by username before any tenant is known. Closing it needs a `SECURITY DEFINER` login function and
**a complete enumeration of every unscoped reader**. Known so far:

- both background sweepers
- the LLM-Ops registry cache warm at startup
- `list_recent_audit`, which never calls `set_tenant_scope` at all
- **the Phase 3 job claim** (claims unscoped on the admin engine, by design)
- **the Phase 7 SQL console**

**Ship it behind `RLS_FAIL_CLOSED=false` first**, with a logger recording any session that
queries a tenant-scoped table without a bound scope. Use the log as the enumeration, move
background work onto the admin engine, then flip. Flipping first turns every unenumerated path
into a silent zero-row result — which is worse than the fail-open it replaces.

### 9.7 — Vector search off the event loop

13.5 ms per query of pure CPU inside the event loop blocks every other request for that
duration. `asyncio.to_thread` around the brute-force scan. Two lines.

---

## Named premature, so nobody starts one

Each has a trigger that would reverse the decision:

| Not doing | Would reconsider when |
|---|---|
| Horizontal scale-out | More than one box exists |
| A distributed queue | Postgres claim throughput is the bottleneck — it is 4 orders of magnitude away |
| PgBouncer | Connection count binds — and note it **breaks `LISTEN/NOTIFY`** |
| Read replicas, sharding | Read load binds |
| Log-aggregation stack, Prometheus/Grafana | More than one process to aggregate |
| Exactly-once delivery | At-least-once + idempotency keys proves insufficient |
| Per-tenant process isolation | A tenant can crash another's run |
| Kubernetes | Deployment is not one Windows box |
| A real load harness | A 10-query shape test stops answering the question |

---

## Definition of done

- [ ] `VECTOR_STORE_MODE=server` works against a running Chroma server; `embedded` + `--workers>1` **refuses to boot with the reason**.
- [ ] A background job that exceeds its tenant's budget dead-letters with `BudgetExceededError` — tested by actually exhausting a test tenant's budget.
- [ ] Twenty concurrent agent calls are limited to the configured ceiling — tested with a counting fake gateway.
- [ ] Pool sizes are explicit, documented, and sum to less than `max_connections`.
- [ ] With `RLS_FAIL_CLOSED=true`, login still works and both sweepers still run — proved on the live-Postgres suite, not asserted.
- [ ] The unscoped-reader logger reports **zero** sessions over a full suite run before the flag flips.
- [ ] Vector search no longer blocks the event loop — measured.
- [ ] Full suites green, ruff clean, `next build` green.

## Demo at the end of this phase

Exhaust a test tenant's budget and watch a background ingestion job dead-letter cleanly with the
reason, while another tenant's work continues untouched. Then show the boot refusal when someone
asks for two workers on an embedded store.

Both are the same story: **the platform says no, out loud, instead of degrading quietly.**

## Risks

**9.5 is the one that can break login.** The fail-open branch exists for a real reason. The
flag-first-then-enumerate ordering is not optional caution — flipping first turns every
unenumerated reader into a silent zero-row result, which is a worse failure than the one being
fixed.

**The budget context on jobs touches every enqueue site.** Miss one and that path spends
unbudgeted — the exact defect being fixed, now harder to spot because the others are covered.

**Server-mode Chroma is a second process to keep alive** on a machine already running Postgres,
Neo4j and Memurai. Embedded stays the demo default; the seam exists so the claim is honest.
