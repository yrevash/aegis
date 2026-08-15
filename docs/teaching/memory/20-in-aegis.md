# Memory — the implementation in Aegis

Every claim here is checkable against source. Paths are relative to the repo root.

The module lives at **`aegis/src/aegis/memory/`** — a standalone, importable package with
no domain logic and no host coupling. The backend at **`backend/src/app/memory/`** is a
strangler shim that re-exports it and wires one thing: the domain contract.

---

## How you import it

```python
from aegis.memory import (
    MemoryConfig,                       # every tunable
    assemble_working_memory,            # the read path (recall + assemble)
    recall, RecallBundle,               # recall only
    consolidate, ConsolidationResult,   # the write path
    enqueue_consolidation, sweep_pending, prune_forgotten,
    MemorySemanticCache,                # the derived cache
    set_default_spec,                   # the domain seam
    MemoryFact, MemoryMessage, MemoryProfile, MemorySession, MemoryWriteLog,
)
```

The full export list is `aegis/src/aegis/memory/__init__.py:67-114` — 48 names. Importing
the package pulls `sqlalchemy` (via the `aegis[data]` extra) and nothing heavier: no
retrieval stack, no gateway (`aegis/src/aegis/memory/__init__.py:1-14`).

---

## The domain seam: `MemorySpec`

`aegis.memory` does not know what a "fact" is. That is injected.

**`aegis/src/aegis/memory/spec.py:53-88`** defines `MemorySpec` as a `Protocol`:

| Member | Type | What the domain supplies |
|---|---|---|
| `FACT_EXTRACTION_PROMPT` | `str` | The system prompt for the extractor |
| `IMPORTANCE_HINTS` | `str` | Domain guidance for the 1–10 poignancy rating |
| `PROFILE_FIELDS` | `list[str]` | The ordered structured-profile fields |
| `FACT_TYPES` | `list[str]` | The kinds of durable fact this domain distils |
| `SKILLS_DIR` | `str` | Directory of procedural-skill markdown files |
| `FactSchema` | `type` | Pydantic model for one extracted fact |
| `FactExtraction` | `type` | Pydantic container with a `facts` list |
| `render_profile(profile)` | `-> str` | Renders the profile as the prompt's human block |
| `select_skills(query, persona, available)` | `-> list[str] \| None` | Picks procedural skills |

It is a **structural** protocol — no inheritance. The reference implementation is a
*module*: `backend/src/app/adapter/memory_spec.py`. A module function `render_profile(profile)`
matches the protocol method `render_profile(self, profile)` because module functions bind
no `self` (`spec.py:56-59`).

Resolution: pass `spec=` explicitly, or configure a process-wide default once
(`spec.py:94-121`: `set_default_spec` / `get_default_spec` / `resolve_spec`). `get_default_spec`
**raises** if nothing was configured — it does not silently return a stub.

The backend wires it at import time:

```python
# backend/src/app/memory/__init__.py:28-33
from app.adapter import memory_spec as _memory_spec
set_default_spec(_memory_spec)
```

That is the whole adapter seam for memory. On hackathon day you rewrite
`app/adapter/memory_spec.py` and nothing in `aegis.memory` changes.

---

## The storage layer

**`aegis/src/aegis/memory/stores.py`** — six SQLAlchemy models on the shared
`aegis.data.AegisBase` metadata, so a host's `create_all` materialises them.

| Model | Line | Tier | Notes |
|---|---|---|---|
| `MemorySession` | `stores.py:65` | — | Thread: `turn_count`, rolling `summary` |
| `MemoryMessage` | `stores.py:82` | Episodic | One row per turn; `embedding` + `embedding_dim` |
| `MemoryFact` | `stores.py:123` | Semantic | Bitemporal: `valid_at`/`invalid_at`, `created_at`/`expired_at`, `supersedes_id` |
| `MemoryProfile` | `stores.py:160` | Semantic | The structured JSONB card, unique per `(subject_id, tenant_id)` |
| `MemoryWriteLog` | `stores.py:178` | Audit | Append-only: op, `before`, `after`, reason, model, trace |
| `MemoryConsolidationJob` | `stores.py:201` | Queue | `PENDING → RUNNING → DONE/ERROR` |

