# Phase 4 — Ingestion and retrieval, rebuilt on evidence

**Status: awaiting approval. Nothing here is implemented.**

**Depends on Phase 3.** Ingestion is the primary consumer of the job substrate
([`phase-03-platform-spine.md`](phase-03-platform-spine.md) §3.1–3.4) and of `run_events`
(§3.6) — not a decorative user of it. Stage-level resumability, parse serialisation, batching
across queued documents, the live log and scheduled re-indexing are all **ingestion
correctness properties that the substrate provides and nothing else does**; §2.6 states each
one as a requirement. This phase **runs on that substrate**; it does not build a queue of its
own. Starting Phase 4 before 3.1–3.4 land means writing a second, weaker job system that then
has to be deleted.

This supersedes the v1 ingestion plan, which was written from assumption. Thirteen of its
factual claims turned out to be wrong — including one of mine that was not merely overstated
but backwards. Everything below is either measured on hardware, or cited to a paper or
primary source, and every decision carries its reason, its trade-off, and what we would do
with more room.

Research behind it: [`research/docling-verified.md`](research/docling-verified.md) ·
[`research/ingestion-sota.md`](research/ingestion-sota.md) ·
[`research/retrieval-sota.md`](research/retrieval-sota.md) ·
[`research/eval-design.md`](research/eval-design.md)

---

## 1. What we are actually building

On 30 August somebody hands us documents nobody has seen. The pipeline below turns those
bytes into answers a jury can check.

```
PDF ─▶ Docling ─▶ structured tree ─▶ chunker ─▶ enriched chunk ─▶ index
       (layout +   (headings with     (structural   (title · date ·      │
        tables)     real levels,       packing)      heading path)       │
                    page + bbox)                                         │
                                                                         ▼
                                        ┌──────────────────────────────────┐
  question ─────────────────────────────▶  dense  ·  BM25  ·  graph        │
                                        └──────────────┬───────────────────┘
                                                       ▼
                                              RRF fusion (top ~20)
                                                       ▼
                                        local ONNX cross-encoder rerank
                                                       ▼
                                              top 6 ─▶ answer + citations
                                                       ▼
                                          verbatim span check on every cite
```

Three of those boxes do not exist today, and one exists but is disconnected.

---

## 2. What is wrong right now

### 2.1 There is no ingestion at all

Not weak — absent. Zero hits for `docling`, `pypdf`, `PyMuPDF`, `pdfplumber`, `unstructured`
across both source trees and both `pyproject.toml` files. `backend/src/app/api/routes.py` is
**3,066 lines and 57 `@router` decorators** with no upload route, no ingest route, no job
route.

`Retriever.ingest()` exists, and `backend/src/app/retrieval/pipeline.py:164` wraps it as a
governed public entry point — **and no route reaches either.** Verified: `grep -n ingest
backend/src/app/api/routes.py` returns two comments and no call. Whatever is in the LightRAG
working directory on the demo machine was put there by hand.

### 2.2 The production backend has no corpus-wide keyword search

This is the largest quality gap in the system and it is invisible from the outside.

`LightRAGBackend` implements `recall_ranked` but **not** `keyword_recall`. The lite backend
(`memory.py`) implements both. So in full mode the BM25 arm takes the pool-scoped branch: it
re-ranks the 20 candidates dense retrieval already found, computing IDF over those 20
documents. **It cannot surface anything dense retrieval missed** — which is the entire reason
to have a lexical arm.

Why that matters, with numbers: on the BEIR-style comparison in the retrieval research, BM25
alone scores **0.644 R@5 and beats dense alone at 0.587**; hybrid reaches 0.695. We ship the
hybrid *architecture* with one arm disconnected.

The failure mode this creates is specific and demo-relevant: an exact identifier — a case
number, a policy code, a part number — is exactly what dense embeddings are worst at and BM25
is best at. A jury asking "what does clause 7.3.2 say?" is the query most likely to fail.

### 2.3 The reranker is locked out by an expired premise

`reranker.py`'s docstring, and `MODULE_REFERENCE.md` as a locked decision:

> no local cross-encoder, because the deploy target is a 16 GB, no-GPU machine

That premise is false, and it is the same mistake I made about the VLM pipeline. `fastembed`
0.8.0 ships `TextCrossEncoder` over ONNX with **no torch dependency**. A 33M-parameter
reranker is ~130 MB. The measured value of reranking in the research: **+12.1 pp recall@5 and
+17.2 pp MRR@3**, and on the T2-RAGBench anchor (`arXiv:2604.01733`, 23k queries over
text+table documents) reranking is worth roughly **5.5× what per-chunk LLM enrichment is
worth** — at a per-query cost rather than a per-chunk one.

### 2.4 The chunk prefix carries the weakest of the three useful fields

