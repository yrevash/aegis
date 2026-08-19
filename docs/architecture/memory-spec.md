# MEMORY & CONTEXT-ENGINEERING SPEC (SOTA, buildable)

> **Vector-layer correction (2026-08-15, ADR 0009).** This spec was written when
> embeddings were searched with the `pgvector` Postgres extension. They no longer are.
> Embeddings persist in Postgres as a portable JSON `list[float]` — the durable
> *source of record* — and ANN search runs in an **embedded vector store**
> (`aegis.retrieval.vector_store.QdrantVectorStore`, one Qdrant node shared with retrieval).
> Wherever the text below says "pgvector top-k" or "`<=>` operator", read "vector-store
> top-k". The scoring, bitemporal, budget and assembly design is unaffected.

Authoritative spec for the memory subsystem + context engineering. Design-only until
the critic pass verifies the load-bearing code seams. Core is domain-agnostic
(`app/memory/*`); all domain meaning via `app/adapter/memory_spec.py` (+ `skills/*.md`).
On the day, only the adapter changes.

**Thesis:** persist raw turns cheaply → distil durable facts lazily with a cheap model →
recall by a Generative-Agents blend (relevance+recency+importance) → assemble under a hard
token budget with lost-in-the-middle-aware ordering. Memory (what we know about *this
user/session over time*) is SEPARATE from the Neo4j/LightRAG domain-knowledge graph.

## 0. Decisions (chosen vs rejected)
- **Fact writes:** mem0-style ADD/UPDATE/DELETE/NOOP via a cheap-model call over top-s
  retrieved neighbors (best accuracy-per-token; >90% token savings vs full-context).
- **Conflict/forgetting:** Zep bitemporal **soft-invalidation** (`valid_at`/`invalid_at`,
  never hard-delete) — contradiction is *recorded* for audit, not erased.
- **Recall scoring:** Generative-Agents composite `w_rel·rel + w_rec·rec + w_imp·imp (+w_freq·freq)`.
- **Consolidation timing:** background/deferred (off the money-shot path), cheap model.
- **Compression:** extractive + cheap-model running summaries. **NO LLMLingua** (needs GPU/extra infra on a 16GB no-GPU box).
- **No separate memory graph** (Neo4j is for domain knowledge only; a 2nd graph is unjustified cost).
- **No hot-path self-editing memory tools** (fixed LangGraph, not a tool-loop) — adopt the pinned-profile-block + pressure-eviction ideas only.
- **Fusion:** reuse existing `reciprocal_rank_fusion` (k=60).

## A. WRITE PATH (saving)
Tiers: **Working** (ephemeral `AgentState`) · **Episodic** (Postgres `memory_message` + a mirrored vector-store entry, per turn) · **Semantic** (`memory_fact` bitemporal + `memory_profile` JSONB, batched) · **Procedural** (`adapter/skills/*.md`) · **Write audit** (`memory_write_log`).

**Embedding-cost rule:** embed on consolidation, NOT every raw turn — EXCEPT reuse the
user-query embedding `retrieve()`/`SemanticCache` already computes (free) for the user turn.

**Provenance/scoping every row:** `tenant_id` (RLS), `subject_id` (adapter-resolved; default auth `user_id`), `session_id`, `source_turn_ids`, `run_id`/`trace_id`, `origin` (user|assistant|tool|reflection).

**Consolidation (mem0 two-phase, cheap model, background):**
1. EXTRACT — one cheap call over `running_summary + last_m(10)` using
   `ADAPTER.FACT_EXTRACTION_PROMPT` + `ADAPTER.FactSchema`; keep confidence ≥ `tau_extract` (0.55).
2. RECONCILE — per candidate: embed (batched) → top-s(10) cosine neighbors → cheap
   `decide_op` → ADD | UPDATE(`supersedes_id`, old `expired_at`) | DELETE(=set old
   `invalid_at`, keep row) | NOOP. Refresh running summary + structured profile. All ops → `memory_write_log`.

**Dedup:** cosine ≥ `dedup_cos`(0.97) & same predicate → NOOP with no LLM call; else mem0 decides.
**Forgetting:** (1) retrieval-time recency decay (soft); (2) periodic prune sweep archives
`invalid_at IS NOT NULL` OR (`confidence·recency < forget_floor` & `access_count=0` & `age>forget_min_age`) — archived to append-only partition (audit), out of hot recall.