Three enums: `MemoryOrigin` (`stores.py:36`), `WriteOp` (`stores.py:45` — add / update /
invalidate / noop / prune / delete), `ConsolidationStatus` (`stores.py:56`).

**Isolation posture, stated in the module docstring** (`stores.py:11-15`): app-level
`WHERE` filtering is *primary*, NULL-safe and dialect-independent. `tenant_id` is a plain
indexed column with no cross-package foreign key, because the tenancy table is a host
concern. Postgres RLS is an additive belt a host wires itself.

---

## The read path

### `recall()` — selection

**`aegis/src/aegis/memory/recall.py:373`**. Signature:

```python
async def recall(
    session: AsyncSession, *, subject_id: str, session_id: str, persona: str | None,
    query: str, query_vec: list[float] | None, config: MemoryConfig,
    tenant_id: int | None = None, spec: MemorySpec | None = None,
) -> RecallBundle
```

`RecallBundle` (`recall.py:56-75`) carries five fields: `profile_text`, `facts`,
`episodic`, `skills`, `running_summary`. Selection only — no token budget, no layout.

Five private tier functions:

| Function | Line | What it does |
|---|---|---|
| `load_raw_window` | `recall.py:91` | Last `raw_window_turns` (40) of this session, oldest-first |
| `_recall_facts` | `recall.py:122` | Embedded ANN over *valid* facts → composite → top-`n_fact` (6). Falls back to `ORDER BY valid_at DESC` when `query_vec is None` |
| `_recall_profile` | `recall.py:185` | Direct lookup → `spec.render_profile(...)` |
| `_recall_episodic` | `recall.py:210` | RRF-fuses a recency list and a vector list, both drawn from turns *outside* the raw window |
| `_recall_skills` | `recall.py:300` | `os.listdir(spec.SKILLS_DIR)` → `spec.select_skills(...)` → read markdown |

**The tenant predicate is single-sourced** at `recall.py:45-53`:

```python
def _tenant_clause(model, tenant_id):
    if tenant_id is None:
        return model.tenant_id.is_(None)
    return model.tenant_id == tenant_id
```

`None` means the *null-tenant scope*, never "any tenant". The docstring at `recall.py:8-13`
explains why: recall output goes verbatim into a prompt.

**`_bump_recall_access`** (`recall.py:322`) is the one write recall performs: `access_count + 1`
and `last_access_at = now` on every fact and message actually recalled, then **commits**.
The commit is deliberate and documented (`recall.py:333-339`) — the recall session is
otherwise read-only and its caller never commits, so a bare `flush()` would be rolled back
on session close and the frequency signal would stay permanently zero.

### `assemble_working_memory()` — budgeting and layout

**`aegis/src/aegis/memory/working.py:322`**. It calls `recall()`, fetches the raw window,
and delegates to the **pure** `build_working_text()` (`working.py:140`), which takes no
session and is therefore unit-testable with no database.

Layout order (`working.py:42`):

```python
_LAYOUT = ("profile", "facts", "skills", "summary", "episodic", "raw")
```

Eviction order (`working.py:45`):

```python
_EVICT_ORDER = ("raw", "episodic", "summary", "skills", "facts", "profile")
```

Budget arithmetic (`working.py:162`):

```python
budget = config.ctx_token_cap - config.answer_reserve - count_tokens(query)
```

With defaults that is `8000 − 1200 − |query|`. If the result is `<= 0` it returns an empty
`AssembledMemory` rather than a partial one.

Each tier is greedily filled by `_fill()` (`working.py:108`) up to
`per_tier_caps[tier] * budget` and the global budget, with cross-tier dedup on a `key`
(facts key on `subject|predicate`, messages on `msg:{id}`). Then a final **eviction loop**
(`working.py:253`) drops one item at a time until the rendered text fits — necessary
because the join separators between sections can nudge the total over.

`AssembledMemory` (`working.py:61-84`) returns `text`, `recalled_fact_ids`,
`recalled_message_ids`, `tokens_used`, and `conversation`.