We prepend the heading path (`[Returns > Refund window]`). The ECIR 2026 field ablation
(`arXiv:2601.11863`, on SEC filings) removed fields one at a time: dropping **company and
year** caused *severe* degradation; dropping **section titles** caused only a modest
Context@K drop and no Title@K effect.

So we ship the junior partner of the winning technique and omit both senior ones — at zero
model-call cost. Adding document identity and date takes Context@5 from **33.3% → 55.0%**.

### 2.5 The corpus table cannot express a tenant — and this one is a blocker

**Verified in source, `backend/src/app/data/models.py:141-156`.** The `Chunk` model declares
exactly six columns:

```python
# backend/src/app/data/models.py:149
__tablename__ = "chunks"
id, doc_id, persona, content, embedding, meta
```

**There is no `tenant_id`.** So `chunks` is not one of the tenant-scoped tables Phase 1
registered, it carries no RLS policy, and its isolation today is the vector-store namespace
plus whatever `meta` happens to hold.

Every other defect in §2 is a quality gap. This one is a **security regression waiting on a
feature**: §2.2's fix queries `chunks` directly through Postgres FTS, and with no `tenant_id`
column there is no predicate to filter on. Shipping the keyword arm before the column means
shipping a known cross-tenant read path — re-opening precisely the leak Phase 1 closed,
through a path Phase 1 never covered because the arm did not exist yet.

**This is D4b. It is a blocker, not a note.** It gates task 4.7 and it appears in the
definition of done as a hard gate.

### 2.6 There is nowhere durable to run an ingest

An ingest is a **multi-stage pipeline, minutes long, with billed model calls in it**. It cannot
live in an HTTP request. Today there is no queue at all: the one job pattern in the repo is
`aegis/src/aegis/memory/consolidate.py:983-1005`, a SELECT-then-guarded-UPDATE with **no lease,
no reaper, and an `attempts` counter read nowhere** — Phase 3 §1 documents why copying it is
the wrong move.

Phase 3 §3.1–3.4 builds the substrate. Phase 3 §3.6 builds the `run_events` record the live log
reads from. **Neither is this phase's work, and this phase must not build a second one.**

What ingestion specifically requires from it — every item is correctness, not convenience, and
each is stated as a requirement in Phase 3's *"Why this exists"* section:

| Requirement | What breaks without it |
|---|---|
| **Stage-level progress on the job row** | A failure at the graph stage re-parses 200 pages. At ~1.1 s/page that is a four-minute penalty for a ten-second bug. |
| **A per-job-type concurrency limit** | Docling is CPU-bound and single-process. Two concurrent parses on a 16 GB box contend and both get slower. **Parses serialise; embed calls do not** — one limiter, two different values. |
| **Batching across documents, not only within one** | Embedding is billed per call. Batching across the queue, not just across one document's chunks, is a real saving against $100 of credit — and it is only possible because the work is queued. |
| **Multi-document upload** | Ten files dropped at once must become ten queued jobs with visible queue positions. Without a queue they are ten timeouts. |
| **A durable job row to stream from** | The live ingest log the tenant watches **is job progress**. There is no separate log channel to invent — task 4.12 is a projection over the job row and `run_events`. |
| **The scheduler** | Re-indexing on a cadence is a direct requirement, not a nice-to-have. See task 4.13. |
| **Budget context on the job** | Ingest spends money. A cost preflight before the job runs is the difference between refusing a 200-page upload and discovering it half way through. |

If Phase 3 §3.1–3.4 has not landed, this phase has nowhere to put its longest-running operation
and should not start.

---

## 3. Every decision, with its reason and trade-off

This is the section to argue with. Nothing here is "because the laptop is small."

### One rule that decides most of this

**Ingest is cheap. Queries are not.**

| | Ingest-time | Query-time |
|---|---|---|
| Happens | Once per document, in a worker | Every question |
| Who waits | Nobody | A person |
| Budget | Hours are fine | Sub-second |

The user's ruling: *"chunking is one time thing… if it takes an hour more but quality improves
then it's ok."* So we buy quality at ingest and we measure latency at query time.

That is the whole framework. It settles TableFormer (ACCURATE), and it is why the reranker —
which sits on the query clock — is the one place we benchmark rather than assume.

### What we are deliberately NOT adding

The evidence is unusually clear that the wins come from **having three components**, not from
elaborating around them:

- **RAGSmith:** exhaustive optimisation of the entire advanced-RAG space buys **+3.8% average.**
- **T2-RAGBench:** reranking is worth **~5.5×** what per-chunk LLM enrichment is worth.
- Semantic, proposition and LLM-driven chunking all **lose** to structural chunking.

So: no document expansion, no hypothetical-question generation, no per-chunk summaries, no
local enrichment models, no second embedding field. Each is a real technique with a real
paper; none of them beats simply connecting BM25 and turning on the reranker, and every one
adds a moving part that can fail on stage.

