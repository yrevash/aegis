# aegis.memory — Long-Term Memory (extraction) Design Spec

- **Date:** 2026-08-12 · **Branch:** `feat/aegis-module-contract` · Module 4 of 8
- **Map:** `.superpowers/sdd/module-memory-map.md`

## 1. Goal

Extract the three-tier long-term memory (episodic · semantic/bitemporal · procedural + durable
consolidation) into a standalone importable **`aegis.memory`**: LLM-agnostic (inject completer/embedder),
domain-agnostic (inject a memory-spec), honest infra (portable pgvector-or-SQLite recall, explicit
recency-only degradation, durable consolidation queue). Establishes a small shared ORM foundation
`aegis.data` that memory and (later) governance build on — keeping `aegis.core` truly minimal.

## 2. Data-layer decision (the key wrinkle)

Memory's SQLAlchemy models currently register on the platform's shared `app.data.models.Base` with
`VectorType`/`JsonB`/`EMBED_DIM`. To decouple:

- **Create `aegis/src/aegis/data/`** (new, under an `aegis[data]` extra = `sqlalchemy[asyncio]>=2.0`, `pgvector>=0.3`):
  - `base.py` — a fresh `DeclarativeBase` (`AegisBase`), portable `VectorType` (pgvector `vector` on PG,
    JSON on SQLite — copied from `app.data.models`, no `app.*`), `JsonB`, and `EMBED_DIM = 3072`.
  - Optional: light `AsyncSession` type re-exports; NO app.config/engine coupling (the backend owns engine/session).
- `aegis.core` stays pydantic-only. **Extend the dep-free guard to also ban `sqlalchemy`** from `aegis.core`
  (sqlalchemy lives in `aegis.data`, not core).
- `aegis.memory` models (`stores.py`) register on `aegis.data.AegisBase`. The backend shim `create_all`s
  the aegis metadata alongside its own and applies RLS to the memory tables (via `bootstrap_rls`).
- Governance (module 6) reuses `aegis.data`.

## 3. Design (`aegis/src/aegis/memory/`)

Move (rebinding imports): `stores.py` (models → `aegis.data.AegisBase` + `VectorType`/`JsonB`/`EMBED_DIM`),
`recall.py`, `consolidate.py`, `scoring.py`, `vector_ops.py`, `working.py`, `tokens.py`, `config.py`. Add
`spec.py` (the `MemorySpec` Protocol) and `stream.py` (AG-UI).

### Sever couplings
- `app.retrieval.{fusion,models,vectors,spotlight}` → `aegis.retrieval.*` (exist). `app.api.schemas.RetrievalOrigin` → `aegis.retrieval.types`. `app.core.models.ModelRole` → `aegis.core.models.ModelRole`.
- `app.data.models` (Base/VectorType/JsonB/EMBED_DIM) → `aegis.data`.
- `app.adapter.memory_spec` → an injected **`MemorySpec` Protocol** (`aegis.memory.spec`): `FACT_EXTRACTION_PROMPT`, `PROFILE_FIELDS`, `SKILLS_DIR`, `IMPORTANCE_HINTS`, `FACT_TYPES`, `FactSchema`, `FactExtraction`, `render_profile(dict)->str`, `select_skills(query,persona,available)->list[str]|None`. The consolidation/recall functions take a `spec: MemorySpec` param (or a module-level injected default). `memory_subject_for` stays app-layer.

### Inject
completer (`CompleteFn`) + embedder (`EmbedFn`) [already]; `MemorySpec`; `AsyncSession` [already a param]; the sessionmaker/tenant-scope stay in the backend shim's `MemoryDeps`.

### AG-UI streaming (à la carte)
`aegis/src/aegis/memory/stream.py`: `stream_assemble(memory_deps_or_fn, ..., emitter)` emits
`emitter.step("recall_memory", SpanKind.CHAIN)` bracketing `emitter.custom(stream_names.MEMORY_RECALL,
{recalled_fact_count, recalled_message_count, tokens_used})`. `MEMORY_RECALL` already in stream_names.

### Honest infra (preserve)
Portable recall (subject-scoped SQL prefilter + Python cosine; no pgvector `<=>` at query time — works on
SQLite). Recency-only degradation EXPLICIT + dim-guarded (`embedding_dim` recorded; lite 256-dim never
recall-comparable). Redis tier declared-but-unwired (documented). Durable consolidation QUEUE
(`MemoryConsolidationJob` + `sweep_pending`), not fire-and-forget.

## 4. Strangler shim

`backend/src/app/memory/` → shims delegating to `aegis.memory`, injecting: `app.core.llm.complete` +
`app.retrieval.gateway.default_embed` (completer/embedder), `app.adapter.memory_spec` (the `MemorySpec`),
`app.data` session/tenant. `MemoryDeps` stays in `app.agent.deps` (unchanged facade). `app.data` `create_all`
+ `bootstrap_rls` must cover `aegis.memory`'s tables (register `aegis.data.AegisBase.metadata`). Graph nodes
(`recall_memory`/`answer_memory`/`persist_memory`) unchanged.

## 5. Testing & proof

Port `backend/tests/memory/` (7 files; already inject scripted fake complete/embed — offline). They run on
SQLite via the aegis Base. Add: a streaming test (`MEMORY_RECALL` emitted); an import-isolation guard
(`import aegis.memory` pulls no heavy retrieval deps; note it DOES pull sqlalchemy — that's fine, it's under
`aegis[data]`). Backend parity: full backend suite green (minus the 2 env failures); memory graph nodes work
through the shim; RLS on memory tables still enforced.

## 6. Definition of done

`aegis.data` (portable ORM base) + `aegis.memory` importable, LLM+domain-agnostic (inject completer/embedder/
memory-spec), recall/consolidate/working preserved, streams MEMORY_RECALL, honest infra intact, `aegis.core`
still minimal (pydantic-only; sqlalchemy banned from core), backend green through the shim (minus 2 env failures).