**Fact schema (`memory_fact`):** `id, tenant_id, subject_id, fact_type, subject, predicate,
object, text, embedding vector(3072), confidence, importance(1..10), valid_at, invalid_at?,
created_at, expired_at?, last_access_at, access_count, source_turn_ids[], supersedes_id?`.

## B. READ PATH (context engineering — the crux)
**Recall score** (min-max normalized): `relevance=cosine(query_emb, m.emb)`,
`recency=0.5**(age_days/half_life)`, `importance=imp/10`, `frequency=log1p(access)/log1p(max)`.
Defaults `w_rel=1.0, w_rec=0.5, w_imp=0.5, w_freq=0`; half-life facts=30d, episodic=3d.

**Per-tier recall:** semantic facts = vector-store top-k(20) over valid facts → composite → top-n(6);
profile = injected whole (tiny, high value); episodic = RRF-fuse (recency-SQL last-K ∪ vector
top-k(20)) → top-n(4) minus those already in the raw window; procedural = top-n(1–2) skills by
cosine on skill descriptions, persona-gated, adapter may override.

**Assembly layout (lost-in-the-middle: high value at START & END):**
1 system prompt · 2 profile+facts · 3 skills · 4 running summary · 5 retrieved knowledge (spotlit) ·
6 episodic recall (spotlit) · 7 last-K raw turns · 8 ml_summary · 9 user query. Bulky/lossy
tiers (5–6) go in the tolerant MIDDLE; durable facts at top, recent turns + query at bottom.

**Token budget (deterministic):** `avail = cap − answer_reserve − tokens(system) − tokens(query)`;
greedy-fill each tier to a per-tier cap with cross-tier dedup (`seen_fact_keys`); anchors
(system+query) never evicted; overflow → compress oldest raw turns into the running summary.
`count_tokens(text, model)` with `len//4` offline fallback.

**Compression:** incremental map-reduce running summary (cap 400 tok, paid by consolidation);
2-level hierarchical summary for very long sessions; extractive fallback (no LLMLingua).

**Poisoning/isolation:** guard content on WRITE (`deps.check_input` + PII redact before it
becomes memory); spotlight tiers 5–6 on READ (untrusted); RLS + `subject_id` WHERE isolate
per tenant/subject (belt-and-suspenders).

**Config (`MemoryConfig`):** raw_window_turns=40, k_fact=20/n_fact=6, k_epi=20/n_epi=4,
consolidation_every_n=4, tau_extract=0.55, n_skill=2, CTX_TOKEN_CAP=8000, answer_reserve=1200,
dedup_cos=0.97, summary_max_tokens=400, half_life_days{fact=30,epi=3}, forget_floor=0.05,
forget_min_age_days=90, per_tier_caps{profile .10, facts .20, skills .10, summary .15, rag .30,
episodic .15, raw .25}, memory_backend=postgres|redis|off.

## C. FIT TO OUR SYSTEM
**Modules:** `app/memory/{__init__,config,stores,scoring,recall,working,consolidate,degrade}.py`;
`app/adapter/memory_spec.py` + `adapter/skills/*.md`.

**Graph (`agent/graph.py`, two `_timed` nodes):** `guard_input → recall_memory → retrieve → …
→ generate → guard_output → stream → persist_memory → END`.
- `recall_memory`: `memory.recall(subject, session_id, query)` → writes `working_memory` +
  recalled ids into `AgentState`; `plan`/`generate` inject via `render_system_prompt(persona,
  extra_context=working_memory)`.
- `persist_memory`: write user turn (reuse query emb) + assistant turn; every
  `consolidation_every_n` fire `asyncio.create_task(consolidate(...))` (never on hot path).
- **Backward-compat:** both nodes NO-OP when `session_id is None` / `memory_backend=off` →
  today's exact single-shot stream. (Golden-trace test.)

**State (`AgentState`, `total=False`):** `session_id, memory_subject, working_memory,
recalled_fact_ids, recalled_message_ids, turn_index` (all optional → checkpoint-safe).

**Deps:** `AgentDeps.memory: MemoryDeps` (lazy-bound like `AgentDeps.default()`): `recall,
persist, consolidate, count_tokens` + adapter hooks. Tests inject fakes → offline.