The one local model we take is the reranker (~250M, query clock, benchmarked). Nothing else
runs locally.

**Future scope, written down so it is a decision and not an oversight:** doc2query expansion,
HyPE, contextual retrieval (+2.2pp on top of hybrid), late chunking. All revisit after 30 Aug.

### D1 — Docling, standard pipeline (layout + TableFormer). Not the VLM pipeline.

**Measured on a 16 GB M3, same RAM budget as the target:**

| | Standard | granite-docling-258M |
|---|---:|---:|
| seconds/page | **1.10** | **281.0** |
| peak RSS | 2,199 MB | **2,027 MB** |

**Reason:** 255× slower. That is the whole argument.

**The trade-off, stated honestly:** the VLM is not worse at everything. It is a single model
that reads layout, tables and reading order together, and on genuinely messy documents —
scanned, rotated, heavy multi-column — it can beat a layout-model pipeline. We are giving that
up.

**What I got wrong, and why it matters here:** the previous plan said the VLM was "flatly
impossible at 16 GB". It uses **less** memory than the pipeline we are shipping. The
objection was backwards. If throughput ever stops being the binding constraint — a GPU box, a
batch job, an overnight ingest — **the VLM should be reconsidered on merit**, and this
paragraph exists so nobody inherits my wrong reason.

**What would be better with more room:** run both, on the same documents, and measure
retrieval quality rather than parse fidelity. Nobody has told us the VLM's *downstream* effect
on answers; we are inferring from speed alone.

### D2 — Turn on heading hierarchy. Both settings, not one.

Docling ships `heading_hierarchy_options.enabled = False`. Its layout model emits
`SECTION_HEADER` **with no level**, so every heading lands at level 1.

| Configuration | Measured result on a 9-page PDF |
|---|---|
| defaults | `{1: 20}` — completely flat |
| `heading_hierarchy.enabled=True` alone | `{1: 16, 2: 4}` — **silent partial failure** |
| both, plus `generate_parsed_pages=True` | `{1: 8, 2: 6, 3: 4, 4: 1, 5: 1}` — real 5-level tree |

**Reason:** the middle row is the dangerous one. It looks like it worked. You would ship
flattened context for four fifths of your headings and never see an error.

**Cost:** +5.6% wall clock. **Trade-off:** none worth naming at that price.

**Depth is not free forever, though.** HiChunk (`arXiv:2509.11552`) finds gains improve L1→L3
and are **minimal beyond level 3**, and the whole effect is **+7 pp on evidence-dense corpora
but only +1 pp on sparse ones**. So this is a real win, not a large one, and it is corpus-
dependent. We do it because it is nearly free, not because it is transformative.

### D3 — `docling[rapidocr]`, and OCR decided per document

**Two separate corrections here.**

The install string in the old plan (`docling[format-pdf-docling,format-office,...]`) is
**invalid** — all five extras are silently ignored. And `docling[models-onnxruntime]`, its
stated fallback, installs **`onnxruntime-gpu`** on Windows.

On OCR: profiling a fully born-digital PDF showed OCR consuming **33.5 s of 38.1 s — 88% of
total runtime** — because figure regions contain no text cells and get sent to OCR anyway. The
per-*page* mitigation the old plan proposed already exists upstream at finer granularity and
does not help.

**Decision:** probe for a text layer per *document*, and set `do_ocr` accordingly.

**Trade-off:** a document that is 90% digital with one scanned page gets no OCR on that page,
and we lose it. **Mitigation:** report the per-document decision in the ingest log so it is
visible, not silent. **What would be better:** a per-page text-cell-density heuristic that
enables OCR only for pages below a threshold — maybe half a day, and it is on the more-time
list rather than in the plan.

### D3b — TableFormer stays on `ACCURATE` (the default)

**Changed 2026-08-17.** An earlier draft set `FAST`, inherited from a Docling CPU-guidance
note without asking which clock the cost lands on.

**Reason:** it lands on the ingest clock, which is the cheap one. Tables are where enterprise
PDF retrieval is won or lost — a mis-parsed table is a wrong answer with a confident citation,
and it is not recoverable downstream by any amount of reranking.

**Cost:** ACCURATE is roughly +0.8 s per table over FAST. On a 40-table document that is ~30
seconds, once, in a background worker.

**The user's ruling, which settles it:** *"we need accuracy not trying to be fast — retrieval
needs to be optimised, chunking takes some time it's ok."*

**When FAST would be right:** interactive parsing where a person waits for the result, or a
corpus large enough that ingest wall-clock becomes the binding constraint. Neither is us.

### D4 — Warm the converter at startup

Cold start is **50–120 seconds**, documented nowhere in Docling's own docs.

