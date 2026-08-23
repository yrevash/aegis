# Phase 4 — Ingestion and retrieval, rebuilt on evidence

> **Kept as a record, 2026-08-23.** This phase shipped. It survives the documentation
> clean-up because five source files, `backend/src/app/retrieval/NOTES.md` and
> `docs/adr/0006` cite its §D6 and D1 for measured numbers that exist nowhere else. The rest of the v2 plan — the
> master plan, the roadmap, the other phases, six research plans and five technology
> surveys — was deleted and is in git history (last full set: `2d8b84d`). **Links from
> here into `plans/` and `research/` are therefore dead**; the bodies are intact, only
> the cross-references are broken. See [`README.md`](README.md).



**Status when written: approved 2026-08-18, nothing implemented. It has since shipped —
see the banner above.**

> ## Amendments of 2026-08-18 — read before the body
>
> Phase 3 **landed** between this document being written and being approved, and four things
> below are now stale or incomplete. This block is the authority where it disagrees with the
> body.
>
> **A. The substrate exists.** §2.6 ("there is nowhere durable to run an ingest") is obsolete.
> Temporal, the six ingest stages (`parse → chunk → enrich → embed → index → graph`), per-queue
> concurrency, stage-level resume, the reconciler and debounced re-index all shipped. **This
> phase supplies stage handlers; it builds no orchestration.**
>
> **B. `documents` already exists** (`aegis/src/aegis/jobs/models.py`) with `tenant_id`,
> `content_sha256`, `status`, `completed_stage`, `workflow_id`, `page_count`, `chunk_count` and
> `UNIQUE (tenant_id, content_sha256)`. Task 4.5 shrinks to the upload route plus wiring; it
> does **not** create the table. Task 4.13 similarly reduces to supplying a handler.
>
> **C. `chunks.doc_id` and `documents.id` do not join** — `String(255)` against `BIGINT`. Not
> noted anywhere in the body. Fixed as part of D4b; see the revised DDL there.
>
> **D. RLS on `chunks` protects the keyword arm only.** Embeddings are not searched in
> Postgres. `VectorColumn` is a JSON column of record, and ANN runs on Chroma against **one
> shared collection** (`_LITE_COLLECTION = "aegis_lite_chunks"`) scoped by a metadata `where`
> filter. Adding `tenant_id` to `chunks` therefore fixes **one of three arms**. See the new
> **D4c**, which is a second blocker.
>
> **E. Task 4.0 found a blocker nobody planned for: `OMP_NUM_THREADS`.** Installing
> Docling puts `torch` in the venv, and `presidio-analyzer` then imports it on sight — so
> the *PII path*, not the parser, loads a second OpenMP runtime into the same process as
> `xgboost`. One order segfaults, the other deadlocks, and both suites died on it. Fixed
> with `OMP_NUM_THREADS=1` at the composition roots; recorded as **D4a**. Read it before
> touching anything that imports torch.
>
> Also recorded: **this Postgres has no `pgvector`** (`plpgsql` only). Nothing in this phase
> depends on it, but no task may assume a native vector column exists.
>
> Revised total: **~4 days**, down from 5.15, because the substrate absorbed the queue work.

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

Research behind it: `research/docling-verified.md` ·
`research/ingestion-sota.md` ·
`research/retrieval-sota.md` ·
`research/eval-design.md`

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

> **[CONFIRMED] 2026-08-19, task 4.9.** Every factual claim in this section held up when
> installed and measured: `fastembed==0.8.0` pulls onnxruntime and **no torch**, and the 33M
> reranker is **134 MB on disk, measured**. The lock is removed and the local reranker ships.
> The one number this section does *not* make and D6 does — the latency — did not survive;
> see the correction under D6.

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

**[MEASURED] 2026-08-19, task 4.9: the query budget is the one this phase overran.** The
reranker costs 1.44 s p50 on this machine, not the sub-second the table asks for. It is kept,
because it replaces a gateway call over the same twenty passages that is no faster and is
billed — but "sub-second" is now a target we miss by 0.44 s and say so, not a claim.

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

The one local model we take is the reranker (33M as shipped, query clock, **benchmarked
2026-08-19: 1.44 s p50 over 20 x 400-word chunks — see the correction under D6**). Nothing else
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

**[MEASURED] 2026-08-18, task 4.0, same 16 GB M3, `docling==2.120.3`, on the four Phase 4
fixtures** (`spikes/docling_spike.py`). The 1.10 s/page above is a text-dense number and does
not generalise — TableFormer on ACCURATE dominates, so a table-dense document costs ~3× more:

| Fixture | Pages | Tables | Parse | s/page | Peak RSS |
|---|---:|---:|---:|---:|---:|
| `bert-two-column.pdf` | 16 | 8 | 7.6 s | **0.47** | 1,422 MB |
| `transformer-single-column.pdf` | 15 | 4 | 6.4 s | **0.43** | 1,515 MB |
| `census-income-tables.pdf` | 67 | 40 | 214.3 s | **3.20** | **3,248 MB** |
| `irs-1040-instructions-tables.pdf` | 126 | 39 | 361.3 s | **2.87** | **3,381 MB** |

RSS is a single-document process peak (`ru_maxrss`), measured one document per fresh process;
the two small papers were measured in a shared process, so their figures include the models.

**Two planning numbers in this document are therefore wrong.** "~1.1 s/page" makes a 126-page
government PDF a two-minute parse; it is **six minutes**. And "a Docling parse peaks around
2.2 GB" (repeated in `aegis/src/aegis/jobs/stages.py` and `backend/.env.example`) is
**3.2–3.4 GB** on a document of that size — peak RSS scales with the document, not just with
the models. Both make the CPU queue's `max_concurrent_activities = 1` more clearly right, not
less: two concurrent parses of a document like these would want ~7 GB on a 16 GB box that is
also running Postgres, Neo4j and Memurai.

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

**[MEASURED] 2026-08-18 on `docling==2.120.3`, `bert-two-column.pdf` (16 pages), one run
each — and the middle row is worse than described:**

| Configuration | Heading histogram |
|---|---|
| defaults | `{1: 33}` — completely flat, exactly as claimed |
| `heading_hierarchy.enabled=True` alone | `{1: 13, 2: 12, 3: 8}` — a **plausible three-level tree** |
| both, plus `generate_parsed_pages=True` | `{1: 2, 2: 13, 3: 18}` — the same headings, correctly placed |

**Reason:** the middle row is the dangerous one, and on this version it no longer even looks
partial. It is a well-shaped tree with eleven headings at the wrong depth, and **no histogram
check can catch it** — the only defence is setting both switches. That is why
`aegis.ingestion.convert` sets them together in one function and a test asserts the resulting
*histogram*, not the configuration.

The measured histograms for the other three fixtures, all under the shipped configuration:
`transformer-single-column.pdf` `{1: 8, 2: 15, 3: 4}`; `census-income-tables.pdf`
`{1: 7, 2: 2, 3: 3, 4: 21, 5: 33, 6: 52}`; `irs-1040-instructions-tables.pdf`
`{1: 24, 2: 45, 3: 129, 4: 133, 5: 25, 6: 120}`. Six real levels on both long documents, so
D2 holds; the flat-tree failure it was written about does not occur with both switches on.

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

**[MEASURED] 2026-08-18 — the 88% figure does not reproduce on `docling==2.120.3` with
RapidOCR, and the reason to keep the probe is the other direction.**

| Fixture | `do_ocr=False` | `do_ocr=True` | OCR share | Blocks |
|---|---:|---:|---:|---:|
| `bert-two-column.pdf` (16 p) | 7.8 s | 8.3 s | **6%** | 254 → 254 |
| `census-income-tables.pdf` (67 p) | 239.2 s | 259.1 s | **8%** | 567 → 568 |

So OCR on a born-digital document costs **6–8%**, not 88%, and it finds essentially
nothing (one extra block on 67 pages). Whatever produced 33.5 s of 38.1 s was a different
engine or an older release; it is not what we are shipping.

**That does not retire the probe — it re-points it.** The expensive mistake is now the
*other* branch: a scanned document parsed with `do_ocr=False` yields almost no text,
produces a handful of empty chunks, reports success, and answers every question about
itself with "not found". The probe is what prevents that, and its own cost is **0.37 s on
126 pages** (PDFium text extraction, no model). Keep it; stop justifying it on the OCR
saving.

The four fixtures' decisions, measured: three at 100% born-digital, and
`census-income-tables.pdf` at 66/67 — page 66 carries no text layer and is named in the
log as a page we chose not to OCR, which is exactly the trade-off below being made
visible rather than silent.

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

**[MEASURED] 2026-08-18 — the 50–120 s figure is two different costs added together, and
separating them changes what this decision is for.**

| | Measured |
|---|---:|
| Model **download**, into an empty directory (layout + TableFormer + RapidOCR) | **730 MB in 66.8 s** |
| Model **load** from a primed cache — the actual cold start | **3.1–3.7 s** |
| Resident after warm-up, before any document | **989 MB** |

So warming at startup buys ~3 seconds, not a minute. The minute-long stall D4 was written
about is real but it is the **first download**, which happens once per machine and only if
nobody primed the cache — and it fails outright on a box with no network, which is the
scenario that would actually lose the demo.

