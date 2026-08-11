# aegis.retrieval — Hybrid Retrieval (extraction) Design Spec

- **Date:** 2026-08-12
- **Branch:** `feat/aegis-module-contract`
- **Status:** Design (autonomous rollout — module 2 of 8)
- **Map:** `.superpowers/sdd/module-retrieval-map.md` (distilled extraction facts)

## 1. Goal

Extract the backend's hybrid retrieval into a standalone importable **`aegis.retrieval`**: structure-aware
chunking → dedup → poisoning validation → hybrid recall (vector + graph + hand-rolled BM25) → **Reciprocal
Rank Fusion** → LLM-as-reranker → spotlighted assembly, with a two-tier semantic cache, an agentic Self-RAG
loop, and honest provenance/citations. **LLM-agnostic** (inject completer + embedder), heavy deps
(lightrag/neo4j/redis/pgvector) under `aegis[retrieval]`, streaming citations over the AG-UI spine, honest
lite mode. `aegis.core` stays heavy-dep-free. Strangler shim keeps the backend green.

## 2. Why this is a clean extraction (from the map)

Retrieval is **already dependency-injected**: every heavy dep is lazy-imported inside methods; the LLM,
embedder, Redis client, and knowledge backend are all injected via Protocols (`protocols.py`). The only
production binding to the gateway is `gateway.py` (`default_complete`/`default_embed`). BM25, cosine, and
lite embeddings are pure Python. So extraction = sever schema/config couplings + replace `gateway.py` with
constructor injection + move the agentic loop in.

## 3. Design

### 3.1 Package layout (`aegis/src/aegis/retrieval/`)

Move (rebinding imports): `pipeline.py`, `models.py`, `protocols.py`, `lightrag_backend.py`, `cache.py`,
`answer_cache.py`, `memory.py` (lite backend), `fusion.py`, `reranker.py`, `query_rewrite.py`,
`spotlight.py`, `validation.py`, `vectors.py`, `chunker.py`. Add `agentic.py` (moved from
`app.agent.retrieval_loop` — it depends only on retrieval Protocols/models, so it belongs here). Add
`types.py` for the schema enums/models moved out of `app.api.schemas`. Add `stream.py` for AG-UI streaming.

### 3.2 Sever couplings

- **Schema types → `aegis.retrieval.types`** (pydantic-only): `RetrievalOrigin` (vector|graph|bm25|cache),
  `FusionMethod` (none|rrf|mix), `GraphNode` {id,label,kind}, `GraphEdge` {source,target,relation}.
  `app.api.schemas` re-exports these (identity) so the agent/API are unchanged.
- **Config** → a package `RetrievalConfig` (already exists) extended with store URLs
  (`postgres_dsn`, `neo4j_uri/user/password`, `redis_url`) + `embed_dim` (default 3072) + `stores_enabled`.
  Replace `app.config.get_settings` reads with this. No `app.config` import.
- **`ModelRole`** → a small `aegis.core` role enum OR keep rerank/extract roles as injected strings.
  Recommend: a minimal `aegis.core.models.ModelRole` (CHEAP/REASONING/GENERATION/EMBEDDING) since the
  gateway module (module 3) will own it — define it now in `aegis.core.models` (dependency-free).
- **LLM/embedder** → constructor injection. Delete `gateway.py`'s `app.core.llm` resolvers; the two
  builders (`build_default_retriever`, `build_lite_retriever`) take `complete: CompleteFn` +
  `embed: EmbedFn` args. The backend shim passes `app.core.llm.complete/embed`.
- **`EMBED_DIM`** → package constant / config field.
- **`app.observability`** (span/semconv/SpanKind) → make optional/no-op inside retrieval; the AG-UI
  emitter (spine) + `aegis.observability` (module 8) own tracing. Use `aegis.core.events.SpanKind`.
- **`app.adapter.corpus`** → the lite `from_corpus` takes a caller-supplied corpus path/docs, not
  `importlib.resources.files("app.adapter.corpus")`.

### 3.3 AG-UI streaming (à la carte)

`aegis/src/aegis/retrieval/stream.py`: `async def stream_retrieve(retriever, query, emitter, *, persona=None)`:
```
async with emitter.step("retrieve", SpanKind.RETRIEVER):
    result = await retriever.retrieve(query, persona=persona)
    await emitter.custom(RETRIEVAL_CITATIONS, {
        "num_candidates": result.num_candidates,
        "sources": [{"id": s.id, "label": s.metadata.get("title", s.id), "score": s.score,
                     "origin": _origin_of(s), "snippet": s.text[:280]} for s in result.sources],
        "cache_hit": result.cache_hit,
        "provenance": {"origins": [o.value for o in result.provenance.origins],
                       "fusion": result.provenance.fusion.value,
                       "cache_kind": getattr(result.provenance.cache, "kind", None)},
        "graph_delta": {"nodes": [...], "edges": [...]}})
    return result
```
`RETRIEVAL_CITATIONS` already in `stream_names`. The rerank stage keeps `SpanKind.RERANKER` (via the emitter
if wired, else internal). No new stream_names needed.

### 3.4 Honest infra (preserve — already good)

Keep the explicit lite path (`InMemoryRedis` + `InMemoryKnowledgeBackend`), gated by `stores_enabled` —
**not silent**. Entity/relation counts stay `None` (honest unknown), not fabricated 0. Cache hits carry
`CacheProvenance` lineage. Tie the mode to `AEGIS_MODE` where sensible (full ⇒ require stores; lite ⇒
in-memory, loudly). No `except → in-memory` path.

### 3.5 Strangler shim

`backend/src/app/retrieval/` → thin shims delegating to `aegis.retrieval`, wiring `app.core.llm` as the
injected completer/embedder and `app.config` → `RetrievalConfig`. `app.api.schemas` re-exports the 4 moved
types. `app.agent.retrieval_loop` re-exports `aegis.retrieval.agentic`. Backend `AgentDeps` wiring
(`from app.retrieval import retrieve`) keeps working. Full backend suite green (minus the 2 env failures).

## 4. Extras

`aegis/pyproject.toml`: `retrieval = ["lightrag-hku>=1.0", "neo4j>=5.24", "redis>=5.1", "pgvector>=0.3", "asyncpg>=0.29"]`; add to `all`. All lazy-imported, so `import aegis.retrieval` works with none installed (lite path). Dep-free guard: `aegis.core` unaffected; add a guard that `import aegis.retrieval` pulls no heavy deps (they're lazy).

## 5. Testing & proof

Port the ~90 retrieval tests (all use injected fakes: FakeRedis/RecordingComplete/SequenceEmbed/FakeBackend —
prove no-infra operation): pipeline/RRF/cache/answer_cache/memory-lite/lightrag(faked)/fusion/reranker/
query_rewrite/spotlight/validation/chunker/provenance. Add: a streaming test (`stream_retrieve` emits
STEP(retrieve) → CUSTOM(retrieval_citations) → STEP_FINISHED with sane citation payload); an import-isolation
guard (`import aegis.retrieval` pulls no lightrag/neo4j/redis). Backend parity: full backend suite green
minus the 2 known-env failures; the agent's retrieve node still works through the shim. Live end-to-end:
retrieval_citations decodes on the frontend.

## 6. Definition of done

`aegis.retrieval` importable + `aegis[retrieval]`-installable, LLM-agnostic (inject completer/embedder),
`retrieve()`/`ingest()` preserved, streams citations over AG-UI, honest lite mode, `aegis.core` heavy-dep-free,
backend green through the shim (minus 2 env failures). Add `aegis.core.models.ModelRole` (dependency-free)
as a shared role enum for this and the gateway module.