**`conversation` is the one to know about.** It is the surviving raw turns in OpenAI chat
shape, derived from the *assembled* raw section rather than from `raw_turns` directly
(`working.py:276-292`), so it inherits the token budget for free, then hard-capped at the
last 12 turns (`_CONVERSATION_TURN_CAP`, `working.py:54`) and filtered to
`("user", "assistant")` roles only (`working.py:58`). It exists so the pre-retrieval query
rewriter gets a real transcript — see the bug story in `30-deep-dive.md`.

**Spotlighting.** The episodic tier is untrusted, so its header carries the spotlight
instruction (`working.py:173`) and each item is wrapped by
`aegis.retrieval.spotlight.spotlight()` (`working.py:315`). Putting the instruction *in
the header* means it is budgeted with the tier and disappears if the tier is evicted —
you never ship an instruction about content that is not there.

---

## The vector layer

**`aegis/src/aegis/memory/vector_ops.py`**. `MemoryVectorIndex` (`vector_ops.py:68`)
wraps a `ChromaVectorStore`.

- `MemoryVectorIndex.local(path=...)` (`vector_ops.py:87`) — embedded Chroma, real HNSW,
  `:memory:` for tests. **This is the production constructor too**: the deployment target
  forbids installing a vector server, so `main.py`'s lifespan binds this one with
  `VECTOR_STORE_PATH`. It fails loud on construction if the directory is unusable.
- `MemoryVectorIndex.server(url=..., api_key=...)` (`vector_ops.py:92`) — a live Chroma
  node, fails loud on construction if unreachable. Kept as a seam for a deployment that
  *can* run one; nothing in Aegis's own wiring uses it (see ADR 0009).

`search_rows()` (`vector_ops.py:184`) is the three-step contract documented at
`vector_ops.py:196-208`:

1. `_sync_subject()` (`vector_ops.py:116`) mirrors newly-added embedded rows into the
   `(table, dim)` collection, bounded by a per-scope **high-water mark** on the primary key
   (`vector_ops.py:145-146, 182`).
2. ANN-search the vector store, metadata-filtered by subject (+ tenant), over-fetching
   `k*4 + 16` when a validity/predicate filter will run (`vector_ops.py:233`).
3. Re-fetch the hit ids from SQL under the same scope plus `valid_only`/`predicate`
   (`vector_ops.py:255-269`). The SQL row is the source of truth.

Collections are named `aegis_mem_{table}_d{dim}` (`vector_ops.py:112-114`), so a lite
256-dim vector is never compared against a 3072-dim one — the dimension check is expressed
as collection routing.

Every vector-store call is synchronous, so each runs under `asyncio.to_thread`
(`vector_ops.py:178-179, 236`) to keep the hot recall path off the event loop.

`topk_by_cosine()` (`vector_ops.py:311`) is a thin function over the process-wide default
index — the name is historical (it used to be an in-Python cosine loop) and the signature
was kept so call sites did not change.

Process-wide default: `get_default_index()` (`vector_ops.py:286`) builds an embedded index
on first use; `set_default_index()` (`vector_ops.py:299`) installs a server-backed one.
The backend does exactly that at
**`backend/src/app/main.py:185-193`**, gated on `stores_enabled and not is_dev`.

---

## The write path

### `consolidate()`

**`aegis/src/aegis/memory/consolidate.py:811`**:

```python
async def consolidate(
    session, *, subject_id, session_id, config, complete, embed,
    tenant_id=None, trace_id=None, spec=None,
) -> ConsolidationResult
```

`complete` and `embed` are **injected callables**, so the whole path is offline-testable
with scripted fakes. It does **not** commit — the caller owns the transaction boundary
(`consolidate.py:842-843`).

`ConsolidationResult` (`consolidate.py:111-130`) has five counters: `added`, `updated`,
`invalidated`, `noop`, **`rejected`**. `rejected` is separate from `noop` on purpose:
"the model returned garbage" and "there was nothing to do" must not look the same in your
metrics.

Phase 1 — `_extract_candidates` (`consolidate.py:284`): one `ModelRole.CHEAP` call over the
running summary plus the last 10 turns (`_LAST_M_TURNS`, `consolidate.py:75`), parsed with
`spec.FactExtraction.model_validate_json`, filtered by `confidence >= config.tau_extract`
(0.55).

Phase 2 — `_reconcile` (`consolidate.py:573`), per candidate:

