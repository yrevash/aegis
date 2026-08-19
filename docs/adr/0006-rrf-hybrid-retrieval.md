# ADR 0006 — Reciprocal Rank Fusion for explicit hybrid retrieval (vs mix-only)

- **Status:** Accepted
- **Date:** 2026-08-05
- **Deciders:** Team
- **Related:** ADR 0003 (LightRAG over GraphRAG),
  `app/retrieval/fusion.py`, `app/retrieval/pipeline.py`, `app/retrieval/memory.py`.

## Context

The pipeline claimed "hybrid retrieval," but fusion was **delegated entirely** to
LightRAG's `mode="mix"`, which returns one pre-blended graph+vector list — the pipeline
did no explicit fusion of its own and could not name *where* a passage came from. Worse,
the semantic cache ran **before** recall and could **replace** it: a 0.95-cosine hit
returned a stored answer and skipped recall+rerank entirely, so a *different* question
could get a stale-but-similar answer with no fresh provenance. The no-database "lite"
path was keyword-overlap only — markedly weaker than the "hybrid" story implied, and a
real day-of risk on the blind problem.

The 2026 consensus hybrid pipeline is *retrieve wide from several retrievers → fuse with
Reciprocal Rank Fusion → rerank to top-k → generate*. RRF is chosen specifically because
it is **rank-only**: it needs a document's *rank* in each list, sidestepping the
score-incompatibility that breaks naive weighted fusion of a cosine score against a
BM25/graph-proximity score.

## Decision

Make fusion **explicit and provenance-tagged**, shared by both the store-backed and
lite paths:

- Add `app/retrieval/fusion.py` — a pure `reciprocal_rank_fusion(ranked_lists, k=60)`
  that merges origin-tagged `RankedList`s (`score(d) = Σ 1/(k + rank_i(d))`), unions the
  contributing `RetrievalOrigin`s per surviving candidate, and exposes `collect_origins`
  for honest `provenance.origins`.
- The pipeline recalls **vector + graph** (LightRAG lists, or the lite backend's split
  lists) **plus a dependency-free BM25 list** computed over the recalled pool, and RRF-
  fuses them before the LLM reranker. Every result carries `Provenance(origins=[...],
  fusion=RRF)`, streamed as a `provenance` SSE event.
- **Demote the cache** to a conservative near-exact front layer: raise the
  semantic-answer threshold to **0.985** and tag every hit with `CacheProvenance`
  (kind, original query, timestamp) — the cache accelerates near-identical requests, it
  never silently substitutes a similar one (Open Decision D4).
- Upgrade the **lite** backend (`InMemoryKnowledgeBackend`) to a genuine mini-hybrid:
  brute-force cosine over local hashed embeddings (a **vector** list) + a co-occurrence
  **graph** expansion list, fused by the *same* RRF and reranked by the same core — so
  lite and full differ only in their stores, with no Faiss/ANN/GPU.

## Consequences

- **+** Provenance is honest and demoable: "vector + graph fused via RRF" is a fact the
  UI and audit can show, and the offline eval (`app/eval/`) asserts `fusion == rrf` over
  multiple origins as a quality signal.
- **+** The cache stops being a *quality* risk and becomes an *honest efficiency* story:
  near-exact hits still count on the token dashboard, but never launder a stale answer.
- **+** Lite mode is a real hybrid retriever, closing the biggest gap between the
  "hybrid" claim and the no-database fallback — the day-of safe path stays strong.
- **+** RRF is ~15 lines of pure Python, no new dependency, and unit-testable in
  isolation (`tests/retrieval/test_fusion.py`).
- **−** RRF needs *ranked lists per retriever*; since LightRAG `mix` returns one
  pre-fused list, we keep it as a single tagged list and add BM25 as the second, rather
  than making two LightRAG calls (Open Decision D6) — honest fusion at the cheapest
  latency, revisited only if graph-only queries underperform.
- **−** The higher cache threshold lowers the visible hit-rate; we accept the drop as
  the correct trade under a rubric that scores *both* efficiency and quality.
- **Note:** RRF's `k` (default 60) is the community-standard damping constant; larger
  `k` flattens the rank weighting.

## Alternatives considered

- **Keep LightRAG `mix` as the only fusion.** Simplest, but opaque (no per-origin
  provenance), non-configurable, and leaves the lite path keyword-only — it cannot back
  the "explicit hybrid, provenance-shown" claim the rubric rewards.
- **Weighted score fusion (α·cosine + β·bm25 + γ·graph).** Tunable, but fuses
  incomparable score scales; the weights are fragile and dataset-specific, exactly what
  RRF's rank-only formulation avoids.
- **A local cross-encoder reranker instead of fusion.** Rejected here for two reasons, and
  **one of them was false.** The true half stands: a cross-encoder is not a fusion strategy —
  it cannot merge two recall lists, it can only reorder one, so it was never an alternative
  to RRF and the two now compose (RRF fuses, the cross-encoder reorders what RRF produced).
  The false half was "needs a GPU or a heavy model": `fastembed`'s ONNX `TextCrossEncoder`
  needs neither. **The number this ADR carried was the wrong one, by 20x.** Phase 4 D6
  measured a 33M-parameter cross-encoder at **p50 1.44 s (p95 1.55 s) over a 20-candidate
  pool of 400-word chunks** on the 16 GB / no-GPU box (`spikes/rerank_bench.py`, recorded in
  `docs/dev_new_docs_v2/phase-04-ingestion.md` under D6). ~72 ms is the **per-passage**
  constant, not the pool figure — a cross-encoder's cost is linear in pool size, so quoting
  the per-passage number for a 20-candidate pool understates the call by the pool size. This
  is the same class of error D6 was raised to correct (its own estimate said 150–400 ms), and
  it is corrected here so a reader who inherits this decision inherits the measurement rather
  than the estimate. It is still the reranker after RRF, with the LLM-as-reranker as its loud
  fallback (`aegis.retrieval.local_reranker`) — `recall_top_k` is the latency lever.