**Reason:** without this, the first upload of the day — which on 30 August is a jury handing
us a document — stalls for a minute with no explanation.

**Trade-off:** the worker holds ~2 GB resident from boot rather than on first use. On a 16 GB
machine running Postgres, Neo4j and Memurai, that is worth measuring rather than assuming;
the task includes doing so.

### D4b — 🚧 **BLOCKER** — `chunks` needs `tenant_id` BEFORE the BM25 arm lands

**This is the one hard gate in the phase. Nothing about D5 is safe to merge without it.**

**Verified in source and against the live database, `backend/src/app/data/models.py:141-156`.**
`chunks` columns are `id, doc_id, persona, content, embedding, meta`. **There is no
`tenant_id`.**

It is therefore not one of the 13 tenant-scoped tables Phase 1 registered, and it carries no
RLS policy — its isolation today is the vector-store namespace plus the `meta` payload.

**Why this blocks D5:** the corpus-wide keyword arm queries `chunks` directly through Postgres
FTS. With no `tenant_id` column there is no predicate to filter on, so a lexical hit could
return another tenant's passage — **re-opening precisely the leak Phase 1 closed**, through a
path Phase 1 never covered because the arm did not exist yet.

**The work, and it is not optional:**

- Add `tenant_id` to `chunks`, backfilled from the owning document.
- Register it in the tenant-scoped table catalogue so the boot-time catalog read-back covers
  it — the same registration `jobs` gets in Phase 3 §3.1.
- The FTS query carries the tenant predicate on the same row — which was the argument for
  Postgres FTS in D5 in the first place.
- Extend the live isolation test (Phase 1 task 1.6) to assert a lexical hit cannot cross
  tenants.

**Sequencing: task 4.6 lands in the same change as task 4.7, or 4.7 does not land.** Shipping
the keyword arm first and the column second means shipping a known cross-tenant path, and
"we'll add the column next" is exactly how it stays shipped.

### D5 — Connect the corpus-wide BM25 arm

Implement `keyword_recall` on `LightRAGBackend`, backed by **Postgres full-text search**.

**Reason:** §2.2. This is the single largest quality gap, and the evidence for lexical
retrieval on exact identifiers is not marginal.

**Why Postgres FTS specifically:** no new dependency, no second index to keep in sync, and —
the part that matters for us — **the tenant filter is a `WHERE` clause on the same row**.
After Phase 1, any retrieval path that cannot express tenant scope is a liability.

**Trade-off:** Postgres FTS is a weaker ranker than a dedicated BM25 implementation. Its
`ts_rank` is not true Okapi BM25, and stemming is per-language configuration. **What would be
better:** a real BM25 index, or Postgres 17's improvements, or `bm25s` — all rejected here on
the tenant-isolation and dependency grounds, and all listed under more-time.

### D6 — Add a local ONNX cross-encoder reranker (~250M, API fallback)

`fastembed` `TextCrossEncoder`, ONNX, no torch.

**Reason:** +12.1 pp recall@5, +17.2 pp MRR@3. Second-highest-value change available, and it
removes a per-query model call — which matters against $100 of credit.

**Why local is now allowed:** `reranker.py` is locked to API-only because *"the deploy target
is a 16 GB, no-GPU machine."* That premise expired — a ~250M ONNX cross-encoder is ~250 MB and
needs no GPU. It is the **only** local model we adopt.

**Size ceiling and why:** the reranker is on the **query** clock. ~250M over 20 passages is
roughly 150–400 ms on CPU, which a person does not notice. Larger models exist and score
better; they are not worth an extra second per question. **Benchmark the chosen model's real
latency before shipping it** — this is the one number in the phase we measure rather than
assume.

**Fallback:** the existing API reranker, on a **loud** failure. Never silently to no
reranking — a retrieval stage that quietly stops running is exactly the class of defect Phase
1 and 2 spent their time removing.

### D7 — Enrich the chunk prefix: document title · type · date · heading path

Deterministic. Zero model calls.

**Reason:** Context@5 **33.3% → 55.0%**. The strongest quality-per-hour item in the phase —
roughly an hour of work.

**Two rules the evidence is unambiguous about**, and we already satisfy both: **prefix, not
suffix**, and **into the embedded text, not metadata-only**. The second is specifically what
LangChain's header splitter gets wrong.

**Trade-off:** every prefix token is a token not spent on content, and it perturbs BM25's IDF
statistics slightly. At four short fields this is comfortably worth it; at twelve it would not
be.

**Explicitly rejected, with reasons:** entity/keyword lists in chunk text (no paper isolates
the effect, and it distorts IDF) and document-level summaries in every chunk (Anthropic tested
and rejected them).

### D8 — Tables as first-class objects with a natural-language summary