**What that changes:** the warm-up stays (it is three lines and it moves a visible pause off
the first upload), but the load-bearing mitigation is now **prefetching the models on the demo
box while there is still network**: `python spikes/docling_spike.py --prefetch <dir>`.

**Trade-off:** the worker holds **989 MB** resident from boot rather than on first use —
measured, not the ~2 GB assumed. It is therefore gated on `DOCLING_WARM_ON_START`, off by
default: only the process that serves the CPU queue and actually runs the parse stage should
pay it, and an API-only process or a test worker should not.

### D4a — 🚧 **BLOCKER, found by task 4.0** — Docling's torch and the ML spine's xgboost cannot share a process unbrokered

**Installing Docling brought `torch` into the venv, and that alone broke the platform.**
Not the parser — the *presence* of torch.

| Order | Result |
|---|---|
| `import torch`, then an xgboost fit | **Segmentation fault** in `xgboost.core.set_label` |
| xgboost first, then any `torch` op | **Deadlock** — a 512×512 matmul never returns |
| Either order, `OMP_NUM_THREADS=1` | Works |
| Either order, `OMP_NUM_THREADS` = 2, 4 or 8 | Still segfaults |
| Either order, `KMP_DUPLICATE_LIB_OK=TRUE` | Still segfaults |

`torch` ships its own `libomp.dylib`; `xgboost` and `scikit-learn` ship another. Two
OpenMP runtimes in one process is undefined behaviour, and on macOS/arm64 it is not
subtle.

**Why this is not confined to the ingest worker.** `presidio-analyzer` — the PII engine —
imports `torch` *opportunistically* the moment it is installed
(`presidio_analyzer/nlp_engine/device_detector.py`, and `HuggingFaceNerRecognizer`). So
merely running a PII check in the API process now loads torch, and the next ML prediction
segfaults the process. **Measured, not hypothesised:** both test suites started dying —
the backend suite at `tests/api/test_platform_reads.py::test_model_card_returns_measured_shape`,
after `tests/api/test_agui_demo.py` had triggered the PII path. Nothing in ingestion was
involved.

**Neither failure raises anything catchable.** A segfault takes the process; a deadlock
takes the request and every one behind it. On stage that is the demo ending mid-answer.

**The fix, and it is one line in three places:** `OMP_NUM_THREADS=1`, set before the first
OpenMP library loads —
`backend/src/app/__init__.py` (the composition root, so every entry point gets it),
plus each suite's root `conftest.py`. `aegis.ingestion.convert` also pins it immediately
before it imports Docling, so a process that reaches the parser by a path that skipped the
application package is still safe.

**Cost, measured:** +5% on a Docling parse (7.6 s → 8.0 s on the 16-page fixture), and the
aegis ML suite got *faster* (12.1 s → 5.2 s — thread-pool overhead dominates on models
this small). `setdefault`, so a deployment can override it deliberately.

**What this constrains later in the phase:** task 4.5 may not assume it can raise Docling's
`accelerator_options.num_threads` for speed, and a future decision to run the ingest worker
as a separate process is now a *second* mitigation rather than the only one.

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

**The work, and it is not optional. Revised DDL — the join is repaired in the same change:**

```sql
ALTER TABLE chunks
  ADD COLUMN document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  ADD COLUMN tenant_id   INT    NOT NULL REFERENCES tenants(id);
CREATE INDEX ON chunks (tenant_id, document_id);
```

`doc_id String(255)` does not join to `documents.id BIGINT`, so today there is no way to get
from a chunk to the document that produced it — which breaks the tenant backfill, citation
provenance and re-index alike. Repair it here or every later task works around it.

**`tenant_id` is denormalised onto `chunks` deliberately**, even though it is reachable through
`documents`. An RLS policy that has to *join* to find the tenant is paid on every row of every
query, and — more importantly — it would make the **join** the boundary rather than the row.
That is precisely the mistake Phase 3 found on partitions: a parent's policy does not protect
what is reached another way. The policy predicate must sit on the row it protects.

`ON DELETE CASCADE` because re-ingest otherwise orphans chunks that still answer queries.

**Proven, not assumed** (scratch PostgreSQL, `NOSUPERUSER NOBYPASSRLS` role, both tenants
holding a chunk containing "Clause 7.3.2"): with the policy in place, `WHERE tenant_id = 2`,
an unfiltered `SELECT *`, a `JOIN` through `documents`, and a bare `COUNT(*)` **all** return
only the caller's own row. Not even the count leaks.

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

**[LANDED] 2026-08-18, task 4.6 — with two corrections to the DDL above.**

*`documents.id` is not `BIGINT`.* It is declared `Mapped[int]`, which SQLAlchemy maps to a
plain `integer`, and the live `taif` database confirms it. `document_id` is therefore
`integer` too. A `BIGINT` child would still *work* — PostgreSQL has an `int8 = int4`
operator — but it would leave the referencing column a width the referenced index is not,
which is a trap to inherit rather than a guarantee to keep.

*The `ALTER TABLE` was a `CREATE TABLE`.* `chunks` held **zero rows**, so there was nothing
to back-fill and the "backfilled from the owning document" step above had no work to do.
`app.data.session._recreate_legacy_chunks` drops the pre-tenancy table at bootstrap **only
when it is provably empty** and raises `SchemaDriftError` naming the row count when it is
not — the recreate can never quietly become a `DELETE` of a tenant's corpus.

*`Chunk` moved to `aegis.jobs.models`.* It was on the host's own declarative base, and
SQLAlchemy resolves a `ForeignKey` by name **within one MetaData** — so a `chunks` there
could not reference a `documents` here at all, and the D4b relationship would have been a
naming convention the database never checked. It is re-exported from `app.data.models` under
its historical name.

`tenant_id` is `NOT NULL` where `documents.tenant_id` is nullable. Deliberate: under the
`tenant_isolation` predicate `NULL = <scope>` is NULL, so a null-tenant chunk would be
invisible to every tenant while still being indexed and paid for. A platform-level document
owns no rows in this table.

### D4c — 🚧 **BLOCKER** — one Chroma collection per tenant, not one shared collection

**D4b protects the keyword arm. It does not protect the dense arm, which is the primary one.**

Embeddings are not searched in Postgres. `VectorColumn` is *"the durable source-of-record
embedding … **not** a search index"*, and ANN runs on `ChromaVectorStore`. Today every tenant's
vectors live in **one shared collection**:

```python
# aegis/src/aegis/retrieval/memory.py:67
_LITE_COLLECTION = "aegis_lite_chunks"
```

Isolation is a metadata `where` filter the caller passes. The store handles the subtle part
already and deserves credit for it — Chroma silently *drops* a `None` metadata value, which
would make a null tenant match "any tenant", so `None` is stored as an explicit sentinel. That
is a real leak class, already closed.

**But the shape is still fail-open.** A caller that forgets the filter, or builds it from a
tenant id that arrives `None`, gets **every tenant's chunks** and no error. Compare Postgres,
where forgetting the predicate returns nothing extra because the database applies the policy
itself. One arm is enforced by the engine; the other is enforced by remembering.

**The fix, and it is the standard multi-tenant vector pattern** (Pinecone namespaces, Qdrant
collections, Milvus partitions): **collection per tenant**, `aegis_chunks_t{tenant_id}`,
derived from the bound tenant — never passed in by a caller.

Then a forgotten scope resolves to *no collection* and returns **nothing**, instead of
resolving to *no filter* and returning **everything**. Same failure, opposite direction, and
the safe direction is the one where a bug is visible.

**Trade-off, honestly:** many small collections cost a little more memory than one large one,
and a cross-tenant platform query becomes a fan-out. Neither matters at two tenants, and
neither is a reason to prefer a fail-open boundary.

**Tests required:**

- Two tenants, the same query text, disjoint results — asserted on **content**, not counts.
- A retrieval call whose tenant does not resolve returns nothing and **raises**; it must not
  fall back to an unscoped search.
- The live isolation sweep covers the dense arm as well as the FTS arm, so "chunks are
  isolated" means all of it.

**Sequencing: D4c ships with D4b.** Fixing the lexical arm while the dense arm stays shared
would leave the *primary* retrieval path as the weak one, and the phase would read as solved.

**[LANDED] 2026-08-18, task 4.6b — plus one hole D4c did not name.**