1. `topk_by_cosine(..., k=10, valid_only=True)` for neighbours.
2. **Dedup short-circuit** (`consolidate.py:608-624`): top cosine `>= config.dedup_cos`
   (0.97) **and** same predicate → bump access, log a NOOP, no second LLM call.
3. `_decide_op` (`consolidate.py:312`) — a cheap JSON call returning
   `{op, target_id, reason}`, parsed into `_DecideOp` (`consolidate.py:133`) with an
   unknown op defaulting to `noop`.
4. `_resolve_target` (`consolidate.py:159`) — see below.
5. Apply: `_apply_add` (`:538`), `_apply_update` (`:418`), `_apply_invalidate` (`:478`).

`_reconcile` **returns the applied candidates** — only the ops that genuinely reached the
store — and `_update_profile` (`consolidate.py:738`) is fed *that* list, not the raw
extractor output (`consolidate.py:884-892`). Within a batch, several applied facts can map
to the same profile field; they are merged in ascending confidence so the most confident
value wins (`consolidate.py:760`).

### The concurrency guard

Both `_apply_update` and `_apply_invalidate` mutate under a guarded UPDATE:

```python
# consolidate.py:439-450
update(MemoryFact)
  .where(MemoryFact.id == target.id,
         MemoryFact.invalid_at.is_(None),
         MemoryFact.expired_at.is_(None))
  .values(expired_at=now)
res = await session.execute(guard)
if (res.rowcount or 0) == 0:   # a concurrent writer already moved it
    result.noop += 1
    return False
```

`rowcount == 0` means another writer already superseded that row. The function returns
`False`, and the caller does **not** append the candidate to `applied` — so a write that
never happened cannot move the profile.

### `_resolve_target` — the refusal rule

**`consolidate.py:159-197`.** The decide-op returns a `target_id`. The resolution rules:

- `target_id` names one of the neighbours the model was shown → resolved.
- `target_id` names something else → **refused**, with a reason naming the failure.
- `target_id` omitted and exactly one neighbour → defaulted (referent unambiguous).
- `target_id` omitted with several neighbours → refused as ambiguous.
- `target_id` omitted with no neighbours → refused (nothing to target); the caller then
  treats the candidate as a plain ADD (`consolidate.py:651-653`).

A refusal writes a `NOOP` audit row carrying the reason and increments
`result.rejected` (`consolidate.py:631-649`). Nothing is written to the fact table.

### The queue

- `enqueue_consolidation` (`consolidate.py:784`) inserts a `PENDING` row and **commits**
  synchronously on the request path.
- `sweep_pending` (`consolidate.py:972`) claims each job with a guarded
  `PENDING → RUNNING` update (`consolidate.py:997-1010`) so two sweepers cannot double-run
  it, consolidates, then marks `DONE` or `ERROR`. Errors are caught per job so one bad job
  cannot wedge the queue.
- Every sweep cycle also runs `prune_forgotten` (`consolidate.py:900`) in its own
  try/commit (`consolidate.py:1046-1051`) — a prune failure must never break consolidation.

`prune_forgotten` uses a cheap SQL prefilter (live + `access_count == 0`) and applies the
exponential-decay test in Python, because that arithmetic is not portable across SQLite
and Postgres (`consolidate.py:916-919`). Archival sets `expired_at = now` and writes a
`PRUNE` log row — never a delete.

---

## The scoring maths

**`aegis/src/aegis/memory/scoring.py`** — pure, no I/O, deliberately separated so the
ranking maths is unit-testable in isolation (`scoring.py:5-9`).

| Function | Line |
|---|---|
| `recency_decay(age_days, half_life_days)` | `scoring.py:53` — `0.5 ** (age/half_life)`; non-positive half-life → 1.0 |
| `minmax(values)` | `scoring.py:70` — constant or empty column → **all zeros** (`scoring.py:86`) |
| `score_candidates(candidates, config, *, half_life_days)` | `scoring.py:91` — the four-term weighted composite; frequency is `log1p(access_count)` (`scoring.py:116`) |
| `rank_top(...)` | `scoring.py:126` — stable sort, highest first |
| `ForgetPolicy.is_archivable(...)` | `scoring.py:160` |

