# retrieval — research notes & targeted APIs

Verified against current upstream docs/source on 2026-08-03 (do not trust memory —
these libraries move fast).

## LightRAG (`lightrag-hku`, main branch — pins to `>=1.0`)

Source of truth: `HKUDS/LightRAG` `lightrag/lightrag.py`, `lightrag/base.py`,
`lightrag/utils.py`, `lightrag/kg/{neo4j_impl,postgres_impl}.py`.

- Construct: `LightRAG(working_dir=..., llm_model_func=..., embedding_func=EmbeddingFunc(...),
  kv_storage=..., vector_storage="PGVectorStorage", graph_storage="Neo4JStorage",
  doc_status_storage=...)`.
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
  Postgres/pgvector → `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`,
  `POSTGRES_PASSWORD`, `POSTGRES_DATABASE`. `LightRAGBackend` derives these from
  `settings.neo4j_uri`/`settings.postgres_dsn` and sets them on `os.environ` before
  constructing LightRAG (LightRAG offers no direct kwargs for them).

## Reranker — LLM-as-reranker (design decision)

The fleet (`app.core.models`) has **no dedicated rerank model** and the platform runs on
a 16 GB, no-GPU machine, so a local cross-encoder is off the table. We therefore
implement **LLM-as-reranker**: a single `ModelRole.CHEAP` (escalatable to `REASONING`)
scoring prompt that grades each wide-recall candidate 0–10 for relevance and returns
strict JSON. Candidate text is **spotlighted before it reaches the scoring model**
(the reranker consumes untrusted retrieved content, so it is itself an injection
surface). See `reranker.py`.

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