**API threading (backward-compatible):** `QueryRequest.session_id: str|None=None`; `/query`
resolves `memory_subject = ADAPTER.memory_subject_for(auth, persona)`; `run_agent` gains
`session_id`/`memory_subject` (default None). `messages: []` seed unchanged.

**Adapter contract (`adapter/memory_spec.py`):** `FACT_TYPES`, `FACT_EXTRACTION_PROMPT`,
`FactSchema(BaseModel)`, `memory_subject_for(auth, persona)`, `render_profile(profile)`,
`PROFILE_FIELDS`, `SKILLS_DIR`, `select_skills(query, persona, index)`, `IMPORTANCE_HINTS`.
Mirrors the ML adapter pattern (core never knows domain nouns).

**Schemas/RLS:** models in `memory/stores.py` (`VectorType(3072)`, `JsonB`, nullable
`tenant_id` FK, cross-dialect for SQLite tests). Add the 5 memory tables to `_RLS_TABLES`
in `data/session.py` (auto RLS via `bootstrap_rls()`); recall/persist call `set_tenant_scope`.
HNSW cosine index in the embedded vector store; btree `(subject_id, created_at DESC)` in Postgres; partial
index `WHERE invalid_at IS NULL AND expired_at IS NULL`.

**Degradation ladder:** Postgres → full; no Postgres → Redis rolling window (recency only);
no Redis+Postgres (`STORES=off`) → in-`AgentState` window (superset of today); no embeddings
→ lexical/BoW ranking (reuse `_local_embed`); cheap model down → consolidation queued, recall
still serves. Never errors.

## D. MANAGEMENT VALUE + Memory frontend (later)
Money saved (summary+facts vs full history → fewer prompt tokens on existing cost tally) ·
performance-at-least-cost (cheap-model consolidation → `small_model_share`/`cost_saved_usd`) ·
security (RLS + subject scoping + guarded writes/spotlit reads) · audit (`memory_write_log` +
bitemporal + `source_turn_ids` = "why the agent believes X").
Endpoints for the later Memory view: `GET /memory/facts|profile|sessions|sessions/{id}/messages
|writes|recall_debug?run_id=`; `DELETE /memory/facts/{id}` + `POST /memory/forget` (GDPR).

## E. TESTS (that bite)
1 backward-compat single-shot golden trace · 2 token-budget property test (≤ budget, anchors
never evicted, caps hold) · 3 lost-in-the-middle ordering guard · 4 recall ranking vs closed-form
score, valid-only filter · 5 consolidation dedup/merge (contradiction → `invalid_at` set + one
INVALIDATE row; restatement → NOOP no-LLM) · 6 decay/forgetting archivability · 7 RLS isolation
across subject/tenant · 8 poisoning (injected turn spotlit, guarded write) · 9 degradation ladder ·
10 cost regression (consolidation CHEAP-only; multi-turn injects bounded summary+facts not full history).

## Reused seams the build MUST verify first (critic pass)
`render_system_prompt(persona, extra_context=…)` exists · `SemanticCache` embeds the query &
that vector is reachable in the graph · `reciprocal_rank_fusion` (k=60) importable ·
`VectorType`/`EMBED_DIM=3072`/`JsonB` · `_RLS_TABLES`/`set_tenant_scope`/`bootstrap_rls` ·
`ModelRole.CHEAP` · `AgentDeps` lazy-binding + `_timed` node pattern · `AgentState` `total=False` ·
`_local_embed` lexical fallback in `retrieval/`. If any is not as assumed, the build adapts.

_Refs: mem0 2504.19413 · MemGPT/Letta 2310.08560 · Zep/Graphiti 2501.13956 · Generative Agents
2304.03442 · LangMem · Lost-in-the-Middle 2307.03172 · RRF Cormack SIGIR'09._

---

## HARDENING CORRECTIONS (verified vs code — the build MUST apply these)