The table's *embedded* text is a generated NL summary; the structured grid is stored
alongside and returned with the citation.

**Reason:** enterprise PDFs are mostly tables, and a pipe-delimited grid embeds terribly — the
numbers dominate the vector and the semantics vanish. Prior research calls this the single
largest retrieval-quality lever on real PDFs.

**Trade-off:** the summary costs one model call per table at ingest. That is a real cost and
it scales with the document. **Mitigation:** cache by table content hash, so re-ingesting the
same document is free.

**I have moved this out of the cut list.** The old plan named tables as first-to-cut; both
research reports say that is inverted.

### D9 — Page and bounding-box provenance, threaded from day one

**Reason:** it is what makes a citation checkable rather than decorative — "page 7, this
paragraph" instead of "somewhere in this document". Directly worth jury marks under Problem
Understanding and Business Impact.

**Why day one and not last:** the old plan deferred it because "the cost is threading it
through the write path." That is the argument for doing it *first*. Threading a field through
a pipeline you have already built is strictly more work than building the pipeline with the
field in it.

### D10 — Verbatim span verification on every citation

For each citation, assert the quoted span appears verbatim in the cited chunk.

**Reason:** ~40 lines of code that converts "the model says it cited this" into "we checked".
It is also the same primitive the gold set uses, so it is built once and used twice.

**Trade-off:** it catches fabricated quotes, not fabricated *reasoning* over real quotes.
Worth saying plainly rather than overselling.

### D11 — Keep our chunker. Do not adopt semantic or LLM chunking.

**Reason, and this one is unusually clear in the 2026 literature:** semantic, proposition
(DenseX) and LLM-driven chunking **all lose to plain structural chunking** on in-corpus
retrieval, at 10²–10⁴× the cost. Propositions score 15–27% worse. LumberChunker is 1,600×
slower for no gain. Fixed-size runs in under a second where DenseX takes 15 hours and lands
within 2 points.

Our chunker is already on the winning side of that result. **Changing it would be churn that
loses quality**, and it is the kind of change that sounds impressive in a pitch.

**Trade-off:** structural chunking is only as good as the structure it is given — which is
exactly why D2 matters, and why a document with no headings at all degrades to word windows.

### D12 — Keep RRF, but stop describing it as unambiguously superior

`fusion.py` presents RRF's parameter-free nature as a clear win. `arXiv:2210.11934` finds
convex combination beats RRF both in- and out-of-domain, and that RRF **is** parameter
sensitive.

**Reason to keep it anyway:** we are pointed at an unknown corpus with no tuning set. Under
genuine corpus uncertainty, a method with nothing to tune is the right default.

That is a defensible position — but it is a **trade-off**, not a superiority claim, and the
docstring should say so before a judge asks.

### D13 — Reject the fashionable query-side techniques

HyDE, multi-query expansion, query decomposition, reflection loops: **evidence is negative or
contested**, and every one costs latency and tokens.

**Reason to say no:** RAGSmith found exhaustive optimisation of the entire advanced-RAG space
buys **+3.8% on average**. The double-digit moves come from *having* the three right
components — structure, hybrid, rerank — not from tuning around them.

**One nuance:** our existing `agentic.py` sufficiency loop is **current and on the defensible
side** of the evidence, because it is bounded, additive and retrieval-focused rather than
reflection-focused. Recommendation is *keep it and do not expand it*.

**But one real bug in it:** `_fallback_sufficiency` declares any non-empty context sufficient
— textbook premature stopping, and it makes the loop a no-op exactly when the network is
failing. That gets fixed.

### D14 — Position the graph arm honestly

`arXiv:2607.26497`: LightRAG scores 48.0 against BM25's 74.7 at the smallest corpus tier, with
super-linear (b=1.36) build cost.

At hackathon corpus size none of that bites, and the graph's demo and explainability value is
real. **But it should be positioned as the relational/explainability arm, not the quality
engine** — because if we claim it is the quality engine, the ablation in §5 will contradict us
on our own slide.

### D15 — Adopt no eval framework

RAGAS's headline metrics are LLM-computed (cost and nondeterminism in CI); DeepEval's pattern
is already implemented natively in `regression.py`; an existing test **actively bans both**
from `aegis.evals`'s import graph, protecting a real architectural property. Phoenix 14.6 is
already installed if a recognised metric name is wanted.

**Trade-off:** we forgo the credibility of a recognised library name. A jury scores the number
and its error bar, not the library that printed it.

---

## 4. The tasks