`RecallCandidate` (`scoring.py:21-50`) is the ORM-decoupled scorable: `key`, `text`,
`relevance` (precomputed cosine — so the maths never touches an embedding), `age_days`,
`importance`, `access_count`, `payload`.

---

## Configuration

**`aegis/src/aegis/memory/config.py:49-116`.** Every knob, with defaults:

| Knob | Default | Meaning |
|---|---|---|
| `raw_window_turns` | 40 | Verbatim recent turns loaded |
| `k_fact` / `n_fact` | 20 / 6 | Fact recall fan-out / kept |
| `k_epi` / `n_epi` | 20 / 4 | Episodic fan-out / kept |
| `n_skill` | 2 | Procedural skills per turn |
| `consolidation_every_n` | 4 | Consolidate every N turns |
| `tau_extract` | 0.55 | Minimum extractor confidence |
| `dedup_cos` | 0.97 | Same-predicate NOOP threshold |
| `w_rel / w_rec / w_imp / w_freq` | 1.0 / 0.5 / 0.5 / 0.1 | Composite weights |
| `half_life_days_fact` / `_epi` | 30 / 3 | Recency half-lives |
| `ctx_token_cap` | 8000 | Hard ceiling on the assembled block |
| `answer_reserve` | 1200 | Tokens reserved for the answer |
| `forget_floor` / `forget_min_age_days` | 0.05 / 90 | Prune thresholds |
| `per_tier_caps` | see `config.py:31-46` | Fractions of budget per tier |
| `cache_*` | enabled / 900s / 0.05 / 512 | The derived semantic cache |

`per_tier_caps` are **independent ceilings, not a partition** — they sum to 1.25 and that
is intentional (`config.py:34-37`): whichever tiers come first in priority order win the
shared budget.

`MemoryBackend` (`config.py:16-28`) is a `Literal["postgres", "redis", "off"]`. The
docstring is unusually honest: only `postgres` and `off` are live paths; `redis` is
retained for API compatibility and behaves like `postgres`, because facts never lived in
Redis — Redis is the *derived cache*.

---

## The derived cache

**`aegis/src/aegis/memory/cache.py`.** `MemorySemanticCache` (`cache.py:357`) dispatches
to one of two backends:

- `_RedisVLBackend` (`cache.py:209`) — real RedisVL `SemanticCache` over RediSearch, with
  `subject_id`/`tenant_id` as filterable tag fields (`cache.py:261-265`).
- `_InMemoryBackend` (`cache.py:105`) — an explicit, **labelled** fallback implementing the
  same semantics (real TTL, cosine threshold, subject+tenant scoping, `max_entries`
  eviction) in pure Python, with an injectable clock so TTL is testable.

Every hit carries `backend` (`"redisvl"` or `"in-memory"`, `cache.py:54-55`) so nobody can
confuse the two paths. `from_config()` (`cache.py:418`) picks: `cache_enabled=False` →
`None`; `require_redis=True` with no URL → **raises**; otherwise Redis if a URL is given,
else the labelled fallback.

`invalidate()` on the Redis backend (`cache.py:314-345`) uses a **`FilterQuery`** over the
tag fields, not a vector probe — see `30-deep-dive.md` for why that distinction is a bug
story rather than a detail.

---

## Streaming

**`aegis/src/aegis/memory/stream.py`** wraps the lifecycle in AG-UI events:

| Function | Line | Emits |
|---|---|---|
| `stream_assemble` | `stream.py:75` | `STEP_STARTED("recall_memory")` → optional `memory_cache` (hit/miss) → `memory_recall` → `STEP_FINISHED` |
| `stream_add` | `stream.py:199` | Runs `consolidate`, **commits**, emits `memory_write`, then invalidates the cache |
| `stream_forget` | `stream.py:253` | `forget_fact`, commit, `memory_write` (op `delete`), invalidate |

The ordering in `stream_add` is the consistency contract in code: `consolidate` →
`session.commit()` (`stream.py:236`) → `_emit_evict` (`stream.py:249`). Authoritative SQL
is durable before the derived cache drops.

`AssembleLike` (`stream.py:44`) is a structural protocol so the streaming seam never
threads a DB session — the host facade opens its own.

---

## Explicit CRUD