Collections are `aegis_chunks_t<id>` and `aegis_chunks_shared`, derived by
`aegis.retrieval.types.tenant_collection_name` from the bound scope (or, on the write side,
from the row's own recorded owner) and never from a caller argument. A tenant-scoped read is
a two-collection fan-out — its own, then the shared corpus — merged by score; a name built
from anything that is not a token this package minted raises rather than being interpreted.

*The cache is the first door, not the backend.* `Retriever.retrieve` consults both cache
tiers **before** any recall arm runs, and `RetrievalScope.partition_key` built its key with
`int(self.tenant_id)` — which turns the *string* `"7"` into tenant 7. A scope whose tenant
had lost its type would therefore have been served a real tenant's cached answer with no arm
of the backend running and nothing to raise. Fixing the vector arm alone would have left that
path open. `partition_key`, `tenant_value` and `visible_tenant_values` all now route through
`RetrievalScope.resolved_tenant_id`, which raises `UnresolvedTenantScopeError` — `None` stays
*resolved* (the shared corpus), because "I have no tenant" is an answer and "I do not know
whose data this is" is a defect.

---

### D-parse — a parse quality gate, because bad parses do not raise

Docling fails **silently** on multi-column layouts
([#2067](https://github.com/docling-project/docling/issues/2067)): no exception, just scrambled
reading order that chunks, embeds and indexes exactly like good text. The same silence applies
to D2's half-configured heading hierarchy, which yields a plausible-looking `{1: 16, 2: 4}`.

You cannot prevent this. You detect it, at parse time, and record it on the document:

> **REDESIGNED [MEASURED] 2026-08-18, task 4.0.** The original design leaned on the heading
> histogram to catch D2's half-configured case. **It cannot.** On `docling==2.120.3`,
> enabling `heading_hierarchy` alone no longer yields an obviously flat tree — it yields
> `{1: 13, 2: 12, 3: 8}`, a *plausible* three-level tree with eleven headings at the wrong
> depth. No histogram check can tell that apart from a correct one, because it is
> well-shaped and wrong. **The only defence against that case is setting both switches**,
> which `convert.py` now does and asserts.
>
> The histogram still earns its place against the *flat* case (`{1: 33}` on defaults), which
> is a real failure mode if the configuration is ever regressed. It is simply not the
> defence for the half-configured one, and this table no longer claims it is.
>
> Two further parse-side defects were found by reading real chunks, and both are **fixed**
> rather than gated (fixing beats detecting when the cause is knowable): Docling's layout
> model collapsed `3.2 Attention` and `3.2.1 Scaled Dot-Product Attention` to one level, so
> the deeper heading popped its own parent and the path lost a rung — depth now comes from
> the author's own section numbering where a heading carries one. And a table's caption was
> emitted **twice**, standalone and again at the head of the table's Markdown, putting one
> fact in two chunks; the standalone copy is now dropped when an adjacent table already
> carries it, keeping the table self-describing.

| Signal | What it catches |
|---|---|
| **Heading-level histogram** | The **flat** case only — everything at level 1 across a long structured document. Explicitly *not* the half-configured case; see the correction above |
| **Text-layer cross-check** | Independently extract the raw text layer and compare *ordering* against Docling's output. Column interleaving scrambles order while keeping token overlap high, so ordering is the signal, not overlap |
| **Fragment rate** | Chunks ending mid-clause spike when columns interleave |

Store `parse_confidence` on the `documents` row, **surface it in the ingest log (4.12)**, and do
not silently publish a low-confidence parse. Trusting a parser blindly is the thing production
ingestion pipelines do not do.

**Test required:** a deliberately multi-column fixture parses to a *low* confidence — a gate
that never fires is not a gate.

> **[LANDED] 2026-08-19, task 4.6c — and the fixture this gate was written around does not
> fail.** `aegis/src/aegis/ingestion/quality.py` implements the three signals;
> `parse_pdf` scores every parse; `documents.parse_confidence` carries the number.
> Four corrections, each measured on `docling==2.120.3` and reproducible from
> `aegis/tests/ingestion/test_parse_confidence.py`.
>
> **1. `bert-two-column.pdf` parses *correctly*.** The README calls it "the fixture the
> D-parse quality gate must score low". It does not: its per-page reading order agrees
> with the raw text layer at **tau 0.997** over 2443 anchor tokens on all 16 pages,
> against the single-column control's 1.000. Docling 2.120.3's reading order is a
> rule-based geometric predictor over layout boxes
> (`docling_ibm_models.reading_order`) and on this document it gets the columns right.
> **All four fixtures score high** — 0.912 (IRS, 126p), 0.919 (census, 67p), 0.997
> (BERT), 1.000 (transformer). Making the gate fire on BERT would have meant tuning a
> threshold until it flagged a correctly parsed document, which is the same defect as a
> gate that never fires, pointed the other way.
>
> **So the gate is proved against the failure rather than against the fixture.** The
> test takes the *real* parse of the *real* two-column paper and re-orders its blocks
> by position alone — top to bottom, left to right, columns not detected — which is
> exactly what a layout model that missed the column split produces. Every block, box
> and word is genuine; only the order is the failure's. That yields a **stronger**
> control than the README's, because the identical operation is applied to all four:
>
> | fixture | Docling's order | read across the columns |
> |---|---|---|
> | `transformer-single-column.pdf` | 1.000 | 1.000 — **unchanged** |
> | `bert-two-column.pdf` | 0.997 | **0.565 — low** |
> | `census-income-tables.pdf` | 0.919 | **0.724 — low** |
> | `irs-1040-instructions-tables.pdf` | 0.912 | **0.452 — low** |
>
> Top-to-bottom-ignoring-columns *is* the reading order of a single-column page, so the
> operation costs the control nothing and sinks all three multi-column documents. A low
> score is therefore attributable to **layout**, which is what the control existed to
> establish.
>
> **2. The cross-check has to be per page, or it measures almost nothing.** Reading
> order is scrambled *within* a page and never across pages, so a document-wide Kendall
> tau is dominated by the cross-page pairs that cannot invert. The scrambled BERT parse
> scores **0.967 document-wide against 0.565 per page** — the difference between a gate
> and a decoration. The aggregate is the anchor-count-weighted mean of per-page taus.
>
> **3. The score is the *minimum* of the three signals, not their average.** They detect
> disjoint failures, so an average lets a perfect ordering score hide a heading tree that
> is entirely flat. `LOW_CONFIDENCE = 0.75`, placed at the lower end of the measured gap
> (0.724–0.912) rather than its middle: 0.912 is the lowest four correct documents
> happened to produce, not a floor for correct parses in general, while 0.724 comes from
> a scramble whose severity is ours to choose.
>
> **4. A low score flags, it does not block** — the decision this row's "do not silently
> publish" left open. The check measures *disagreement between two readings* and cannot
> say which one is wrong; a PDF whose content stream is emitted out of visual order makes
> PDFium the wrong one, and blocking would then reject a correctly parsed document. It
> would also fail the most expensive stage in the pipeline, so the orchestrator would
> re-parse 126 pages twice more to reach the same verdict, and it would hand the tenant a
> refusal with no action attached to it. Instead: the value is on the `documents` row,
> the reasons are on the parse artifact (`ARTIFACT_VERSION` 2), and `parse_stage` logs a
> **WARNING** naming every signal when the score is low. Not blocked is not silent.
>
> **One calibration the two small papers alone would have got wrong.** The fragment
> floor sits at 0.25 only because `census-income-tables.pdf` — a *correct* parse —
> measures **0.221**. Real prose ends without a full stop far more often than a first
> guess suggests (0.029, 0.094, 0.119, 0.221 across the four fixtures), and a floor drawn
> from the two 15-page papers would have penalised correct parses of the other two. This
> is the reason the slow fixtures are worth their ten minutes even though they stay
> gated behind `AEGIS_DOCLING_SLOW_FIXTURES=1`.

---

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

**[LANDED] 2026-08-18, task 4.7 — with two corrections measured on the cluster.**

*`plainto_tsquery` AND-s its terms, and that would have lost the query the arm exists for.*
"what does clause 7.3.2 say?" parses to `'claus' & '7.3.2' & 'say'`, which matches only a
passage containing all three — so the passage carrying the identifier is exactly what the
default query builder drops. BM25 is disjunctive, and the arm replacing it has to be too:
the connective is rewritten to `|` on `plainto_tsquery`'s **output**, which is already
normalised and stripped of operators, so no user text is interpolated into SQL.

*`ts_rank_cd` ranks this query class backwards.* Measured on both passages: for
"what does clause 7.3.2 say about refunds?", a decoy repeating the common word "clause"
four times scores **0.161** against the answer's **0.137** — cover-density rank is
proportional to the number of covers, so repetition beats coverage. `ts_rank` saturates
repeated occurrences (BM25's `k1`) and scores the answer **0.0144** against the decoy's
**0.0060**. The arm uses `ts_rank` with normalisation flag `1` (divide by
`1 + log(length)`, BM25's `b`). What is still missing versus Okapi BM25 is **IDF** — the
docstring says so rather than implying otherwise.

One thing D5 does not say and should: the arm has **two** boundaries, not one. The `WHERE
tenant_id` predicate is D5's, and because the host injects the *serving* session factory
(`app.retrieval.pipeline._chunk_session`) the arm also binds the tenant GUC and runs under
the `tenant_isolation` policy. Both are proved separately in
`aegis/tests/retrieval/test_keyword_recall.py`, because a single test over an unprivileged
role would keep passing with the predicate deleted.

The write side of `chunks` is still task 4.5's: nothing populates the table yet, so this
change ships the read arm complete and the corpus it reads arrives with the stage handlers.

### D6 — Add a local ONNX cross-encoder reranker (~250M, API fallback)

> **[MEASURED] 2026-08-19, task 4.9, 16 GB M3, `fastembed==0.8.0`,
> `jinaai/jina-reranker-v1-tiny-en`. Reproduce: `PYTHONPATH=aegis/src
> backend/.venv/bin/python spikes/rerank_bench.py`.**
>
> **The premise reversal was right, and the latency estimate below is wrong by 4x.** Both
> halves matter, so both are recorded.
>
> **Right:** `fastembed==0.8.0` resolves to huggingface-hub, loguru, mmh3, numpy,
> **onnxruntime** (CPU), pillow, py-rust-stemmers, requests, tokenizers, tqdm. **No torch.**
> (`fastembed-gpu` is the variant that pulls onnxruntime-gpu; we do not use it, and the
> reranker asks for `CPUExecutionProvider` by name so an onnxruntime-gpu arriving via some
> other package cannot silently take over.) The model is 33M parameters and **134 MB on
> disk, measured** — the "~250M / ~250 MB" in the text below is the size we budgeted for,
> not the size we needed.
>
> **Wrong:** *"~250M over 20 passages is roughly 150–400 ms on CPU, which a person does not
> notice."*
>
> | | measured |
> |---|---|
> | Rerank 20 passages x 400 words (`recall_top_k` x `chunk_size`) | **p50 1.44 s · p95 1.55 s** |
> | Per passage | **~72 ms** per 400-word passage — the constant that travels between boxes |
> | Warm load (weights cached) | 0.43 s |
> | Cold load (download + init, good wifi) | 7.84 s, 134 MB on disk |
> | Peak RSS | 427 MB after load, 610 MB steady while serving |
>
> The estimate was not wrong about the model, it was wrong about the **passage**. A
> cross-encoder's cost is linear in total sequence length and in pool size, and the 150–400 ms
> figure comes from ~60-word retrieval passages. Our chunks are 400 words. Measured:
> 64 words → 221 ms, 128 → 418 ms, 256 → 939 ms, 400 → 1585 ms, 460 → 1848 ms; and pool size
> is linear too (5 → 383 ms, 10 → 752 ms, 20 → 1567 ms, 30 → 2420 ms). **`recall_top_k` is
> therefore the honest latency lever**, and it is a straight quality trade, not a free one.
>
> **Verdict: ship it, and say the number out loud.** 1.44 s is not "a person does not notice".
> But it is not 1.44 s *added*: it replaces an LLM call that graded the same twenty passages
> (~12k prompt tokens through the gateway), which is neither faster, nor free, nor
> reproducible across two eval runs. What we bought — **on our own gold set, not on
> T2-RAGBench** — is **MRR@20 +12.9 pp** and recall@6 +0.009: a better-ordered answer, a
> per-query cost of zero, and a deterministic order. The "+12.1 pp recall@5" belongs to the
> external benchmark and is quoted as external wherever it appears (task 4.11, point 3). What it costs on the **Windows demo box is unmeasured** —
> re-run `spikes/rerank_bench.py` there, as 4.0 did for Docling.
>
> **Three findings the plan did not anticipate:**
>
> 1. **Batch size is a 400 MB memory decision and not a speed one.** At `fastembed`'s default
>    batch of 64 the process settles at **867 MB** RSS; at batch 4 it is **470 MB**, because
>    onnxruntime's CPU arena sizes itself to the largest batch it ever saw and never returns
>    it. Latency is unchanged (1.39 s vs 1.47 s — batch 4 is marginally *ahead*) and scores are
>    identical to 1e-7. The reranker therefore defaults to `batch_size=4`.
> 2. **Do not pin onnxruntime to one thread.** D4a's `OMP_NUM_THREADS=1` has no effect here
>    (onnxruntime uses its own pool, not OpenMP): 1593 ms pinned vs 1610 ms unpinned. But
>    passing `threads=1` to the session **is** 2.6x slower (3712 ms vs 1524 ms). The two knobs
>    look alike and only one of them is free.
> 3. **The weights default into `$TMPDIR`.** `fastembed`'s cache is
>    `<system temp>/fastembed_cache` unless `FASTEMBED_CACHE_PATH` says otherwise — cleared on
>    reboot on most Linux boxes, which on a venue with no network means the first query after
>    a restart falls to the API reranker. The loader now logs a WARNING when it lands there.
>
> **The model comparison the cut list said to skip was cheap enough to do**, and it changed
> the answer. All four at pool 20 x 400 words: `jina-reranker-v1-tiny-en` **1.57 s**,
> `ms-marco-MiniLM-L-6-v2` 1.88 s, `jina-reranker-v1-turbo-en` 2.38 s,
> `ms-marco-MiniLM-L-12-v2` 3.69 s. The 33M default is also the fastest — and MiniLM-L-6 has a
> 512-token input cap, which would silently truncate the tail of a 400-**word** chunk.
> `bge-reranker-base` and `jina-reranker-v2-base-multilingual` were not benchmarked: ~1.05 GB
> each, and the v2 model is CC-BY-NC.

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

> **CORRECTION [MEASURED] 2026-08-18, task 4.4.** This decision says the four fields "are
> already on the `documents` row". **Two thirds of that is false.** `documents` carries
> `filename`, `mime_type`, `content_sha256`, `size_bytes` and `created_at` — **no title, no
> document type, no document date.** `mime_type` is `application/pdf` for the entire corpus
> so it discriminates nothing, and `created_at` is when somebody uploaded the file, not when
> the document is from — using it would stamp every chunk of a 2019 contract with 2026.
>
> **Title** is therefore derived from the parse (the document's first heading, which on all
> four fixtures is the real printed title), falling back to the filename stem.
> **Type and date must be supplied by the tenant at upload — that is now task 4.5's scope**,
> and it is the only task that can honestly know them. Until it does they degrade to
> `untyped`/`undated` placeholders rather than to a confident wrong constant.
>
> The "zero model-call cost" claim survives intact: these are still metadata, not inference.

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

**[IMPLEMENTED] 2026-08-19 — task 4.10, with one correction and three measurements.**

**The correction: the summary does not replace the embedded text; it is prepended to it.**
As written above, "the table's *embedded* text **is** a generated NL summary" and the grid is
"stored alongside". Following that literally is a quiet data loss. The numbers are what most
questions about a table are actually asking for — *"what BLEU did the Transformer reach on
EN-FR?"* is answered by `41.8` and by no sentence anyone would write about that table — and a
chunk whose text is only prose about the grid cannot be quoted, cannot be span-verified
(4.14), and cannot be reranked on the cell the asker wants. `chunks.content` for a table is
therefore `summary` + blank line + grid: the summary is what makes the table *findable*, the
grid is what makes it *answerable*, and PostgreSQL generates `search_vector` from both. In
front rather than behind because the head of a chunk is what a truncating embedder and a
cross-encoder reranker both see most of.

**A table is now its own chunk.** Left in the general packing run a 143-word table merges
with the paragraph above it, so the chunk is only partly a table, its citation names two
pages, and the grid's tail becomes the overlap seed of the next prose chunk — which then
opens with three rows of orphaned numbers. `chunker._packing_groups` isolates it. Cost: a
handful of extra chunks on a table-dense document.

**The threshold, measured rather than guessed.** A table is summarised at ≥3 rows, ≥3 columns
**and** ≥12 cells (`app.config.table_summary_*`). Two columns is a key-and-value list and two
rows is a label and a value; both already read as prose. Across all four fixtures:

| fixture | tables | above threshold | distinct |
|---|---:|---:|---:|
| `transformer-single-column.pdf` | 4 | 4 | 4 |
| `bert-two-column.pdf` | 8 | 8 | 8 |
| `census-income-tables.pdf` | 40 | 38 | 38 |
| `irs-1040-instructions-tables.pdf` | 39 | 37 | 37 |

So the threshold refuses 4 of 91 real tables — every one of them a 2-column block — and the
smallest table it admits from the two papers is 8×3. It buys its saving from layout artefacts,
not from the tables D8 exists for.

**Where the money actually is, and it is not the call count.** It is the *input tokens*. The
IRS fixture's 39 tables are **3.2 MB of Markdown**; the 126-page document would send ~800 k
tokens of grid in one ingest. The 6,000-character-per-table prompt cap
(`table_summary_max_grid_chars`) takes that to 134 KB, ~33 k tokens, and says in the prompt how
many rows it dropped so the model cannot describe a third of a table as the whole of it. The
census fixture goes 442 KB → 184 KB the same way. **The cap is a bigger lever than the
threshold by two orders of magnitude**, which the original decision did not anticipate.

**The cache is per tenant**, not global — `table_summaries(tenant_id, digest)`, registered in
`rls._TENANT_SCOPED_TABLES`. A global cache would hit more often and the summary of an
identical grid leaks nothing, but "safe to share because the inputs were equal" is a rule that
survives exactly until what is cached is widened, and it would be the first row in the
retrieval path whose predicate is not `tenant_id`. The digest normalises Docling's column
padding, so the same table rendered beside a wider neighbour still hits.

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

**[LANDED] 2026-08-19, task 4.14 — with two things this decision did not settle.**

*It lives in `aegis/src/aegis/retrieval/citations.py`, and the gold set imports it from
there.* `normalise_span` / `span_present` are the grading rule of 4.11 and the verification
rule of a citation, one function, so a normalisation that is too generous inflates the eval
and waves through a fabricated quote in the same motion. What it folds is transport and
nothing else: NFKC (a PDF font's `ﬁ` ligature), line-wrap hyphens, case, and every
non-alphanumeric run — including the spotlight datamark, so turning the injection defence on
does not silently fail every citation. It also deletes `U+FFFE`, which is what PDFium emits
where it cannot map the wrap-hyphen glyph — **332 times in `bert-two-column.pdf` alone**, and
without that rule 8 of 53 gold spans failed to anchor to the document they are printed in.

*A failed check is **marked, never dropped**, and this decision does not say which.* Dropping
it is the tempting option because the output looks clean; it is wrong, because an answer
quoting a sentence that is in no retrieved chunk is the loudest hallucination signal the
system can produce, and deleting it destroys the evidence while leaving the prose it justified
in place and unlabelled. So `verify_citations` returns one check per citation with an explicit
status — `verified`, `unverified`, or `unknown-source` (a citation naming a chunk the answer
was never given, which is an id-mapping bug and not a hallucination) — plus a `matched_fraction`,
so a paraphrase at 0.8 and an invention at 0.09 are not the same red cross.

*What it is not yet wired to, because there is nothing to wire it to.* The answer path emits
**prose**, not structured citations: `RetrievalResult.sources` carries chunks, and
`ScoredSource` in the API schema carries `id`/`label`/`score` with no quoted span anywhere.
So 4.14 ships as a verified, exported primitive with no caller in the serving path, and the
generator that emits `(source_id, quote)` pairs is what turns it on. Stated here rather than
left for someone to discover that "every citation is checked" checks a list that is always
empty.

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
| 4.0 | ✅ Spike — **macOS leg done 2026-08-18**, Windows leg outstanding | 0.25 | Docling install, model prefetch, cold start, all four fixtures. See §4.0 |
| 4.1 | ✅ `convert.py` seam — **with page/bbox from line one** | 0.5 | Done 2026-08-18. `aegis/src/aegis/ingestion/`; Docling never leaks past `convert.py` |
| 4.2 | ✅ Text-layer probe + header/footer/page-number stripping | 0.25 | Done 2026-08-18. The stripper is a **backstop** on these fixtures — see §4.0 |
| 4.3 | ✅ `chunk_sections()` — feed pre-structured sections to the existing packer | 0.25 | **Landed 2026-08-18.** The chunker survived intact: `_pack_units` gained the unit indices a chunk needs to find its blocks again, and nothing else moved. Page and bbox thread through, including for the words carried in an overlap tail |
| 4.4 | ✅ Enriched prefix: title · type · date · heading path | 0.15 | **Landed 2026-08-18** with 4.3. Highest quality-per-hour in the phase. A field the document does not carry renders as a placeholder (`untitled` · `untyped` · `undated` · `unsectioned`) rather than collapsing — the prefix is embedded, so its *shape* must not vary across a corpus |
| 4.5 | ✅ Upload route + **stage handlers on the P3 workflow**, **and `documents.title`/`doc_type`/`doc_date`** | 0.55 | `documents` already exists (P3). No queue machinery — the stages are activities. **Owns the three prefix fields D7 wrongly assumed existed** — see the correction under D7. **Landed 2026-08-18**; four corrections, see §4.5 |
| 4.6 | ✅ **`chunks.tenant_id` + `document_id` FK** + RLS + isolation test | 0.35 | **Blocker for 4.7.** D4b — repairs the broken join in the same change. **Landed 2026-08-18** |
| 4.6b | ✅ **Collection per tenant in the vector store** | 0.25 | **Second blocker.** D4c — the dense arm was fail-open; shipped with 4.6. **Landed 2026-08-18** |
| 4.6c | ✅ **Parse quality gate** — heading histogram, order cross-check, fragment rate | 0.3 | D-parse. Docling fails silently on multi-column; the gate is how anyone finds out. **Landed 2026-08-19** — and `bert-two-column.pdf` turns out to parse *correctly* on 2.120.3, so the gate is proved against the failure rather than against the fixture. Four corrections, see D-parse |
| 4.7 | ✅ **Corpus-wide `keyword_recall`** on Postgres FTS | 0.5 | The largest quality gap. D5 — **Landed 2026-08-18**; two corrections, see D5 |
| 4.8 | ✅ `corpus_version` bump + cache invalidation | 0.25 | **Landed 2026-08-19.** Plugs into Phase 1's seam. The bump lives in `finish_ingest` — the close-out activity — and not in the upload route or in a stage: see the correction under 4.8 below, which also records that one claim in 4.5's body about the idempotency key is wrong |
| 4.9 | ✅ **Local ONNX cross-encoder reranker** + model benchmark | 0.5 | Second largest. **Landed 2026-08-19.** `fastembed==0.8.0` in the `retrieval` extra (no torch); `jinaai/jina-reranker-v1-tiny-en` (33M, 134 MB). The API reranker is now the **loud** fallback. **D6's latency estimate was wrong by 4x** — measured 1.44 s p50 over 20 x 400-word chunks, not 150–400 ms; see the correction under D6 |
| 4.10 | ✅ **Table objects with NL summaries, hash-cached** | 0.4 | **Landed 2026-08-19.** A table is now its own chunk carrying `(rows, cols)`, its caption and a content digest; above a configured size the `chunk` stage writes a generated sentence or two **in front of** the grid and caches it in `table_summaries`, keyed on the digest. **TableFormer stays on ACCURATE** — see D3b, unchanged. Duplicated table captions were already fixed in `convert.py`, so the summaries are written over text that does not repeat itself. **One correction to D8, and it is not cosmetic — see below.** |
| 4.11 | ✅ Span-anchored gold set + naive-baseline ablation | 0.5 | **Landed 2026-08-19.** 58 cases (53 gradeable) over all four fixtures, every span verified verbatim in the PDF's own text layer. **The ladder does not go up the way §5 assumed and three of its arms do not pay for themselves — see the measured block under §5, which is the most important correction in the phase.** |
| 4.12 | ✅ Live ingest log — a projection over the job row | 0.4 | **Landed 2026-08-19.** `app/ingestion/progress.py` (read) + `app/jobs/ingest_log.py` (write) + `GET /documents/{id}/ingest`. **Two corrections, and the first one is a whole half of the task — see below.** |
| 4.12b | ✅ **Graph construction visible in the ingest log** | 0.25 | **Landed 2026-08-19** with 4.12. Entities and relations aggregated out of `chunks.meta` in SQL, with mention counts and both ends of every edge resolved to their human labels |
| 4.13 | ✅ Re-index handler on the P3 scheduler | 0.1 | **Landed 2026-08-19.** `app/ingestion/reindex.py` — every stage except `parse`, over the stored parse artifact. Two corrections, see §4.13 |
| 4.14 | ✅ Verbatim citation verification | 0.15 | **Landed 2026-08-19.** `aegis/src/aegis/retrieval/citations.py` — the primitive 4.11 grades with, plus `verify_citations`. A failed check is **marked, never dropped**; see the correction under D10 |

**Total: ~5.0 days** after the P3 credit (−0.35 on 4.5, −0.15 on 4.13) and the three
additions above (+0.9). Honest rather than padded — see the cut order.

Three of these have detail that does not fit a table row.

### 4.0 — the spike, and the five things it corrected (macOS leg DONE 2026-08-18)

**Installed:** `docling[rapidocr]==2.120.3` — pinned exactly, because a parser release moves
reading order, heading levels and table structure, and chunks embedded under one version are
not interchangeable with another. With it: `docling-core` 2.91.0, `docling-parse` 7.14.0,
`docling-ibm-models` 3.14.0, `rapidocr` 3.9.2, `pypdfium2` 5.13.0, `torch` 2.13.0,
`transformers` 5.8.1. **52 packages added, +816 MB in the venv** (1,469 → 2,285 MB), and two
packages *downgraded* by the resolver: `tokenizers` 0.23.1 → 0.22.2 and `typer` 0.27.1 →
0.26.8. Both are shared with chromadb / litellm / spacy / nemoguardrails; both suites pass
after the downgrade, which is the only reason it is acceptable.

**Model weights:** 730 MB, downloaded in 66.8 s into an empty directory
(`spikes/docling_spike.py --prefetch <dir>`). Do this on the demo box while there is still
network.

**Five corrections, each measured rather than argued:**

1. **s/page is corpus-dependent, and the planning number is 3× low.** 0.43–0.47 s/page on the
   two papers; **2.87–3.20 s/page** on the two table-dense documents. See D1.
2. **Peak RSS scales with the document**, not just with the models: **3.2–3.4 GB** on the 67-
   and 126-page fixtures, against the 2.2 GB assumed. See D1.
3. **Cold start is 3.1–3.7 s, not 50–120 s** — with the model cache primed. The 50–120 s is
   the first download. See D4.
4. **OCR is not 88% of runtime on this stack.** See D3.
5. **Docling 2.120.3 already discards running headers and footers** on all four fixtures.
   The raw text layer of `census-income-tables.pdf` carries
   `"34 Poverty in the United States: 2022 U.S. Census Bureau"` on every page and
   `irs-1040-instructions-tables.pdf` carries a bare page number plus
   `"Need more information or forms? Visit IRS.gov."`; **neither reaches our blocks**, and no
   item on any fixture came back labelled `page_header` or `page_footer`. So task 4.2's
   stripper removed **nothing** on all four documents. It ships anyway, as a backstop for the
   documents where furniture does survive — it removes parser-labelled furniture outright and
   position-plus-repetition runs otherwise — and it is proved by unit tests over synthetic
   blocks rather than by a fixture, which is stated here so nobody reads the fixture run as
   evidence that it works.

6. **Installing the parser broke the platform, and not through the parser.** Docling
   brings torch; `presidio-analyzer` imports torch on sight; torch and xgboost cannot
   share a process. Both suites segfaulted. See the new **D4a**, which is a blocker.

**Where the code landed.** `aegis/src/aegis/ingestion/` — `blocks.py` (our vocabulary),
`probe.py` (the text-layer probe and the OCR decision), `furniture.py` (the stripper),
`convert.py` (the only module allowed to import Docling). Host wiring is
`backend/src/app/ingestion/` (the D4 warm-up, gated on `DOCLING_WARM_ON_START`) called from
`app.jobs.worker.run_workers` when this process serves the CPU queue.

**Still outstanding: the Windows leg.** Every number above is macOS on an M3. 4.0 is not
closed until the same script has been run on the demo box — total RSS with Postgres, Neo4j,
Memurai and Temporal also resident is the number that decides whether one parse at a time is
enough.

### 4.5 — `documents`, the upload route, and the `ingest_document` job (0.75d)

**[LANDED] 2026-08-18 — with four corrections to what follows.**

*The `documents` table was not this task's to create.* Amendment B already said so; what was
actually missing is the three prefix fields, and they are now on the row: `title` (nullable,
written by the **parse** stage from the document's first heading, falling back to the
filename stem — never to `source_name`, which is the content-addressed path in the document
store and so is a SHA-256), `doc_type` and `doc_date` (nullable, supplied by the uploader,
left `NULL` and rendered as `untyped`/`undated` when they are not). `title` was added to
`_HANDLER_WRITABLE_COLUMNS`; `doc_type` and `doc_date` deliberately were **not** — a stage
that could write them could only ever be writing a guess.

*The bytes need somewhere to live, and the phase never said where.* They are not on the row:
a 126-page PDF is megabytes of `bytea` on the hottest tenant-scoped table in the system.
`app.ingestion.store` is a content-addressed, tenant-partitioned directory under
`DOCUMENT_STORE_PATH`, addressed by the same `content_sha256` the row carries. **The parse
artifact lives there too** — `parse` (CPU queue) and `chunk` (default queue) are different
activities in different transactions, so the structured tree has to survive the gap or the
chunk stage would re-parse two hundred pages to learn what the parse already knew. That is a
**shared-filesystem assumption**, stated rather than hidden: it holds trivially on the
single-box posture, and a deployment that splits the queues across machines must point that
setting at shared storage.

*`chunks.embedding` is `NOT NULL`, so the chunk stage cannot leave it unset.* It writes the
**empty** list, which is off-dimension by construction and therefore skipped rather than
believed; a zero vector of the right width would be a valid-looking vector pointing nowhere.
The column was left as task 4.6 shipped it rather than relaxed, because the additive
reconciler cannot relax a live `NOT NULL` and the model would then be describing a schema no
deployed database has.

*Handlers are registered by the process entry point, not by the worker bootstrap.*
`run_workers` reports which stages have no handler and installs none: the registry is a seam
(the kill-and-resume test fills it with journalling handlers and runs the shipped bootstrap
underneath), and a bootstrap that force-registered would silently replace them. `app.main`'s
lifespan registers for the in-process mode; the `__main__` guard in `app.jobs.worker` does it
for `python -m app.jobs.worker`.

**What the six handlers do** (`app.ingestion.stages`): `parse` → Docling, artifact, returns
`page_count` + `title`; `chunk` → `chunk_sections`, **delete-then-insert** of `chunks` rows
carrying tenant, document, prefix, section, page spans and content id, returns `chunk_count`;
`enrich` → one guarded `UPDATE` folding the D7 prefix into `content` (which is also what
`search_vector` is generated from); `embed` → the embedding of record per chunk; `index` →
publishes the chunks to the configured knowledge backend under a tenant-prefixed,
content-addressed id; `graph` → entity/relation extraction recorded on the chunk row, named
with the extractor that produced it.

**Measured on the way in.** `transformer-single-column.pdf` uploaded through the route:
15 pages, 33 chunks, `title` = *"Attention Is All You Need"* read off the first heading, and
`keyword_recall("multi-head attention")` returns the chunk whose prefix reads
`[Attention Is All You Need · research paper · 2017-06-12 · 3 Model Architecture > 3.2
Attention > Scaled Dot-Product Attention]`. Task 4.7's keyword arm has a corpus.


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
- ~~**The idempotency key is `ingest:{sha256}:{corpus_version}`.**~~ **Wrong, and corrected
  by 4.8 rather than implemented by it.** What shipped in 4.5 is the `UNIQUE (tenant_id,
  content_sha256)` constraint on `documents`, and a re-upload of identical bytes is a no-op
  *whatever* the corpus version. Folding the version into the key would have made every
  re-upload after any ingest a fresh, billed parse of bytes the platform already holds —
  which is precisely the double-billing that constraint exists to prevent. The corpus version
  is a **cache** key, not a document identity; a tenant who genuinely wants the same bytes
  re-processed asks for a re-index (4.13), which rebuilds without re-parsing at all.
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

### 4.8 — Where the `corpus_version` bump goes (0.25d) — LANDED 2026-08-19

Phase 1 built the counter and both cache keys already fold it in
(`aegis/src/aegis/retrieval/corpus.py`, `RetrievalScope.partition_key`, the answer cache's
scope string). The only open question was **where the increment goes**, and it is the whole
of the task, because every candidate position is defensible and only one is right.

**It goes in `finish_ingest`** — the close-out activity in `app/jobs/activities.py` — inside
the same guarded `UPDATE` that moves the document to its terminal status.

Not the upload route: that invalidates the tenant's cache before a byte has been parsed, and
every request during the ingest then misses, recomputes, and re-caches an answer over the
*old* corpus, which is worse than the stale answer it replaced. Not a stage handler either.
A bump at `chunk` invalidates while the chunks have no prefix and no vectors; a bump at
`index` invalidates while the graph arm is still empty. Each of those states answers
questions plausibly and wrongly, and the window is minutes rather than milliseconds.

Three properties that fall out of that position, each of which is a line of code and a test:

1. **`RETURNING` on the guarded update, not a second read.** The bump happens only if that
   `UPDATE` actually changed a row, so a replayed close-out — which the substrate does
   produce, measured in Phase 3 §3.0 — bumps nothing. Double-bumping is not a correctness
   bug, but it discards a tenant's whole cache for free.
2. **A `failed` or `cancelled` run bumps too, if `chunk_count` is set.** Chunks written
   before the failure are live to the keyword arm the instant that stage commits. Bumping
   only on success is the version of this feature that is silently wrong exactly when
   something else already went wrong.
3. **The bump precedes the commit.** The transaction may still roll back, in which case the
   tenant paid one cache miss. The other ordering risks a committed ingest with no bump,
   which costs a wrong answer. The asymmetry decides it.

**What turned out wrong.** 4.5's body claims the ingest idempotency key is
`ingest:{sha256}:{corpus_version}` and that bumping the version is what makes a re-ingest a
new job. It is not, and it must not be — see the strikethrough there. The counter is a cache
key; the document's identity is its bytes.

**One property to state rather than let somebody discover.** The counter is process-local.
That is correct for the posture this platform ships in — `app.main`'s lifespan runs the
Temporal worker as an `asyncio` task in the API process, so the bump and the cache lookup
share a counter — and it stops being correct the moment anybody runs
`python -m app.jobs.worker` as a separate process, where the bump would move a counter the
API cannot see and the stale answer would come back with no error anywhere. Splitting the
worker out therefore requires replacing the store in `aegis/src/aegis/retrieval/corpus.py`
with a shared one; the keying it feeds is already right either way. The module docstring
carries this warning so it is read at the point of change.

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

#### What actually landed, 2026-08-19 — and one of these is half the task

1. **"Plus the per-stage `run_events` rows from Phase 3 §3.6" described rows that did not
   exist.** Phase 3 built the record — the partitioned table, the fold, the regenerable
   header — and shipped it with **no producer at all**: `grep -rn record_events backend/src`
   returned nothing. So this task supplied the *write* side as well as the read side, in
   `app/jobs/ingest_log.py`, and the projection is the second thing rather than the only
   thing. The entry is appended **inside the stage's own transaction**, so the log line and
   the `completed_stage` bump it describes commit together; a stage's `seq` is its index in
   `INGEST_STAGES` rather than a counter, so a replay writes the number it wrote before
   instead of appending a second, later-looking entry. This is why the phrase "do not build
   a second log" survived intact: `run_events` now has a real consumer instead of a
   documented one.

2. **A stage handler had nowhere to put anything that is not a `documents` column** — so
   4.6c's hand-off could not be honoured as written. The substrate applies a handler's
   return value through an allow-list (`_HANDLER_WRITABLE_COLUMNS`), which correctly refuses
   any key that is not a column, and the parse *reasons*, the OCR decision and the heading
   histogram are none of them columns and should not be. So there is a second, narrow
   channel — `report_stage_facts` / `collect_stage_facts`, a `ContextVar` holding a mutable
   dict — with the split stated in its docstring: what a handler **returns** is state and
   goes on the row; what it **reports** is evidence about the attempt and goes in the
   append-only log. Reporting outside a collector is a no-op, so a handler called from a
   test or from 4.13's re-index loop needs no double. A context variable rather than a
   module global because `IO_QUEUE` runs 32 activities in one process, and a global would
   interleave one document's evidence into another's.

   It lives in a **new `aegis/jobs/facts.py`, not in `aegis/jobs/stages.py`**, and the
   first attempt at the latter is what found the reason: `stages.py` is re-imported inside
   the orchestrator's workflow sandbox on every workflow task, so a `ContextVar` declared
   there would be minted fresh each time and a handler and its collector could end up
   holding two different ones with nothing reporting a problem. `aegis/tests/jobs/
   test_stages.py::test_this_module_imports_nothing_a_workflow_sandbox_would_re_execute`
   asserts that module's import list against its own AST and caught the mistake — a test
   worth naming, because the failure it prevents would have been silent.

3. **The endpoint is `GET /documents/{id}/ingest`, not `/events`.** Re-streaming the raw
   events alone would leave the browser to derive "which stages completed" from them — and
   that derivation must happen once, server-side, **off `documents.completed_stage`**, or a
   log entry that failed to write would erase a committed stage from the screen. The route
   returns the whole projection: stage states, per-stage durations and facts, the parse
   verdict, the corpus counts, the tables, the graph, and the chronological tail.

4. **The cut order's item 1 turned out not to be a cut.** It offers to drop the
   OCR-decision and heading-histogram panels; both are one field on an event that was being
   written anyway and one small block in a component, so cutting them would have saved
   nothing measurable. Both shipped.

5. **A re-queue makes a new workflow id** (it must — `job_runs.workflow_id` is unique), so
   reading the log off `documents.workflow_id` would show only the latest attempt and lose
   the stages an earlier one committed. The projection collects every `job_runs` row for the
   document instead, so a re-queue produces a *longer* log rather than a rewritten one.

6. **`include_router` is lazy in FastAPI 0.141** and appends one `_IncludedRouter`
   placeholder with no `path` and no `methods`. Mounting the new route module that way would
   have made the endpoint invisible to `tests/api/test_route_coverage.py`, which enumerates
   `app.api.routes.router` — a route escaping the coverage test by accident is exactly the
   drift that test exists to catch. `app.api.ingest_log.mount` extends `routes` instead.

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

#### What actually landed, 2026-08-19 — two corrections

Everything above is Phase 3's, and Phase 3 shipped it: the cadence is a **Temporal Schedule**
(`app/jobs/schedules.py`), not a `job_schedules` table with a materialiser, and the burst is
folded by `ReindexWorkflow`'s per-tenant workflow id and timer reset. So this task supplied
the **handler**, in `backend/src/app/ingestion/reindex.py`, registered at the same two
composition roots that register the ingest stage handlers.

1. **"Re-embed and re-index documents whose `corpus_version` is behind the current one" was
   the wrong selector.** `corpus_version` is a per-tenant cache counter; it is not recorded
   on a document and could not be, since the thing that goes stale is the *index*, not the
   row. The handler re-runs over every document the tenant has in `succeeded` — the same
   visibility test the cadence tick already uses before it asks for a run at all, so the
   schedule never wakes a worker for documents the handler would then decline to touch.
2. **The nightly graph rebuild is not a second schedule, and cutting it would be a bug.**
   The `chunk` stage is delete-then-insert, so re-chunking destroys the entities and
   relations the `graph` stage wrote onto `chunks.meta`. A re-index that stopped after
   `index` would silently empty one of the three retrieval arms for every document it
   touched. `graph` is therefore part of the corpus re-index rather than a separate job, and
   the cut-order entry that offers to drop it (§6, item 5) should be read as dropping the
   *separate schedule* — which no longer exists — and never as dropping the stage.

The re-index runs **every stage except `parse`**, in the pipeline's declared order, through
the *registered* stage handlers rather than a second copy of them. `parse` is excluded
because its output is already durable beside the bytes: re-deriving it costs 0.43–3.20 s a
page, six minutes on `irs-1040-instructions-tables.pdf`, to reproduce a tree on disk. It
bumps `corpus_version` once per run, for the same reason an ingest does. The whole run is one
transaction, so a document with a missing parse artifact fails the run and rolls it back
rather than leaving half the corpus embedded under a new model and half under the old — the
one thing worse than either outcome alone.

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
`research/eval-design.md` §4, including a five-rung fallback ladder
for when the gateway is down. The single most valuable preparation item: **a timed rehearsal
on an unfamiliar corpus before 30 August.**

---

### [MEASURED] 2026-08-19, task 4.11 — the table, and the four things above it that are wrong

**Reproduce:** `AEGIS_DOCLING_SLOW_FIXTURES=1 PYTHONPATH=aegis/src backend/.venv/bin/python
scripts/eval_goldset.py`. Artifact: [`runs/eval-goldset-20260819.json`](../../runs/eval-goldset-20260819.json)
(git sha, model ids, corpus hashes, gold-set hash, seed, per-case ranks). Cost **$0.00** — the
embedder and the reranker are both local ONNX, so two runs are comparable.

**The set:** 58 span-anchored cases, **53 gradeable**, over all four fixtures — 30 hand-written,
11 known-item, 9 table, 3 multi-hop, 5 unanswerable. Every span is asserted verbatim in the
**PDF's own text layer** (PDFium), not in the Docling parse, because checking a parse against
itself is not a check. Corpus: 519 naive windows / 774 structural chunks.

| arm | what changed | recall@20 | recall@6 | precision@6 | MRR@20 | nDCG@10 |
|---|---|---|---|---:|---:|---:|
| **A0** | naive: text layer + fixed windows, dense only | 0.934 (0.85–0.98) | 0.755 (0.62–0.85) | 0.138 | 0.568 | 0.619 |
| **A1** | + layout-aware parse and structural chunking | 0.906 (0.80–0.96) | 0.774 (0.64–0.87) | 0.135 | 0.533 | 0.595 |
| **A2** | + enriched chunk prefix (D7) | 0.896 (0.80–0.96) | 0.736 (0.60–0.84) | 0.129 | 0.563 | 0.613 |
| **A3** | + hybrid recall (vector + graph + BM25, RRF) | 0.915 (0.80–0.96) | 0.821 (0.71–0.91) | 0.145 | 0.557 | 0.622 |
| **A4** | = shipped: A3 + local cross-encoder rerank | 0.915 (0.80–0.96) | 0.830 (0.71–0.91) | 0.151 | **0.686** | 0.732 |
| **L1** | A4 − graph arm | **0.972 (0.90–1.00)** | **0.849 (0.73–0.92)** | 0.154 | 0.692 | 0.740 |
| **L2** | A4 − BM25 arm | 0.821 (0.71–0.91) | 0.764 (0.62–0.85) | 0.135 | 0.643 | 0.681 |

n = 53; Wilson 95%.

> **[CORRECTED] 2026-08-19 — the nDCG@10 column, and nothing else.** The first version of
> this table shipped nDCG values **above 1.0**: every arm had 1–3 cases over the ceiling, the
> worst at **1.631** for a single gold span found at ranks 1 and 2. `ndcg_at_k` summed a gain
> per *retrieved rank* while dividing by an ideal counted per *required span*, and chunks
> overlap by 60 words — so a span straddling a boundary sits in two or three neighbouring
> chunks and was paid for two or three times. The metric now credits each span **once, at its
> earliest rank**, grades a rank by how many spans it is the earliest hit for, and builds the
> ideal from those grades plus one grade-1 item per span never found. It is bounded at 1 and
> still charges for partial recall.
>
> **Only the nDCG@10 column moved. Every per-case rank in the re-run is bit-identical to the
> original artifact**, and `recall@20`, `recall@6`, `precision@6` and `MRR@20` are unchanged
> to the last digit. The four numbered conclusions below — and every paired comparison in the
> table that follows — are computed on `recall_20` and `recall_6` only, so **none of the four
> overturned claims is affected**: A0 is still the strong baseline, the prefix still costs
> 3.8 pp, the reranker still buys ordering rather than reach, and the graph arm is still a
> net negative. Nothing here is a reason to re-litigate them. Artifact regenerated at the
> same path from the same gold set (`gold_set_hash` unchanged).

Paired deltas, bootstrap 95% CI and exact McNemar:

| comparison | Δ recall@6 | 95% CI | discordant | p |
|---|---:|---|---|---:|
| A0 → A1 (structure) | +0.019 | −0.094 … +0.151 | 11 (6 for A1) | 1.00 |
| A1 → A2 (prefix) | **−0.038** | −0.113 … +0.038 | 4 (1 for A2) | 0.63 |
| A2 → A3 (hybrid) | **+0.085** | **+0.019 … +0.170** | 5 (5 for A3) | 0.06 |
| A3 → A4 (rerank) | +0.009 | −0.066 … +0.085 | 5 (3 for A4) | 1.00 |
| A0 → A4 (everything) | +0.075 | −0.038 … +0.198 | 12 (8 for A4) | 0.39 |
| L2 → A4 (BM25 earns it) | **+0.066** | **+0.009 … +0.142** | 4 (4 for A4) | 0.13 |
| L1 → A4 (graph costs it) | **−0.019** | −0.057 … +0.000 | 1 (0 for A4) | 1.00 |

**1. A0 is much stronger than this section assumed, and only one arm beats it with an
interval that excludes zero.** A competent naive baseline — text layer, 400-word windows,
the *same* embedder — already reaches **recall@20 = 0.934**. The end-to-end A0 → A4 delta on
recall@6 is **+0.075 (95% CI −0.038 to +0.198, p = 0.39): not significant at n = 53**, exactly
as §4.3 of the eval design predicted for an effect under 15 points. The honest headline is
"+7.5 points, which this sample cannot defend", and it is a better sentence than a decimal
place nobody can challenge.

**2. Structure and the enriched prefix did not pay for themselves here.** A1 is +1.9 pp on
recall@6 and **−2.8 pp on recall@20**; A2 is **−3.8 pp on recall@6** against A1. D7's cited
"Context@5 33.3% → 55.0%" does not reproduce on this corpus with this embedder. One mechanism
is measured rather than guessed: `BAAI/bge-small-en-v1.5` truncates at **512 tokens**, a
400-word chunk is ~520, and adding the prefix takes the share of chunks that overflow from
**15% to 21%** and pushes one more gold span past the cut. That accounts for about one of the
two cases A2 loses, so it is a partial explanation and is reported as one. The production
embedder (`text-embedding-3-large`, 8191 tokens) does not have this ceiling — which makes
"re-run the ladder under the gateway embedder" the single highest-value follow-up, and makes
**chunk_size vs embedder context a real coupling this phase never wrote down**.

**3. The reranker's +12.1 pp does not reproduce — but it is not doing nothing.** A3 → A4 moves
recall@6 by **+0.009** (5 discordant, 3 favouring A4). The recall@20 → recall@6 gap is
**−9.4 pp for A3 and −8.5 pp for A4**: reranking recovers **0.9 pp** of what truncating 20
candidates to 6 costs. What it *does* buy is **ordering**: MRR@20 **0.557 → 0.686 (+12.9 pp)**
and nDCG@10 **0.622 → 0.732** (corrected — see the note under the table). So the 1.44 s/query measured in D6 buys a better-ordered
answer, not a materially more complete one, and D6's "+12.1 pp recall@5" should be read as an
external result we did not reproduce rather than as ours. **A4's recall@20 is identical to
A3's by construction** — a reranker reorders the pool and cannot add to it — which is why this
gap is the reranker isolated and not a proxy for it.

**4. The leave-one-out probes disagree with each other, and the graph arm loses.** Removing
BM25 (L2) costs **9.4 pp of recall@20 and 6.6 pp of recall@6** — the BM25 arm is the one piece
of this pipeline that clearly earns its place, and D5's "single largest quality gap" is the
one claim in the phase the measurement supports outright. Removing the **graph** arm (L1)
**improves** every metric: recall@20 0.915 → **0.972**, recall@6 0.830 → **0.849**. On this
corpus the graph arm is a net negative, because RRF gives a co-occurrence-expanded list equal
standing with two arms that are actually about the query. D14 said to position the graph as
the relational/explainability arm and not the quality engine; **it is worse than that here —
it is a quality cost**, and the ablation would have contradicted us on our own slide exactly
as D14 warned.

**The subsets say where the work landed** — descriptive only, and labelled as such: at
n = 9 and n = 3 these cannot be statistically separated.

| kind | n | A0 | A2 | A3 | A4 | L2 (−BM25) |
|---|---:|---:|---:|---:|---:|---:|
| known-item (exact identifiers) recall@6 | 11 | 0.636 | 0.818 | 0.818 | **0.909** | 0.818 |
| table (the answer is a cell) recall@20 | 9 | 0.889 | 0.778 | 0.889 | 0.889 | **0.667** |
| multi-hop recall@20 | 3 | 0.833 | 0.833 | 0.833 | 0.833 | **0.500** |
| hand-written recall@6 | 30 | 0.800 | 0.767 | **0.867** | 0.833 | 0.800 |

The pattern is consistent with D5 and with nothing else in the ladder: the arm that carries
the **exact-identifier and table** questions — the ones a judge is most likely to ask on
stage — is BM25, and taking it away costs 22 points of table recall@20 and 33 of multi-hop.
The +27 pp A0 → A4 move on known-item questions is the one place the ladder does what §5
expected it to do everywhere.

Two limits on that last finding, stated rather than buried: the arm measured is
`InMemoryKnowledgeBackend`'s **co-occurrence expansion** (the lite shipped configuration), not
LightRAG's entity graph; and 3 multi-hop cases cannot separate anything. It is a real result
about the arm we ran, not a general claim about graph retrieval.

**What none of this changes:** the gold set, the primitive and the runner are the instrument,
and the instrument is verified — `test_ablation.py` asserts arm A3's ordering **equals**
`Retriever.retrieve`'s over the same backend, so the table is a measurement of the shipped
path rather than of a lookalike.

---

## 6. Cut order — decided now, not at 2am

1. ~~**The ingest-log detail panels**~~ — **not taken.** Both panels are one field on an
   event the stage writes anyway plus a small block in one component; the cut would have
   saved nothing. See the corrections under 4.12. Replay was never a cut — it is free once
   the events are in `run_events`.
2. **bbox renderer** — keep the data, drop the visual overlay
3. **Table NL summaries** — keep tables as objects, drop the generated summary
4. **Local reranker benchmark** — ship the 33M default rather than comparing three
5. ~~**The nightly graph rebuild in 4.13**~~ — **no longer cuttable, and no longer a second schedule.** `chunk` is delete-then-insert, so re-chunking destroys the graph metadata; a re-index that skipped `graph` would silently empty one retrieval arm. See the corrections under 4.13.

**Never cut:** page provenance, `chunks.tenant_id` (4.6), **the per-tenant vector collection
(4.6b)**, the BM25 arm, stage-level job progress, and the gold set. The first two are correctness; the last three are what turn claims
into evidence.

---

## 7. Definition of done

- [ ] A PDF uploaded through `POST /documents` is stored, enqueued, and **parsed by a worker,
      never inside the request**.
- [ ] `chunks.tenant_id` exists, is registered in the tenant-scoped catalogue, carries an RLS
      policy, and the live isolation test asserts **a lexical hit cannot cross tenants**. This
      is merged in the same change as `keyword_recall`, or neither is merged.
- [ ] `chunks.document_id` is a real foreign key to `documents.id` with `ON DELETE CASCADE`,
      and deleting a document removes its chunks — asserted, not assumed.
- [ ] **Each tenant's vectors live in their own collection.** Two tenants issuing the same
      query get disjoint results asserted on *content*; a retrieval whose tenant does not
      resolve returns nothing **and raises**, rather than falling back to an unscoped search.
      Merged with the `chunks.tenant_id` change, or neither is merged.
- [ ] A deliberately multi-column fixture parses to a **low `parse_confidence`**, and that
      figure is visible in the ingest log. A gate that never fires is not a gate.
- [ ] `LightRAGBackend.keyword_recall` searches the **whole tenant corpus**, not the dense
      pool — proved by a query whose answer dense retrieval alone does not return.
- [ ] Heading levels come back as a real tree, not `{1: N}` — asserted on the golden fixture.
- [ ] **Killing the worker during the `embed` stage resumes at `embed`, not at `parse`** —
      tested by actually killing it, and verified by the parse stage not re-running.
- [ ] Three documents uploaded at once produce three queued jobs with queue positions, and
      **only one parse runs at a time**.
- [ ] An upload whose cost estimate exceeds the tenant budget is refused **at upload**, with
      the existing `BudgetExceeded` reason string.
- [x] Re-uploading identical bytes returns the existing document and starts no second
      ingest — enforced by `UNIQUE (tenant_id, content_sha256)`, and deliberately *not* by
      the corpus version; see the correction under 4.8.
- [x] **An ingest bumps that tenant's `corpus_version` and no other tenant's**, and an answer
      cached before the upload is unreachable afterwards — asserted on both tenants, since a
      global counter would satisfy the first half and quietly discard everybody else's cache.
- [x] **A re-index rebuilds chunks, embeddings and index from the stored parse artifact with
      the parser never called** — asserted on a spy, not on a stopwatch — and bumps
      `corpus_version` once per run.
- [x] Ten re-index requests inside the debounce window still produce **one** run with the real
      handler doing real work, not only with Phase 3's recording double.
- [ ] Cancelling an in-flight ingest stops it at the next stage boundary and the spend stops
      with it.
- [ ] The ingest log streams stage transitions live, survives a page refresh, and names the
      per-document OCR decision.
- [ ] The nightly re-index schedule fires **once** per tick with two workers running. (The fold itself is proved; what remains unproved here is two *workers* racing one tick.)
- [ ] The reranker runs locally, its measured p50/p95 latency over 20 passages is recorded in
      the phase notes, and an induced failure falls **loudly** to the API reranker.
- [ ] Every citation's quoted span is verified verbatim against its chunk.
- [ ] The ablation table A0→A4 exists with paired bootstrap intervals, and the slide states
      what n=50 can and cannot defend.
- [ ] **A PII check, an ingest and an ML prediction in one process do not kill it** —
      the `OMP_NUM_THREADS=1` pin of D4a is in place at every entry point. Two OpenMP
      runtimes is a segfault, and a segfault is not a test failure, it is a dead demo.
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

  **[CLOSED] 2026-08-19 by 4.10, and the shape of the risk was wrong.** The bill is not
  dominated by the number of calls — 37 cheap-model calls on the 126-page IRS fixture — but by
  the *input tokens*, whose uncapped total for that one document is ~800 k. The per-table
  prompt cap is what bounds it; the content-hash cache is what stops a re-upload or a 4.13
  re-index paying twice; the size threshold is the smallest of the three levers. All three are
  asserted on a call-counting spy in `backend/tests/ingestion/test_table_summaries.py`.

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