| # | Task | Days | Notes |
|---|---|---|---|
| 4.0 | Spike on the real Windows box | 0.25 | Docling install, model prefetch, cold start, one real PDF |
| 4.1 | `convert.py` seam — **with page/bbox from line one** | 0.5 | Docling never leaks past this module |
| 4.2 | Text-layer probe + header/footer/page-number stripping | 0.25 | Running headers otherwise trip our Jaccard dedup and look like a bug on stage |
| 4.3 | `chunk_sections()` — feed pre-structured sections to the existing packer | 0.25 | The chunker survives intact |
| 4.4 | Enriched prefix: title · type · date · heading path | 0.15 | Highest quality-per-hour in the phase |
| 4.5 | `documents` table, upload route, **`ingest_document` job on the P3 substrate** | 0.75 | No queue machinery here — Phase 3 §3.1–3.4 owns it |
| 4.6 | 🚧 **`chunks.tenant_id`** + RLS + isolation test | 0.25 | **Blocker for 4.7.** See D4b — it ships in the same change or 4.7 does not ship |
| 4.7 | **Corpus-wide `keyword_recall`** on Postgres FTS | 0.5 | The largest quality gap. Gated on 4.6 |
| 4.8 | `corpus_version` bump + cache invalidation | 0.25 | Plugs into Phase 1's seam |
| 4.9 | **Local ONNX cross-encoder reranker** + model benchmark | 0.5 | Second largest |
| 4.10 | Table objects with NL summaries, hash-cached | 0.4 | Promoted out of the cut list. **TableFormer stays on ACCURATE** — see D3b |
| 4.11 | Span-anchored gold set + naive-baseline ablation | 0.5 | The number that goes on the slide |
| 4.12 | Live ingest log — a projection over the job row | 0.4 | The tenant watches their document being read. Cheaper than the old estimate because the job row already carries stage progress |
| 4.13 | Scheduled re-index on the P3 scheduler | 0.25 | Direct requirement — see the task |
| 4.14 | Verbatim citation verification | 0.15 | ~40 lines, reuses 4.11's primitive |

**Total: 5.15 days.** That is honest rather than padded — see the cut order.

Three of these have detail that does not fit a table row.

### 4.5 — `documents`, the upload route, and the `ingest_document` job (0.75d)

**There is no job machinery in this task.** The claim, the lease, the reaper, the retry, the
dead-letter status, the idempotency key, the priority, the admission cap, the cancellation flag
and the worker are all Phase 3 §3.1–3.4. Do not reimplement any of them, and specifically do
**not** reuse `consolidate.py`'s SELECT-then-UPDATE claim — Phase 3 §1 is the argument against
it and Phase 3 §3.2 migrates `memory_consolidation_job` off it.

What this task owns:

- **A `documents` table** — `id, tenant_id, filename, content_type, byte_size, sha256, status,
  page_count, uploaded_by, created_at`. Tenant-scoped and registered in the catalogue, like
  `chunks` in 4.6 and `jobs` in Phase 3 §3.1.
- **`POST /documents`** — multipart upload, following the existing `/voice/transcribe` upload
  pattern rather than inventing a second one. It **stores the bytes and enqueues; it never
  parses inline.** Ten files dropped at once is ten enqueues and ten queue positions in the
  response, which is the whole reason multi-document upload works at all.
- **The `ingest_document` job type**, with its stages named on the row:

  ```
  parse → chunk → enrich → embed → index → graph
  ```

  `payload` carries `{document_id, corpus_version}`; the job writes the **completed stage**
  back on every transition. **A retry resumes at the first incomplete stage.** A failure in
  `graph` must never re-run `parse` — at ~1.1 s/page a 200-page document is four minutes of
  work being thrown away for a ten-second bug, and it is the single most likely thing to
  happen on 29 August.
- **The idempotency key is `ingest:{sha256}:{corpus_version}`.** Re-uploading the same bytes
  under the same corpus version is a no-op that returns the existing job. Bumping
  `corpus_version` (4.8) is what makes a re-ingest a *new* job rather than a duplicate.
- **Concurrency limits, per job type, and they differ:**

  | Job type | Limit | Why |
  |---|---|---|
  | `ingest_document` (parse stage) | **1** | Docling is CPU-bound and single-process at ~1.1 s/page. Two concurrent parses on a 16 GB box contend and both get slower; the queue is what serialises them without dropping work. |
  | embed / index stages | ≥ 4 | Network-bound. Serialising these wastes the whole ingest window for nothing. |

  This is why Phase 3's substrate needs a **per-job-type** limit rather than one global worker
  count, and it is stated there as a requirement (§"What this means for how it is built").
- **Batching across documents, not only within one.** The embed stage pulls from the queue, so
  a batch may span several queued documents. That is a real API round-trip and cost saving
  against $100 of credit, and it is only reachable because the work is queued.
- **Cost preflight against the tenant budget before the job runs.** Estimate from
  `page_count × chunks/page × embedding unit cost`, plus one model call per table if 4.10 is
  on. A 200-page document with 80 tables is real money. Refuse at upload with the existing
  `BudgetExceeded` reason string, not half way through the embed stage.