**`aegis/src/aegis/memory/crud.py`** — `list_facts` (`:32`), `get_fact` (`:63`),
`forget_fact` (`:79`). Forget is **soft by default**: it closes both time axes
(`crud.py:123-127`) so the fact leaves hot recall while the row survives for audit.
`hard=True` deletes outright, for a genuine data-subject erasure. Either way a `DELETE`
op is written to the audit log (`crud.py:129-139`).

---

## How the backend wires it into the agent

**`backend/src/app/agent/deps.py:91-287`** defines the concrete `MemoryDeps` that satisfies
`aegis.agent.deps.MemoryDeps` (a structural Protocol at
`aegis/src/aegis/agent/deps.py:166-200`). It holds `config`, `complete` and `embed`, and
opens its **own tenant-scoped session per call**, so the agent graph never threads a
session.

```python
# backend/src/app/agent/deps.py:280-287
@classmethod
def default(cls) -> MemoryDeps:
    from app.core.llm import complete
    from app.memory.config import MemoryConfig
    from app.retrieval.gateway import default_embed
    return cls(config=MemoryConfig(), complete=complete, embed=default_embed())
```

`assemble()` (`deps.py:106-138`) embeds the query itself if none was supplied
(`deps.py:119-124`), reads the tenant from the governance context (`deps.py:80-88`), calls
`set_tenant_scope(session, tenant_id)`, then `assemble_working_memory`.

`persist()` (`deps.py:140-235`) writes the user and assistant turns, increments
`turn_count`, and — every `consolidation_every_n` turns — calls `enqueue_consolidation`
(which commits) and fires a background task (`deps.py:222-235`).

**The embedding-reuse gate** (`deps.py:169-170`):

```python
vec_dim = len(query_vec) if query_vec is not None else None
user_embedding = query_vec if vec_dim == EMBED_DIM else None
```

A lite/256-dim vector is *recorded by its dimension* but never stored as a
recall-comparable embedding, because a mismatched-dimension embedding would silently
corrupt cosine recall.

**Background tasks are tracked, not fire-and-forget** (`deps.py:67-77`): every
`create_task` is added to a module-level set so the event loop cannot GC it mid-flight,
with a done-callback that logs any exception. The durable `PENDING` job row is the real
recovery seam behind it.

`_run_consolidation` (`deps.py:256-278`) calls **`sweep_pending`**, not a raw
`consolidate` — because a raw consolidate would leave the job `PENDING` and the interval
sweeper would re-run it, paying for a duplicate extract/decide/summary pass.

### The graph nodes

**`aegis/src/aegis/agent/graph.py`**:

- `recall_memory` (`graph.py:640`) — inert unless `deps.memory` and `session_id` and
  `memory_subject` are all present. Wired **plain**, not through `_timed`
  (`graph.py:1143`), so a no-op emits nothing at all and the single-shot trace is
  byte-identical.
- `persist_memory` (`graph.py:685`) — same posture, on the tail after `stream`.
- `answer_memory` (`graph.py:434`) — the memory *specialist*: recalls, then answers
  directly from the recalled block, skipping RAG, ML, planning, the gate and tools.
- `_recall_vector` (`graph.py:1278`) — supplies the query embedding for both memory
  branches, preferring `state["query_vec"]` and falling back to `deps.embed_query`.

Both memory nodes are **best-effort**: a store failure is logged and degrades to no recall
(`graph.py:661-664`) or an unstored turn (`graph.py:709-710`). Memory never fails a run.

### The background sweeper

**`backend/src/app/main.py:93-124`** runs `_run_memory_sweeper`: an in-process asyncio
task that opens a short-lived session and calls `sweep_pending` each cycle, swallowing
errors so a transient DB blip cannot kill it. Started only when `stores_enabled`
(`main.py:201-213`).

---

## What is deliberately *not* here

- No LLM binding inside `aegis.memory` — `complete` and `embed` are always injected.
- No domain vocabulary — that is the `MemorySpec`.
- No model call on the read path. `assemble_working_memory` is fully deterministic
  (`working.py:13-14`); regenerating a summary is a background consolidation job.
- No host schema, no FastAPI, no config object.

**Next:** [`30-deep-dive.md`](30-deep-dive.md) — consistency, concurrency, and the bugs.
