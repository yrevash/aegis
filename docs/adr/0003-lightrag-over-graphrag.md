# ADR 0003 — LightRAG (not Microsoft GraphRAG) for graph+vector RAG

- **Status:** Accepted
- **Date:** 2026-08-03
- **Deciders:** Team
- **Related:** `docs/architecture/backend.md` §4 (retrieval), `app/retrieval/NOTES.md`
  (verified LightRAG API), ADR 0001 (LiteLLM gateway).

## Context

The problem statement is revealed **blind on the day** (`docs/hackathon/brief.md` §1),
so on the day we generate a synthetic domain corpus and must **index it fresh in
the first ~2 hours** — indexing cost and wall-clock time are on the critical path,
not a background batch job. Three constraints shape the retrieval engine:

1. **Day-one machine:** 16 GB RAM, **no GPU, no Docker** (`docs/hackathon/brief.md`
   §3). Nothing heavy may run locally; the only remote calls are the model APIs.
2. **We want graph *and* vector retrieval.** Graph traversal answers relationship
   questions; vector search answers similarity questions. The stores are already
   chosen — **Neo4j** (graph) + **local Postgres/pgvector** (vectors +
   relational) (`docs/architecture/backend.md` §4, §6) — so the pipeline must build and query
   over both.
3. **Fast, cheap re-indexing.** Because the corpus is synthesised on the day and
   may be regenerated as the domain is refined, indexing has to be repeatable and
   inexpensive, and extraction/embeddings must run **via API** through the
   LiteLLM gateway (ADR 0001), keeping local compute near zero.

The two graph-RAG engines in scope are **Microsoft GraphRAG** and **LightRAG**
(`lightrag-hku`). Both extract entities+relationships with an LLM and support
graph+vector retrieval. They differ in what they do at indexing time.

## Decision

Use **LightRAG** (`lightrag-hku`) as the retrieval pipeline, over **Neo4j**
(`Neo4JStorage`) + **pgvector** (`PGVectorStorage`), with entity extraction and
embeddings driven **via API** through the gateway (`gpt-4o-mini` for extraction,
`text-embedding-3-large` for embeddings). LightRAG **skips GraphRAG's expensive
community-detection + community-summarization step**, so `insert()`-time indexing
is fast and cheap — the right trade for indexing freshly-generated synthetic data
under the on-the-day clock.

LightRAG is *the pipeline*; Neo4j and pgvector are *the stores*. It ingests
documents (`ainsert`), builds the graph + embeddings, and retrieves over both at
query time (`aquery` with `mode="mix"`). Reranking is deliberately kept as our
**own** pipeline stage (LLM-as-reranker) rather than LightRAG's optional
`rerank_model_func`, so the two-stage retrieve→rerank split stays explicit,
testable, and independent of the LightRAG version (`app/retrieval/NOTES.md`).
Backends are wired through the env vars LightRAG's storage impls read
(`NEO4J_*`, `POSTGRES_*`), derived from our settings.

## Consequences

- **+** Indexing is fast and cheap on the day: no community-summarization pass
  means far fewer LLM calls and far less wall-clock time to first query — exactly
  what the ~2-hour blind-start window needs.
- **+** Nothing heavy runs locally: extraction and embeddings are API calls
  through the gateway, so the 16 GB / no-GPU / no-Docker machine is respected.
- **+** True graph+vector retrieval over the stores we already run (Neo4j +
  pgvector) — one pipeline serves relationship and similarity queries.
- **+** Reranking stays in our own stage, so the two-stage split is unit-testable
  and not coupled to LightRAG internals.
- **−** No community summaries means weaker **global/thematic** ("summarise the
  whole corpus") queries than GraphRAG's community reports. Accepted: our value is
  agentic, targeted retrieval, not whole-corpus synthesis; `mode="mix"` covers
  local+global retrieval well enough for the demo.
- **−** LightRAG moves fast and its query-return shapes differ across versions
  (`QueryContextResult` vs plain `str`); mitigated by handling both shapes
  defensively (`app/retrieval/NOTES.md`) and pinning `>=1.0`.
- **Note:** retrieved content is treated as untrusted — **Azure Spotlighting**
  marks it as data, not instructions, and content is validated before it is
  written to the graph (`docs/architecture/backend.md` §4; `app/retrieval/spotlight.py`,
  `validation.py`).

## Alternatives considered

- **Microsoft GraphRAG.** The reference graph-RAG implementation, and its
  community detection + **community summarization** genuinely helps global,
  thematic questions. Rejected for the day-one context: that summarization step is
  the expensive part — many extra LLM calls and much longer indexing — which is
  precisely wrong when the corpus is generated on the day and must be indexed in
  the first two hours on a constrained machine. Its strength (whole-corpus
  synthesis) is not our differentiator.
- **Naive vector-only RAG (pgvector alone).** Simplest and cheapest to index — no
  graph build at all. Rejected because it throws away the **relationship**
  retrieval that makes the knowledge-graph animation and multi-hop reasoning
  possible; the graph is both a capability and a demo asset (`docs/hackathon/brief.md`
  §7 money-shot). LightRAG gives us the vector tier *and* the graph for roughly
  the same API-driven indexing cost.