- **Cancellation is the substrate's `cancel_requested` flag**, checked at each stage boundary.
  A tenant who uploaded the wrong file should be able to stop the spend.

### 4.12 — The live ingest log, as a projection (0.4d)

**Do not build a log channel.** The tenant watching their document being read is watching **job
progress**: the stage transitions written by 4.5, plus the per-stage `run_events` rows from
Phase 3 §3.6. `GET /documents/{id}/events` re-streams them; a live run tails them.

Two things it must show, because they are the honest parts:

- **The OCR decision from D3** — per-document, named on screen. A silent `do_ocr=False` on a
  scanned page is exactly the failure D3 trades away, so it has to be visible.
- **The structure that was found** — the heading-level histogram from D2. A multi-column PDF
  that parsed into scrambled chunks looks fine in an answer and obvious in a histogram
  (see Risks).

Because the record is durable, a refresh mid-ingest resumes the view rather than losing it —
which is why replay is cheap here and expensive everywhere it is not built on `run_events`.

### 4.13 — Scheduled re-indexing (0.25d)

A direct requirement: *"re indexing pipeline can be structured in a way that it runs in set
duration and in the meantime user own db take care."* This is Phase 3 §3.5's `job_schedules`
table and its materialiser — **this task only registers the schedules.**

| Schedule | Cadence | Job |
|---|---|---|
| Corpus re-index | nightly | Re-embed and re-index documents whose `corpus_version` is behind the current one. Idempotency-keyed per Phase 3 §3.5 (`sched:{id}:{fire_time}`), so two workers materialising the same tick produce one job. |
| Graph rebuild | nightly, after re-index | The graph arm is the expensive one and it is the least latency-sensitive (D14). |
| Orphan sweep | daily | Chunks whose document is deleted; documents stuck in a non-terminal status past the lease horizon. |

Cadence is per-tenant configurable through the Phase 3 §3.7 settings catalogue
(`ingest.reindex.cadence`), so it is a dashboard control rather than a constant — which is the
"0 code change" goal applied to the one background process a tenant will actually ask about.

**DB clock only, never the worker's** (Phase 3 §3.5). A skewed demo laptop must not fire a
nightly re-index during the pitch.

---

## 5. The eval that proves it

**Anchor gold truth to a verbatim answer span in the source document, never to a chunk id.**
This is the load-bearing idea. A naive baseline chunks in fixed windows; we chunk structurally
— so chunk ids are not comparable across arms, which is how most RAG ablations quietly become
meaningless. A span check (`normalise(span) in normalise(chunk.text)`) is comparable across
any chunking strategy, gives citation verification for free, and survives the Docling swap
without rebuilding the set.

**Metrics at the k values this codebase actually uses** (`recall_top_k=20`, `final_top_k=6`),
not textbook 5/10:

- **recall@20** — the pool ceiling. The ingestion and hybrid number.
- **recall@6** — what the generator actually reads.
- The **gap between them isolates the reranker.**
- precision@6, MRR@20.

**The ablation:** A0 naive (fixed windows, dense only, no rerank) → A1 structure parse → A2
enriched prefix → A3 hybrid → A4 shipped, plus leave-one-out probes for graph and BM25. Two
rules keep A0 honest: it uses the **same embedder** as every other arm, and it is what a
competent team ships in a weekend.

**The statistical honesty that goes on the slide:** paired design, Wilson intervals, paired
bootstrap. **n=50 defends a ≥15-point delta and cannot defend a 5-point one.** Saying that
before a judge asks is worth more than the extra decimal place.

**The day-of procedure** (2 hours, one person, ~$1.60) is designed in
[`research/eval-design.md`](research/eval-design.md) §4, including a five-rung fallback ladder
for when the gateway is down. The single most valuable preparation item: **a timed rehearsal
on an unfamiliar corpus before 30 August.**

---

## 6. Cut order — decided now, not at 2am

1. **The ingest-log detail panels** (4.12 keeps the live stage tail; the OCR-decision and
   heading-histogram panels go). Replay is *not* a cut — it is free once the events are in
   `run_events`.
2. **bbox renderer** — keep the data, drop the visual overlay
3. **Table NL summaries** — keep tables as objects, drop the generated summary
4. **Local reranker benchmark** — ship the 33M default rather than comparing three
5. **The nightly graph rebuild in 4.13** — keep the corpus re-index schedule, drop the second

**Never cut:** page provenance, `chunks.tenant_id` (4.6), the BM25 arm, stage-level job
progress, and the gold set. The first two are correctness; the last three are what turn claims
into evidence.

---

## 7. Definition of done

- [ ] A PDF uploaded through `POST /documents` is stored, enqueued, and **parsed by a worker,
      never inside the request**.
