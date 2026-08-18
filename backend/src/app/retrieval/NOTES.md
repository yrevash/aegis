# retrieval — research notes & targeted APIs

Verified against current upstream docs/source on 2026-08-03 (do not trust memory —
these libraries move fast).

## LightRAG (`lightrag-hku`, main branch — pins to `>=1.0`)

Source of truth: `HKUDS/LightRAG` `lightrag/lightrag.py`, `lightrag/base.py`,
`lightrag/utils.py`, `lightrag/kg/{neo4j_impl,postgres_impl}.py`.

- Construct: `LightRAG(working_dir=..., llm_model_func=..., embedding_func=EmbeddingFunc(...),
  kv_storage=..., vector_storage="NanoVectorDBStorage", graph_storage="Neo4JStorage",
  doc_status_storage=...)`.
- **Storage-backend availability is narrower than `lightrag.kg.STORAGES` suggests.**
  Re-verified against the installed `lightrag==1.5.6` on 2026-08-15 by importing each
  impl module: `ChromaVectorDBStorage` is *declared* in the map but
  `lightrag.kg.chroma_impl` **does not ship**, and `FaissVectorDBStorage` needs a
  `faiss` wheel that is not installed. `NanoVectorDBStorage` (file-backed, pure Python,
  LightRAG's own default) imports cleanly and is what we select — it needs no server
  binary, which is the hard constraint on the target deployment. Note also that
  importing an impl module can trigger `pipmaster` to attempt a `pip install` of its
  driver, so probe them deliberately, not casually.
- `EmbeddingFunc(embedding_dim: int, func: callable, max_token_size: int | None = None)`
  (`lightrag/utils.py`). `func` is an async `(list[str]) -> list[list[float]]`.
- `llm_model_func` signature: `async (prompt: str, system_prompt: str | None = None,
  history_messages: list | None = None, **kwargs) -> str`.
- Optional `rerank_model_func` field exists — we deliberately do NOT use it and rerank
  in our own pipeline stage instead (see below), so the two-stage split is explicit,
  testable, and independent of the LightRAG version.
- **Mandatory init sequence** before any insert/query:
  `await rag.initialize_storages()` then
  `from lightrag.kg.shared_storage import initialize_pipeline_status;
  await initialize_pipeline_status()`.
- Ingest: `await rag.ainsert(text_or_list)`.
- Query: `await rag.aquery(query, param=QueryParam(mode="mix", top_k=..., only_need_context=True))`.
  `QueryParam` (`lightrag/base.py`): `mode ∈ {local,global,hybrid,naive,mix,bypass}`,
  `only_need_context: bool`, `top_k`, `chunk_top_k`, `enable_rerank`.
  With `only_need_context=True` recent versions return a `QueryContextResult` with
  `.context: str` and `.raw_data: dict` (entities/relations/chunks under `data`); older
  versions return a plain `str`. Our backend handles both shapes defensively.
- Backends are configured by **env vars** read inside LightRAG's storage impls:
  Neo4j → `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`;
  Postgres (KV + doc-status only) → `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`,
  `POSTGRES_PASSWORD`, `POSTGRES_DATABASE`. `LightRAGBackend` derives these from
  `settings.neo4j_uri`/`settings.postgres_dsn` and sets them on `os.environ` before
  constructing LightRAG (LightRAG offers no direct kwargs for them). The vector store
  needs **no** env var: `NanoVectorDBStorage` persists to JSON under `working_dir`.

## Reranker — local ONNX cross-encoder, LLM-as-reranker behind it (phase 4, D6)

**This section used to say a local cross-encoder was "off the table" because the platform
runs on a 16 GB, no-GPU machine. That reason was wrong**, and it kept a measured +12.1 pp
recall@5 / +17.2 pp MRR@3 improvement switched off. A cross-encoder does not imply a GPU:
`fastembed`'s `TextCrossEncoder` runs on **onnxruntime** with no torch, and the checkpoint we
ship (`jinaai/jina-reranker-v1-tiny-en`) is 33M parameters and ~130 MB. Measured on the
16 GB M3: 0.14 s to load, ~74 ms p50 to rerank a 20-candidate pool, +134 MB RSS. The
constraint that is real is the **query clock**, and that is what the model size is chosen
against — see `docs/dev_new_docs_v2/phase-04-ingestion.md` §D6 for the numbers and
`spikes/rerank_bench.py` to reproduce them.

So the order is:

1. **Local cross-encoder** (`aegis.retrieval.local_reranker`) — deterministic, free per
   query, no gateway call, and it scores the (query, passage) pair jointly.
2. **LLM-as-reranker** (`aegis.retrieval.reranker`) — the fallback, reached only on a local
   failure that is logged at **ERROR**, and the primary for a deployment that sets
   `RERANK_LOCAL=false`. A single `ModelRole.CHEAP` (escalatable to `REASONING`) scoring
   prompt that grades each wide-recall candidate 0–10 and returns strict JSON. Candidate
   text is **spotlighted before it reaches the scoring model** (that reranker consumes
   untrusted retrieved content, so it is itself an injection surface); the local encoder
   needs no such screen because it emits a float, not a continuation.

What never happens is a silent fall-through to *no* reranking. `observability.rerank.engine`
reports which of the two produced the order.

## Semantic cache — `redis.asyncio` (redis-py `>=5.1`)

`import redis.asyncio as redis` → `redis.from_url(url, decode_responses=True)`; async
`get`/`set(key, val, ex=<ttl_seconds>)`, `sadd`/`smembers`, `aclose()`. Two tiers:
exact-match key first (sha256 of normalised query+persona), then an embedding
nearest-neighbour scan over indexed entries (cosine ≥ threshold). No RediSearch vector
index is required (keeps it portable / infra-light).

## Azure Spotlighting — indirect prompt-injection defence

Microsoft, "Defending Against Indirect Prompt Injection Attacks With Spotlighting"
(arXiv 2403.14720) + MSRC 2025 guidance. Three instantiations: **delimiting**,
**datamarking** (interleave a marker token through the text), **encoding**. We combine
**delimiting + datamarking** plus an explicit instruction that any marked text is DATA,
never instructions. See `spotlight.py`.