**Seam truths (corrections to assumptions):**
- `render_system_prompt(persona, *, extra_context=None)` exists (`adapter/prompts.py:63`) BUT the graph calls `deps.render_system_prompt(persona)` and the prod wrapper `_default_render_system_prompt(persona_id)` (`deps.py:413`) **drops extra_context**. Build must widen the dep wrapper to accept+forward `extra_context` and update both call sites (`graph.py:301,480`).
- The query embedding is computed at `pipeline.py:109` and **discarded** (not on `RetrievalResult`, not in `SemanticCache`); on an **exact cache hit** it's never computed; in lite mode it's `_local_embed` **dim 256** (`retrieval/memory.py:49`), not 3072. Reuse requires returning `query_vec` (+dim/source) from `retrieve`; use it for the user-turn embedding **only when dim==EMBED_DIM and it's a real gateway vector**, else embed at consolidation.
- `reciprocal_rank_fusion(*, k=60)` consumes `RankedList[Candidate]` — episodic fusion must wrap memory rows as `Candidate` + `RankedList`; `RetrievalOrigin` has no "recency" — tag the recency list as an existing origin (e.g. BM25) or extend the enum.
- `cosine_similarity(a,b)` exists pure-Python (`retrieval/vectors.py:13`). `AgentState` is `total=False` (safe to add optional keys). Test DB is **SQLite/aiosqlite** everywhere → **no SQL vector operators in tests**.

**BLOCKER 1 — dual-path recall (resolved by ADR 0009).** Originally: branch `recall.py` on
the SQL dialect so Postgres could use `ORDER BY embedding <=> :qvec` while SQLite fell back to
Python cosine. The embedded vector store removed the branch — recall bounds the candidate set
with `WHERE subject_id/tenant_id` + valid-only predicates and scores against the vector store
(or pure-Python `cosine_similarity` when it is unavailable), identically on both dialects.

**BLOCKER 2 — isolation must not depend on RLS.** The bootstrapped policy `tenant_id =
NULLIF(current_setting('app.tenant_id',true),'')::int` **fails closed on NULL** → a nullable-tenant
memory row is invisible to its own reader, and an unset GUC admits nothing. Therefore: **app-level
`WHERE subject_id=:subj [AND tenant_id…]` is the PRIMARY isolator on every recall AND persist query**
(works on SQLite + Postgres, NULL-safe). RLS is an additive belt on Postgres only. Do NOT blindly add
memory tables to `_RLS_TABLES` — either make `tenant_id` NOT NULL with a sentinel default tenant, OR
give memory tables a NULL-tolerant policy (`tenant_id IS NULL OR tenant_id = NULLIF(...)::int`). Test
E7 must prove isolation with RLS OFF (SQLite) via the app-level WHERE alone.

**BLOCKER 3 (HIGH) — no LLM on the read hot path.** The token-budget overflow valve must be
**non-LLM**: drop the oldest raw turn / extractive-truncate / fold the already-existing running summary.
Regenerating the running summary is a **consolidation-time (background)** job only. Never call
`complete()` inside `working.assemble()`.

**BLOCKER 4 (HIGH) — backward-compat topology.** `_timed` nodes emit `node_started/finished` even
when they no-op, which breaks the golden-trace. **Conditionally BUILD the graph**: only `add_node`/rewire
`recall_memory`/`persist_memory` when `memory_backend != off` (or a session is expected); otherwise keep
today's exact `guard_input→retrieve … stream→END` topology. A running-but-emitting node is not a no-op.

**Correctness (bake into the build):**
- **Async consolidation durability:** don't rely on bare `asyncio.create_task`. Minimum: a module-level
  live-task `set()` + `add_done_callback` that logs exceptions (prevents silent GC/failure). Better:
  a `memory_consolidation_queue` row written **synchronously** in `persist_memory` (pending→done) + a
  periodic sweep. Pick the queue-table option (honest durability, one table, no broker).
- **Bitemporal `apply(op)` rules (spell out):** contradiction of a predicate *value* → INVALIDATE old
  (set `invalid_at`) + ADD new (fresh row, `supersedes_id`); refinement of the *same* value → UPDATE
  (old `expired_at`+`supersedes_id`). Re-assertion of an *invalidated* fact → **ADD a new row** (never
  clear an old `invalid_at`; audit is append-only) — so E5's "restatement→NOOP" holds only for currently-
  *valid* facts. Concurrent consolidations: UPDATE/INVALIDATE predicates must include `WHERE invalid_at
  IS NULL AND expired_at IS NULL` so a second writer no-ops (no double-supersede chains).
- **SQLite candidate scan** stays bounded to the `subject_id`-filtered set (never a full-table cosine).