- [ ] `chunks.tenant_id` exists, is registered in the tenant-scoped catalogue, carries an RLS
      policy, and the live isolation test asserts **a lexical hit cannot cross tenants**. This
      is merged in the same change as `keyword_recall`, or neither is merged.
- [ ] `LightRAGBackend.keyword_recall` searches the **whole tenant corpus**, not the dense
      pool — proved by a query whose answer dense retrieval alone does not return.
- [ ] Heading levels come back as a real tree, not `{1: N}` — asserted on the golden fixture.
- [ ] **Killing the worker during the `embed` stage resumes at `embed`, not at `parse`** —
      tested by actually killing it, and verified by the parse stage not re-running.
- [ ] Three documents uploaded at once produce three queued jobs with queue positions, and
      **only one parse runs at a time**.
- [ ] An upload whose cost estimate exceeds the tenant budget is refused **at upload**, with
      the existing `BudgetExceeded` reason string.
- [ ] Re-uploading identical bytes under the same `corpus_version` returns the existing job.
      Bumping `corpus_version` produces a new one.
- [ ] Cancelling an in-flight ingest stops it at the next stage boundary and the spend stops
      with it.
- [ ] The ingest log streams stage transitions live, survives a page refresh, and names the
      per-document OCR decision.
- [ ] The nightly re-index schedule fires **once** per tick with two workers running.
- [ ] The reranker runs locally, its measured p50/p95 latency over 20 passages is recorded in
      the phase notes, and an induced failure falls **loudly** to the API reranker.
- [ ] Every citation's quoted span is verified verbatim against its chunk.
- [ ] The ablation table A0→A4 exists with paired bootstrap intervals, and the slide states
      what n=50 can and cannot defend.
- [ ] Full suites green, ruff clean, `next build` green.

## 8. Demo at the end of this phase

Drop three PDFs nobody has seen onto the console at once. Three jobs appear with queue
positions; one parses while the others wait, and the log names what it found — five heading
levels, forty tables, OCR off because the document is born-digital. Kill the worker while the
third document is embedding, and watch it come back and resume **at the embed stage**, not at
page one.

Then ask *"what does clause 7.3.2 say?"* — the exact-identifier query that dense retrieval
alone gets wrong — and get the right passage with a page number and a bounding box. Ask the
same question as the other tenant and get nothing, because `chunks` now knows who owns a row.

Finish on the ablation slide: A0 naive to A4 shipped, with the interval drawn on it.

---

## 9. Risks

**Docling on the real Windows box.** Everything is measured on macOS. 4.0 exists to find out
on day one rather than on 29 August. If it fails there, the fallback is `pypdf` plus our
existing chunker — materially worse, but not nothing.

**A jury PDF that parses badly.** Multi-column is a known Docling weakness
([#2067](https://github.com/docling-project/docling/issues/2067)) and it **fails silently into
scrambled chunks**. Mitigation: a multi-column golden fixture in 4.0, and the ingest log
(4.12) showing the heading histogram so a human can see it went wrong.

**The reranker's first local weight.** New failure class in the serving path. Must fail loudly
to the API reranker, never silently to no reranking.

**Ingest cost on a large document.** One model call per table, plus embeddings. A 200-page
document with 80 tables is real money against $100. 4.10 caches by content hash and 4.5
preflights the estimate against the tenant budget before the job runs — that preflight moved
into the phase when the substrate arrived, because a queued job is the only place a preflight
has to stand on.

**Phase 4 slips if Phase 3 slips, and there is no way around it.** Every task from 4.5 onward
either runs on the substrate or streams from it. The temptation on a bad day is to write "just
a small worker for now" — that is the second job system Phase 3 §3.2 already exists to delete,
and it will be the thing that has no lease when a process dies on the 29th.

**Parse serialisation is easy to get wrong in the permissive direction.** A per-job-type limit
that defaults to "no limit" looks correct in every test that runs one document. Test it with
three concurrent uploads on the real box and watch the parse stage, not the queue depth.

---

## 10. What we would do with more time, ranked

1. **Per-page OCR triage** rather than per-document.
2. **Late chunking** — architecturally blocked today: it needs token-level embeddings from a
   local long-context model, and an embedding API returns one pooled vector. The prerequisite
   (embedder swap, 3072→768, full re-embed) is the expensive part, not the technique.
3. **Anthropic contextual retrieval** — strong alone, but only **+2.2 pp on top of hybrid**,
   at one LLM call per chunk. Genuinely good; simply not the best use of the next hour.
4. **A real BM25 index** rather than Postgres FTS.
5. **The VLM pipeline, measured downstream** — see D1. Reconsider the moment throughput stops
   binding.
6. **HyPE hypothetical questions** — large reported effect, weak methodology. Worth an
   experiment, not a commitment.
