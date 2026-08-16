# Ingestion SOTA — what state of the art actually is in 2026, and what we should build

> **Scope.** Everything between "PDF bytes" and "a retrievable, citable chunk". The parser
> itself (Docling) is being verified separately; this document treats the parser as a seam
> and designs the pipeline *around* it.
>
> **Constraints this document respects, without exception.** Windows laptop, 16 GB RAM, no
> Docker, no GPU, CPU inference only. Python 3.11, `pandas>=2.2,<2.4`. Postgres + Neo4j
> Desktop + Memurai native, ChromaDB embedded in-process. ~3 engineering days for ingestion.
> Offline-capable: every model must be pre-downloadable and must run with the network off.
>
> Anything that needs a GPU is named and ruled out explicitly rather than quietly omitted.

---

## 0. The answer in one paragraph

**State of the art in 2026 is not a clever chunker. It is a typed document pipeline with
provenance carried end to end, and a boringly conventional retrieval stack on top of it.**
The strongest systems parse into a *structured document tree* (headings with real nesting,
tables as table objects, per-item page and bounding box) rather than into a text blob; chunk
on **structural boundaries** at paragraph/section granularity; prepend **context** to each
chunk before embedding (Anthropic-style LLM-generated context, or its cheap deterministic
cousin, a heading-path header); treat **tables as first-class objects** that are never packed
into prose and whose *embedded* representation is a natural-language summary rather than a
pipe grid; index into **hybrid dense + lexical** stores; and finish with a **cross-encoder
rerank**. The two ends of the pipeline are where the field genuinely moved: parsing has gone
**VLM-first** at the top of the leaderboards (dots.ocr, PaddleOCR-VL, MinerU 2.5, olmOCR 2 —
all GPU-bound, all out for us), and indexing has moved from "chunk-then-embed" toward
**embed-then-chunk** (late chunking) and multi-granularity parent–child indexes. Equally
important, the 2026 empirical literature is unusually clear about what is *not* worth it:
**semantic chunking, proposition/DenseX chunking, and LLM-driven chunking all lose to plain
structure-based chunking on in-corpus retrieval, at 10²–10⁴× the cost.** Our existing
`chunk_structured` is already on the winning side of that result. The gap between Aegis and
SOTA is not the chunker — it is that we have no parser, no table handling, no page
provenance, no citation verification, and no measurement.

---

## 1. What SOTA is, with citations

### 1.1 Layout-aware parsing is a prerequisite, not an upgrade

The consistent framing across 2026 practitioner and academic sources is that layout-aware
parsing has stopped being a differentiator and become a precondition: it preserves reading
order and table structure, and it emits the element type, page number, and coordinates that
make retrieval explainable and debuggable. Chunking downstream can then follow *real*
document boundaries instead of guessing them from character counts.

The ICSE-SEIP '26 study on financial QA (`arXiv:2604.12047`) is the cleanest recent
demonstration that the *parser × chunker* pair, not either alone, determines answer
correctness — it evaluates parsers and chunking strategies jointly on FinanceBench (text QA)
and TableQuest (table QA) precisely because the interaction is the effect.

### 1.2 Parsing has gone VLM-first — and that door is closed to us

The top of OmniDocBench in 2026 is occupied by vision-language document parsers:
MinerU 2.5-Pro (1.2 B), dots.ocr (1.7 B), PaddleOCR-VL, DeepSeek-OCR, olmOCR 2. LlamaIndex
went as far as publishing that OmniDocBench is *saturated* by these models. They are
genuinely better than layout-model pipelines on hard pages (multi-column, handwriting,
dense tables).

**Ruled out.** A 1.2–1.7 B VLM doing full-page autoregressive inference is GPU work. On CPU
these are tens of seconds to minutes per page, and 16 GB shared with Neo4j, Postgres,
Memurai, Chroma and Next.js has no room for the weights. The same verdict applies to the
small end (`granite-docling-258M`, `SmolDocling-256M`): 258 M params × every page, on CPU, is
not interactive ingestion. **Layout-model + table-structure-model pipelines (i.e. Docling's
default, non-VLM path) are the correct and only choice under our constraints**, and that is a
constraint-driven choice, not a claim that it is the best available parser in the world. Say
it that way to the jury; it is a stronger answer than pretending.

The same reasoning rules out **ColPali-style visual retrieval** (`arXiv:2407.01449`), which
skips OCR entirely and embeds page images as ~1030 patch vectors. It is elegant, it is
strong on scanned and chart-heavy documents, and it costs roughly **170× the index storage
per page** of single-vector text retrieval, needs a VLM at both index and query time, and
recent multi-vector systems report **>7 s query latency on CPU**. Out.

### 1.3 Chunking: the 2026 literature says "stop over-engineering it"

Three independent results converge.

| Source | Finding |
|---|---|
| Qu, Tu & Bao, *Is Semantic Chunking Worth the Computational Cost?* (NAACL 2025 Findings, `arXiv:2410.13070`) | Across document retrieval, evidence retrieval and answer generation, **the computational cost of semantic chunking is not justified by consistent gains**. Sentence-boundary splitting performs nearly as well. |
| *Beyond Chunk-Then-Embed* (`arXiv:2602.16974`, 2026) | On BEIR in-corpus retrieval, **paragraph-based (structure) chunking wins**: avg nDCG@10 0.4303–0.4997 depending on embedder. **Proposition-based chunking underperforms by 15–27 %.** LLM-guided chunking (LumberChunker) gives no in-corpus advantage while running **1,600× slower** (1.11 docs/s vs 1,854 docs/s). Chunk size correlates only weakly with in-corpus nDCG (r = 0.08–0.18). |
| *Chunking Methods on RAG — Effectiveness vs Computational Cost* (`arXiv:2606.00881`, 2026) | 8 methods, 9 datasets. Accuracy@5: Recursive Semantic 89.36, **Fixed-Size 87.71**, GraphSeg 86.85 — i.e. within ~2 points. Runtime: **Fixed-Size <1 s; LumberChunker 8.37 h; DenseX 15.05 h.** Several "advanced" methods failed outright on timeouts/memory. Conclusion: *"chunk quality and structural coherence are more important than chunk quantity."* |

Chroma's chunking evaluation (token-level recall/precision/IoU rather than document-level IR
metrics) points the same way: `RecursiveCharacterTextSplitter` @200 tokens scored 88.5 %
recall / 6.9 % IoU against `ClusterSemanticChunker` @200 at 87.3 % / 8.0 % — a real but small
precision edge for the expensive method, and a **9-point spread in recall across methods**
overall, which is where the money actually is.

**Read this correctly.** It does not say chunking is unimportant. It says the *marginal*
methods (semantic, proposition, LLM-driven) do not pay, while getting the *structural*
boundaries right does.

### 1.4 The two techniques that genuinely move the number

**Contextual retrieval** (Anthropic, 2024, still the reference result in 2026): prepend
50–100 tokens of LLM-generated situating context to each chunk before embedding and before
BM25 indexing.

- Contextual embeddings alone: top-20 retrieval failure **5.7 % → 3.7 % (−35 %)**
- \+ contextual BM25: **→ 2.9 % (−49 %)**
- \+ reranking: **→ 1.9 % (−67 %)**
- Cost with prompt caching: **$1.02 per million document tokens**.
- Anthropic explicitly tested and rejected the cheap-sounding alternatives: generic document
  summaries, HyDE, and summary-based indexing showed *"very limited gains"* or *"low
  performance"*.

The single largest step in that ladder is **reranking** (2.9 → 1.9), and the second is
**hybrid dense+lexical** (3.7 → 2.9). Both are things we already have architecture for.

**Late chunking** (Günther et al., `arXiv:2409.04701`, Jina AI + Weaviate): embed the whole
document with a long-context model, *then* pool token embeddings per chunk. nDCG@10 gains on
BEIR: TREC-COVID 0.591 → 0.638, DBpedia 0.421 → 0.466, SCIFACT 0.681 → 0.710. The 2026
taxonomy paper reproduces this as a **+6 % average in-corpus gain** — and finds it *degrades*
in-document retrieval by −5 % to −53 %, so it is task-specific, not free.

> ⚠️ **Late chunking is architecturally unavailable to us today, and this is the most
> important negative finding in this document.** It requires **token-level embeddings** from
> a **long-context** model. An embedding API returns one pooled vector per input. Aegis
> embeds through the gateway (`text-embedding-3-large`, `EMBED_DIM = 3072`,
> `pipeline.py:67`). Adopting late chunking means adopting a *local* long-context embedder,
> re-embedding the entire corpus, and changing `embed_dim` — which also collides with
> LightRAG's stored vectors. That is not a 3-day change. It is the #1 item on the
> more-time list, conditional on going local-embeddings first.

### 1.5 Hierarchical / parent–child indexing is the cheap real win

Decoupling *retrieval granularity* from *generation context*: index small child chunks for
precision, return the enclosing parent section for the model to read. H-RAG at SemEval-2026
(`arXiv:2605.00631`) uses exactly this shape, and it is the pattern most consistently
described as the production default in 2026 write-ups.

Distinguish it from **RAPTOR**, which recursively clusters and *LLM-summarises* into a tree.
RAPTOR is a genuine capability for multi-hop over long documents but it is an LLM call per
cluster per level, and Anthropic's own negative result on summary-based indexing is a
warning sign. **Parent–child: yes, and nearly free for us. RAPTOR: no, not at 3 days.**

### 1.6 Tables are where naive pipelines lose, and where the field agrees

TableRAG (`arXiv:2506.10380`) states the failure directly: *"flattening tables and chunking
strategies disrupts the intrinsic tabular structure, leads to information loss"* and breaks
multi-hop reasoning. Its answer is to keep the table as a **relation** and answer numeric /
aggregate questions by generating and executing SQL against it, interleaved with text
retrieval. Practitioner sources converge on the same three rules: **late-interaction and
sparse-hybrid retrievers beat dense-only on table queries**; tables must be stored in a
structured representation so retrieval returns evidence the model can reason over; and a
markdown pipe-grid is a poor *embedding* target even when it is a good *reading* target.

### 1.7 Citation verification is a live 2026 gap, and therefore an opportunity

Citation *generation* is now commodity (Perplexity, ChatGPT search, Gemini grounding).
Citation *verification* is not. `arXiv:2605.06635` ("Cited but Not Verified") is built around
exactly that asymmetry in deep-research agents. FullCite (`arXiv:2606.07130`) proposes the
standard we should copy: **every claim carries a source identifier *and* a verbatim evidence
span from that source**. The practitioner consensus adds span-level (not document-level)
citations with visible quote boundaries, claim-level attribution, and freshness/provenance
metadata.

That is a rubric-scoring differentiator available to us for a couple of hours of work, and
it is *not* what other teams will have.

---

## 2. Recommended pipeline architecture, stage by stage

Each stage names what it produces and why it exists. Stages marked **[3-day]** are in the cut
in §4; the rest are the target architecture.

### S0 — Intake and triage **[3-day]**

Content SHA-256, MIME sniff, byte cap, **page cap**, and a **per-page text-layer probe**.

Output a *document profile* before any expensive work: page count, pages with/without a text
layer, table count, estimated chunk count. This is 20 lines and it buys three things: the
per-page OCR decision (OCR only pages with no text layer — full-document OCR on CPU is
minutes for no gain on text-native PDFs), an honest "this document is scanned, it will take
N minutes, proceed?" message instead of a hang, and a live-log event that looks like
engineering rather than a spinner.

### S1 — Parse to a typed document tree **[3-day]**

Docling behind `convert.py`, producing `ParsedDocument{sections, tables, pages, warnings}`
where every item carries `heading_path`, `level`, `page_no`, `bbox`.

**Carry `page_no` and `bbox` from the first line of this module.** They are free at parse
time and expensive to retrofit (see §6, defect 2).

### S2 — Normalise **[3-day, 30 minutes]**

The step nobody plans and everybody needs:

1. **Running header/footer removal.** Detect lines that repeat at the same vertical position
   across ≥3 pages and drop them.
2. **Page-number stripping.**
3. **De-hyphenation** across line breaks (`inter-\nnational` → `international`).
4. Whitespace/ligature normalisation.

Without (1) and (2), every page contributes the same header text, our 3-shingle Jaccard
dedup fires on it, and the live log reports an alarming near-duplicate count on a perfectly
good document. That will read as a bug on stage. Cost: half an hour. This is the best
effort-to-embarrassment-avoided ratio in the whole phase.

### S3 — Route by element type **[3-day]**

Prose → the structural chunker. **Table → the table pipeline (S5), never the prose packer.**
Figure/caption → attach to the nearest section as metadata. Code/formula → keep verbatim,
never sentence-split.

### S4 — Chunk **[3-day, mostly already built]**

`chunk_structured` survives intact. Three additions, in value order:

1. **Never split a table** (enforced by S3 routing).
2. **Richer contextual header.** We already prepend `[A > B]`. Extend to
   `[<doc title> · <heading path> · p.<page>]`. Still deterministic, still free, still no LLM
   call — it is the cheap end of Anthropic's contextual retrieval and it is the technique
   with the best evidence-per-rupee in §1.4.
3. **Parent–child.** The section is the parent; the packed window is the child. Embed
   children, return the parent (or child ± neighbours) as answer context. This is a metadata
   change plus a retrieval-time expansion, not a new chunker.

Do **not** move to token-based sizing yet, and do not chase a "better" chunk size: in-corpus
nDCG correlates with chunk size at r = 0.08–0.18 (§1.3). 400 words ≈ 530 tokens is inside the
flat part of the curve.

### S5 — Table pipeline **[3-day — promoted, see §6]**

Per table, produce **three artefacts**:

| Artefact | Purpose |
|---|---|
| **Structural markdown** (header row repeated when the table spans chunks; header omitted for a row that would overflow with it) | what the model *reads* |
| **A one-line natural-language summary** — caption + section path + what the columns are + the row/column count and units | what gets *embedded* (a pipe grid embeds badly; a sentence embeds well) |
| **Row-group chunks with the header prepended to each group**, for tables over ~15 rows | what BM25/FTS *matches* — this is how "what was the Q3 figure for X" finds the right row |

Store the grid itself as the retrieved payload plus, if there is time, as a `jsonb` grid in
Postgres so a future structured-query tool can run real SQL over it (TableRAG's argument,
§1.6). The `jsonb` column is one DDL line now and a whole capability later; the SQL tool is
post-hackathon.

> The dominant table failure mode is *not* extraction. It is that a number is retrieved
> without its unit, its period, or its row label. Header repetition and the row-group chunk
> are what fix that, and they are both cheap.

### S6 — Validate and label trust **[already exists]**

`validate_content` already runs per chunk before any write. Add the `trust` label so
tenant-uploaded text is spotlighted everywhere, including into the *entity-extraction* system
prompt, which is currently an unspotlighted model call over hostile input.

### S7 — Embed

Gateway (`text-embedding-3-large`) stays primary. See §3.3 for the local-model assessment and
the offline-fallback design.

### S8 — Index

Vector (Chroma, tenant-scoped) + Postgres `tsvector` GIN (lexical, exact identifiers) +
graph (stage 2, LightRAG). Two-stage commit: answerable after vectors, connected after graph.

### S9 — Provenance record

Every chunk row stores: `document_id`, `ordinal`, `section_path`, `page_no`, `bbox`,
`word_start`, `word_count`, `content_hash`, `corpus_version_at_ingest`. This tuple is what
makes a citation *checkable later*, not just displayable now.

### S10 — Answer-time quote verification **[3-day, 2 hours]**

See §3.4. Not strictly "ingestion", but it is the stage that converts ingestion provenance
into a jury-visible trust property, and it is worthless without S9.

---

## 3. The five questions, answered directly

### 3.1 Chunking — real win vs churn

| Technique | Verdict | Why |
|---|---|---|
| **Structure-aware chunking** | **Real — already have it** | Wins in every 2026 comparison for in-corpus retrieval. `chunk_structured` is on the right side of the literature. Do not replace it. |
| **Contextual headers (deterministic)** | **Real — extend it** | Cheap end of the technique that gave Anthropic −35 % failure rate. We prepend the heading path; adding doc title + page is minutes. |
| **Contextual retrieval (LLM-generated context per chunk)** | **Real but not now** | Strongest single-chunk-level result in the literature. Costs one LLM call per chunk — 300 chunks per document, on a metered gateway, in a job that must finish while a jury watches. Revisit with prompt caching post-hackathon. |
| **Hybrid retrieval + cross-encoder rerank** | **Real, and the biggest lever we are not pulling** | Anthropic's ladder: hybrid 3.7→2.9, rerank 2.9→1.9. Not a Phase-3 task today; see §5. |
| **Parent–child / hierarchical** | **Real, cheap** | Decouples retrieval precision from generation context. Metadata + retrieval expansion, no new chunker. |
| **Late chunking** | **Real, but blocked** | +4–11 % nDCG@10 on BEIR, +6 % in-corpus in 2026 replication. Requires token-level embeddings from a local long-context model. Impossible through an embedding API. Blocked on a local-embeddings decision. |
| **Semantic chunking** | **Churn** | NAACL 2025 Findings: cost not justified. ~14× slower for ≤2 points. |
| **Proposition / DenseX chunking** | **Churn, actively harmful** | 15–27 % *worse* in-corpus; 15.05 h vs <1 s runtime; frequently fails outright. |
| **LLM/agentic chunking (LumberChunker etc.)** | **Churn for us** | 1,600× slower, no in-corpus advantage. Only wins for in-document retrieval, which is not our task. |
| **RAPTOR summary tree** | **Churn at 3 days** | LLM call per cluster per level; Anthropic's summary-indexing negative result applies. |

**Honest summary:** our chunker is already at or near the empirical frontier for our task.
The quality left on the table in Phase 3 is *not* in the chunking algorithm. It is in
**tables, page provenance, and header/footer hygiene** — and, outside Phase 3, in **reranking**.

### 3.2 Tables

Best-in-class handling, in order of value under our constraints:

1. **Keep the table intact as its own chunk.** Never pack it into surrounding prose. This one
   rule recovers most of the loss.
2. **Repeat the header row on split; omit the header on single-row overflow.** Docling's
   `HybridChunker` already implements both behaviours; port them, do not adopt the chunker
   (we would lose our overlap-offset correctness, section-scoped dedup, and provenance
   record).
3. **Embed a natural-language summary, retrieve the grid.** Separate the embedding text from
   the payload text. This is the highest-leverage single decision in table handling and it is
   about 30 lines.
4. **Row-group chunks with repeated headers** for long tables, so the lexical arm can match a
   specific row.
5. **Store the grid structurally** (`jsonb`) alongside the markdown, so a later structured
   query tool can execute over it rather than reasoning over prose. TableRAG's result says
   this is where multi-hop numeric questions are actually won — but the SQL tool itself is
   post-hackathon.

**What not to do:** do not build a separate table index with its own retrieval arm in three
days. Fusing a fourth arm that we cannot measure is worse than not having it.

### 3.3 Embeddings and reranking on CPU

**Where the embedding landscape actually is.** `text-embedding-3-large` (MTEB ≈ 64.6) is a
2023 model and has been passed: Qwen3-Embedding tops the 2026 leaderboards (≈ 70.6), Gemini
embeddings ≈ 68.3, Cohere embed-v4 ≈ 65.2. On the small end, **EmbeddingGemma-300M** is the
notable 2026 development: 308 M params, 2 K context, **MTEB English v2 69.67 / Multilingual
v2 61.15**, **under 200 MB RAM quantised**, Matryoshka truncation 768→512→256→128, and an
official ONNX build (`onnx-community/embeddinggemma-300m-ONNX`). Qwen3-Embedding-0.6B is the
other strong sub-1 GB option (multilingual MTEB ≈ 64.3).

> ⚠️ MTEB v1 and v2 numbers are **not** directly comparable. Do not put a 64.6-vs-69.67
> comparison on a slide as though it were a like-for-like win. It is not.

**Is a local embedder an improvement or a regression?** Split the question:

- **Against the models `fastembed` actually ships today** (bge-small-en-v1.5 384 d / 67 MB,
  arctic-embed-xs, all-MiniLM-L6-v2, and up to bge-large / multilingual-e5-large at 1.2–2.2 GB):
  moving to the small end is a **quality regression** versus the gateway. Moving to the large
  end costs 1–2 GB of RSS on a 16 GB box that is already hosting Neo4j's JVM.
- **Against EmbeddingGemma-300M in ONNX**: probably **not a regression at all**, and possibly
  an improvement — but it is a 768-dim model where the pipeline is wired for 3072
  (`EMBED_DIM`), it requires re-embedding everything, and it collides with LightRAG's stored
  vectors. **That is not a 3-day change and it should not be attempted as one.**
- **On resilience it is unambiguously an improvement.** The venue network is unreliable and
  the gateway is a network call on *both* the ingest path and the query path. Today, a dead
  network means nothing works.

**Recommendation:** keep the gateway as primary. Do **not** swap the embedder in Phase 3.
If half a day appears, add a *declared, labelled* local ONNX fallback writing into its own
dimension-tagged collection (the codebase already has the `embedding_dim` discipline in
`memory/stores.py` and `vector_ops.py`) so a network failure degrades embeddings instead of
deleting the capability. Say "our embeddings degrade, they do not disappear" — that is an
honest enterprise answer, and it is much better than discovering it live.

**Reranking — the prior `fastembed` + tiny-jina call is still right, with one correction.**
`fastembed` 0.8.0 (released 2026-03-23) ships `TextCrossEncoder` over ONNX Runtime with no
torch. Available rerankers:

| Model | Size | Note |
|---|---|---|
| `Xenova/ms-marco-MiniLM-L-6-v2` | 0.08 GB | fastest, weakest |
| `jinaai/jina-reranker-v1-tiny-en` | 0.13 GB | 33 M params, built for CPU/AI-PC |
| `jinaai/jina-reranker-v1-turbo-en` | 0.15 GB | usually the better quality/latency point |
| `BAAI/bge-reranker-base` | 1.04 GB | ~278 M params |
| `jinaai/jina-reranker-v2-base-multilingual` | 1.11 GB | multilingual, strongest here |

CPU cross-encoder latency is roughly **~8 ms per (query, document) pair** for a 278 M-param
model, so reranking `recall_top_k = 20` candidates costs ≈ 160 ms and 50 candidates ≈ 400 ms.
That is affordable inline. **Do not lock to the 33 M tiny model on principle — benchmark
tiny/turbo/bge-base in the spike and pick on measured quality-per-millisecond.** The tiny
model is the floor, not the target.

**Dependency risk, resolved — this is a concrete correction to plan 03.** Plan 03 §2.2 and
risk R9 flag a possible `onnxruntime` clash between `chromadb` and `fastembed`. Checked
against current package metadata:

- `chromadb` 1.5.9 requires `onnxruntime>=1.14.1`, `numpy>=1.22.5`, `tokenizers>=0.13.2` —
  **no upper caps.**
- `fastembed` 0.8.0 on Python 3.11 requires `onnxruntime>=1.17.0` excluding 1.20.0 / 1.24.0 /
  1.24.1, `tokenizers>=0.15,<1.0`, `numpy>=1.21`.

**The ranges intersect cleanly on Python 3.11. There is no clash.** `onnxruntime` is
additionally already in the tree via the `nemoguardrails` path. Still verify in the spike,
but stop treating this as a blocker.

**Not affected by any of this:** the *LLM* reranker we ship today is a per-query gateway call
and the dominant per-query cost. Replacing it with a local cross-encoder makes reranking
free, deterministic, and offline-capable, and it makes evals reproducible. It is the single
highest-value half-day available anywhere in the retrieval stack. It is currently scheduled
in plan 03 Phase 6, i.e. not in the 14 days. See §5.

### 3.4 What makes a citation trustworthy

Four layers, each strictly stronger than the last. We can have all four for a few hours.

**L1 — Locator.** `document_id + page_no + bbox + section_path + word_start/word_count`.
Docling supplies page and bbox per item; the chunker already supplies the section path and
the word span (with the overlap-corrected `word_start` arithmetic that the code comment at
`chunker.py:265-269` protects). The only missing link is threading page/bbox from the parsed
section onto `ChunkPiece`.

**L2 — Verbatim quote.** The answer must carry an exact substring of the stored chunk, not a
paraphrase. Follow FullCite's shape: source id + verbatim evidence span per claim.

**L3 — Verification, done deterministically after generation.** For each cited span:
normalise whitespace and casing, assert it is a substring of the stored chunk text; on
failure, fall back to a fuzzy ratio and report it. **A citation that fails verification is
labelled `unverified` in the UI and never silently rendered as a citation.** This is ~40
lines, has no model call, cannot regress, and is precisely the gap the 2026 literature says
nobody is filling.

**L4 — Visual proof.** `bbox` → render the page region with the sentence boxed. Click a
citation, see the actual PDF page with the actual sentence highlighted. That is the strongest
single visual moment available in this phase and it is *why* bbox must not be cut.

**Also required, and cheap:** store `corpus_version_at_ingest` and `content_hash` on the
chunk so a citation is re-checkable after the corpus changes. A citation that cannot be
re-checked is a screenshot, not evidence.

### 3.5 Proving ingestion quality in 3 days

The current Phase 3 definition of done contains **zero quality measurements**. Every checkbox
is functional (does it ingest, does it resume, does it isolate). In a phase whose entire
justification is answer quality, judged against a rubric that rewards *measured, not claimed*,
that is the largest gap in the plan. Fixing it costs half a day.

**The 3-day-affordable evaluation:**

1. **A golden set of 40–60 cases.** The literature's guidance is 50–200 queries with
   identified correct sources; 40–60 is defensible and reachable. Each case:
   `query → (document_id, page_no, section_path)`.
   Build it by generating candidate questions from the ingested chunks with the gateway model,
   then **hand-verifying every one**. Be honest in the write-up that LLM-generated questions
   are biased toward retrievable chunks — and mitigate it by **hand-writing ~10 cases that
   require a table, and ~5 that require joining two sections**. Those are the cases that will
   discriminate.
2. **Primary metric: recall@k** (k = 5, 10, 20). If the right chunk is not in the pool, no
   downstream cleverness recovers it. Target recall@10 ≥ 0.8. Secondary: **nDCG@10** and
   **page-level precision** (did the cited page match the gold page).
3. **The ablation that earns the marks: naive baseline vs our pipeline.** Naive = `pypdf`
   `extract_text()` + fixed 400-word windows, no section headers, no dedup, no table handling.
   ~40 lines. Same golden set, same embedder, same retriever. The output sentence is
   *"structure-aware ingestion moved recall@10 from X to Y on our corpus"* — a measured
   business claim. *"We use Docling"* is not a claim at all.
4. **Two nearly-free additional ablations:** with/without the contextual header prefix;
   with/without table-aware chunking. Report the table subset separately — that is where the
   delta will be largest and most persuasive.
5. **Structural regression fixtures, added the day Docling lands, not later.** One golden PDF
   with hand-checked expected counts: headings and nesting depth, tables and per-table cell
   counts, chunk count, every chunk has a section path, every `word_start + word_count` inside
   the document. Ingestion quality regresses *silently* — nothing else will notice.

> ⚠️ Do not run this through `build_eval_retriever()` as it stands. Plan 03 §1.9 is right:
> it uses `_fake_embed` and a `_fake_complete` that returns `""`. It is an excellent
> deterministic CI gate and it measures nothing about the shipped pipeline. The golden-set run
> must use real embeddings and the real backend, or the number is unquotable.

---

## 4. The ruthless 3-day cut

Ordering principle: **provenance is threaded once, on day 1; the quality levers ship before
the observability polish; and the phase does not end without a measured number.**

### Day 1 — Bytes to structured chunks, with provenance already attached

| # | Task | Time | Note |
|---|---|---|---|
| 1 | **Spike on the actual machine.** Install extras, `docling-tools models download`, convert 3 PDFs (text-native, table-heavy, scanned), record wall clock + peak RSS. Verify a fully offline run. | 0.25 d | Keep exactly as planned. Every timing claim depends on it. |
| 2 | **`convert.py` seam — with `page_no`/`bbox` from line one.** Merge current task 3.8 into 3.1. | 0.4 d | 30 minutes now; 3 hours as a retrofit. |
| 3 | **S0 triage + per-page text-layer probe.** Document profile emitted as an event. | 0.15 d | Turns the scanned-PDF risk into a visible feature. |
| 4 | **S2 normalisation: header/footer/page-number stripping + de-hyphenation.** | 0.1 d | Prevents the dedup counter from reporting nonsense on a real PDF. |
| 5 | **`chunk_sections()` overload.** Everything downstream unchanged. | 0.15 d | Smallest change that unlocks PDFs. |
| 6 | **Structural golden fixture** (heading/table/chunk counts). | 0.1 d | Written the same day Docling lands. |

### Day 2 — Make it a product, and pull the table lever

| # | Task | Time | Note |
|---|---|---|---|
| 7 | **Postgres tables**: `documents`, `document_chunks` (incl. `page_no`, `bbox`, generated `tsvector` + GIN), `ingestion_jobs`, `ingestion_events`, `ingestion_ledger`. | 0.6 d | Trim: create the `tsvector` column, do **not** build FTS retrieval this phase. |
| 8 | **`POST /ingest/documents` + worker with the guarded claim + two-stage commit.** | 0.5 d | Copy `voice/transcribe`'s upload shape and `consolidate.py:996-1010`'s claim verbatim. |
| 9 | **S5 table pipeline — PROMOTED out of the cut list.** Table as its own chunk; repeated header on split; NL summary as the *embedded* text; row-group chunks for long tables. | 0.4 d | The largest quality lever in the phase. See §6 defect 1. |
| 10 | **First real end-to-end ingest.** | — | On day 2, not day 3. Budget for one surprise. |

### Day 3 — Prove it, then let people watch it

| # | Task | Time | Note |
|---|---|---|---|
| 11 | **Scope + `corpus_version` bump.** Ingest → same question → different answer, twice in a row. | 0.25 d | Non-negotiable. Skipping it is how the demo dies. |
| 12 | **Golden set (40–60) + naive-baseline ablation + recall@k / nDCG@10 report.** | 0.5 d | The highest-scoring half day in the phase. Currently absent from the plan entirely. |
| 13 | **Quote verification (L3).** Deterministic substring check; `unverified` label. | 0.15 d | ~40 lines, no model call, uniquely differentiating. |
| 14 | **SSE live log** — live tail first, event-table replay second. | 0.3 d | If anything slips, replay is what slips. |
| 15 | **Trust label into `build_spotlighted_context`** including the extraction system prompt. | 0.1 d | Injection front door. |

### What actually gets cut, and in what order

1. **Event-table *replay* in the SSE stream.** Live tail alone is 2 hours; replay-on-reconnect
   is a polish feature that costs more than it looks. Cut this before touching quality.
2. **`bbox` → highlighted page-crop rendering (L4).** Keep *storing* bbox (free); cut the
   renderer if the console work does not fit.
3. **Row-group table chunks.** Keep the table-as-own-chunk and NL-summary parts; the row
   groups are the refinement.
4. **The local ONNX embedding fallback.** Insurance, not quality.
5. **Nothing else.** Tables and page provenance do not get cut. See §6.

Explicitly still deferred, and correctly so: cost preflight and the confirm gate; the full
three-store delete path; token-based sizing; per-tenant LightRAG instances; FTS retrieval.

---

## 5. What we would add with more time, ranked

| # | Addition | Cost | Why this rank |
|---|---|---|---|
| 1 | **Local ONNX cross-encoder reranker** (`fastembed` `TextCrossEncoder`; benchmark tiny/turbo/bge-base). | 0.5 d | Biggest measured quality step in Anthropic's ladder (2.9 → 1.9 % failure). Removes the dominant per-query model call, makes rerank deterministic, makes evals reproducible, works offline. It is scheduled in plan 03 *Phase 6*, i.e. outside the 14 days. **If any half-day frees up anywhere in the 14, spend it here, not on ingestion.** |
| 2 | **Corpus-wide BM25 via the Postgres `tsvector` we are already creating.** | 0.5 d | Turns the honest "two-arm system described as three arms" into three arms. Hybrid was Anthropic's second-biggest step (3.7 → 2.9). Zero new dependencies; the tenant filter is a `WHERE` clause, so isolation comes free. |
| 3 | **Parent–child retrieval expansion.** | 0.5 d | Precision of small chunks, context of large ones. Metadata + retrieval-time expansion only. |
| 4 | **Structured table storage + a bounded SQL-over-table tool.** | 2 d | TableRAG's result: multi-hop numeric questions are won by executing over the relation, not by reading a flattened grid. Store the `jsonb` grid now (one DDL line) so this is additive later. |
| 5 | **Anthropic-style LLM contextual retrieval** with prompt caching. | 1 d + tokens | −35 % retrieval failure alone. Gated on budget and on ingest wall clock; measure the delta against our free deterministic header before paying for it. |
| 6 | **Local long-context embedder (EmbeddingGemma-300M ONNX) + late chunking.** | 3–4 d | +4–11 % nDCG@10 on BEIR, +6 % in-corpus in 2026 replication. Requires the embedder swap first (3072 → 768, full re-embed, LightRAG vector collision). Ranked here because the *prerequisite*, not the technique, is the expensive part. |
| 7 | **Bbox highlight rendering in the console (L4).** | 0.5 d | Pure demo value, very high per hour — but it is console work and the console plan owns it. |
| 8 | **RAGAS for recognised metric names** on the offline eval run only. | 0.5 d | Rubric optics. Never on the request path. Our in-house proxies stay, honestly labelled. |
| 9 | **RAPTOR-style summary layer.** | 2 d+ | Real for multi-hop over long documents; LLM cost per cluster per level, and Anthropic's summary-indexing negative result is a caution. |
| — | **VLM-first parsing, ColPali visual retrieval.** | — | **Ruled out permanently under these constraints.** GPU-bound at index *and* query time; >7 s CPU query latency; ~170× index storage. Revisit only if the deploy target changes. |

---

## 6. What I believe is wrong or misordered in `phase-03-ingestion.md`

Nine items, ordered by how much they cost.

**1. The cut order is inverted. Tables (3.7) and page/bbox provenance (3.8) are named as the
first two things to drop.** This is the most consequential error in the plan. Plan 03 §16
item 5 — written by the same effort — says table-aware chunking plus a per-table NL summary
is *"the single largest retrieval-quality lever on real PDFs"*, and the 2026 literature (§1.6)
agrees. A phase whose entire justification is answer quality cannot list its largest quality
lever as the first thing to cut. **Cut SSE event replay, then the bbox *renderer*, before you
touch the table pipeline.**

**2. `bbox`/`page_no` belong in task 3.1, not task 3.8.** The plan's own reasoning gives the
argument away: *"the cost is in threading them through the write path and the citation
surface, which is why it is cut second."* Exactly — so thread them once, on day 1, alongside
every other piece of metadata, instead of creating the retrofit the task is afraid of. Adding
two optional fields to a frozen dataclass is minutes; adding them to a write path that has
already shipped is hours.

**3. There is no quality measurement anywhere in the phase.** The definition of done has nine
checkboxes and all nine are functional. Nothing measures whether ingestion is *good*. Under a
rubric that rewards measured-not-claimed, and in a phase justified by answer quality, this is
the biggest single gap. **Add the golden set + naive-baseline ablation as a first-class task
(0.5 d), and add its number to the definition of done.**

**4. Normalisation is missing entirely.** No header/footer stripping, no page-number removal,
no de-hyphenation. Thirty minutes of work, and without it a real 40-page PDF will produce a
running header on every page, trip the section-scoped Jaccard dedup, and print an alarming
near-duplicate count in the live log during the demo. It will look exactly like a bug.

**5. OCR triage has a risk entry but no owning task.** The Risks section correctly identifies
that a scanned PDF with OCR off produces empty sections, and correctly says the mitigation is
per-page auto-enable — but no task in 3.0–3.8 implements the per-page text-layer probe. A
mitigation that nobody owns is not a mitigation. Put it in 3.1.

**6. Quote verification is missing.** The Trust section covers *input* hostility (injection)
thoroughly and correctly. It does not cover *output* honesty: whether the citation shown
actually appears in the cited chunk. That is a two-hour deterministic check, it is the exact
gap the 2026 attribution literature identifies, and it is the kind of thing a jury can be
invited to try to break.

**7. The stage-1 characterisation is slightly false.** Task 3.4 says stage 1 is
*"fast and deterministic"* — but stage 1 includes `embed`, which is a gateway network call.
On an unreliable venue network, stage 1 is neither fast nor guaranteed. Either say so
explicitly in the plan, or pre-warm/cache the embeddings, or accept the local-fallback item
from §4. Do not carry an inaccurate claim into a demo script.

**8. The `onnxruntime` conflict warning should be downgraded.** Plan 03 §2.2 and risk R9 flag
a possible `chromadb` × `fastembed` clash. On Python 3.11 the declared ranges intersect
cleanly (§3.3), and `onnxruntime` is already in the tree via `nemoguardrails`. Still verify in
the spike, but this should not be shaping decisions as a blocker.

**9. "Token-based sizing waits — the word budget is fine" is the right call for the wrong
reason.** It is fine not because tokens are unimportant but because chunk *size* is a weak
lever for in-corpus retrieval (r = 0.08–0.18, §1.3) and 400 words sits inside the flat part of
the curve. Keep deferring it — but do not later "fix" a systematic 25 % sizing error that the
evidence says will not move recall.

**Two things the plan gets exactly right and should not be talked out of:** keeping
`chunk_structured` and porting behaviours into it rather than adopting `HybridChunker`
wholesale (we would lose the overlap-offset correctness, the section-scoped dedup, and the
provenance record, and would have to re-solve all three); and refusing the VLM pipeline in
plain language.

---

## 7. Sources

**Chunking — empirical**
- Qu, Tu, Bao — *Is Semantic Chunking Worth the Computational Cost?* NAACL 2025 Findings —
  <https://arxiv.org/abs/2410.13070>, <https://aclanthology.org/2025.findings-naacl.114/>
- *Beyond Chunk-Then-Embed: A Comprehensive Taxonomy and Evaluation of Document Chunking
  Strategies for Information Retrieval* (2026) — <https://arxiv.org/html/2602.16974>
- *Chunking Methods on Retrieval-Augmented Generation — Effectiveness Evaluation Against
  Computational Cost and Limitations* (2026) — <https://arxiv.org/html/2606.00881v1>
- Chroma Research — *Evaluating Chunking Strategies for Retrieval* —
  <https://www.trychroma.com/research/evaluating-chunking>
- Chen et al. — *Dense X Retrieval: What Retrieval Granularity Should We Use?* —
  <https://arxiv.org/abs/2312.06648>
- *Reconstructing Context: Evaluating Advanced Chunking Strategies for RAG* —
  <https://arxiv.org/abs/2504.19754>

**Context injection and late chunking**
- Anthropic — *Contextual Retrieval* —
  <https://www.anthropic.com/engineering/contextual-retrieval>
- Günther et al. — *Late Chunking: Contextual Chunk Embeddings Using Long-Context Embedding
  Models* — <https://arxiv.org/abs/2409.04701>, code
  <https://github.com/jina-ai/late-chunking>
- *H-RAG at SemEval-2026 Task 8: Hierarchical Parent–Child Retrieval* —
  <https://arxiv.org/html/2605.00631v1>

**Parsing and tables**
- El Bachyr et al. — *Empirical Evaluation of PDF Parsing and Chunking for Financial Question
  Answering with RAG*, ICSE-SEIP '26 — <https://arxiv.org/abs/2604.12047>,
  <https://dl.acm.org/doi/10.1145/3786583.3786911>
- *TableRAG: A Retrieval Augmented Generation Framework for Heterogeneous Document Reasoning*
  — <https://arxiv.org/abs/2506.10380>
- OmniDocBench (CVPR 2025) — <https://arxiv.org/pdf/2412.07626>,
  <https://github.com/opendatalab/OmniDocBench>
- LlamaIndex — *OmniDocBench is Saturated, What's Next for OCR Benchmarks?* —
  <https://www.llamaindex.ai/blog/omnidocbench-is-saturated-what-s-next-for-ocr-benchmarks>
- Faysse et al. — *ColPali: Efficient Document Retrieval with Vision Language Models* —
  <https://arxiv.org/html/2407.01449v2>; scaling/storage —
  <https://blog.vespa.ai/scaling-colpali-to-billions/>

**Citation and attribution**
- *Cited but Not Verified: Parsing and Evaluating Source Attribution in LLM Deep Research
  Agents* — <https://arxiv.org/html/2605.06635v1>
- *Explicit Evidence Grounding via Structured Inline Citation Generation* (FullCite) —
  <https://arxiv.org/html/2606.07130>
- *Measuring and Enhancing Trustworthiness of LLMs in RAG through Grounded Attributions and
  Learning to Refuse* — <https://arxiv.org/pdf/2409.11242>

**Models and packaging (checked against live package metadata, August 2026)**
- `fastembed` 0.8.0 (2026-03-23) dependency metadata — <https://pypi.org/pypi/fastembed/json>
- `chromadb` 1.5.9 dependency metadata — <https://pypi.org/pypi/chromadb/json>
- FastEmbed supported models (dense + cross-encoder rerankers, sizes) —
  <https://qdrant.github.io/fastembed/examples/Supported_Models/>
- EmbeddingGemma — <https://developers.googleblog.com/en/introducing-embeddinggemma/>,
  <https://huggingface.co/blog/embeddinggemma>,
  <https://huggingface.co/onnx-community/embeddinggemma-300m-ONNX>
- Model2Vec / static embeddings (offline last-resort option) —
  <https://github.com/MinishLab/model2vec>
- MTEB benchmark — <https://huggingface.co/papers/2210.07316>

**Evaluation practice**
- RAG retrieval evaluation metrics and golden-set sizing —
  <https://slavadubrov.github.io/blog/2026/05/10/rag-evaluation-metrics/>
- *Benchmarking Information Retrieval Models on Complex Retrieval Tasks* —
  <https://arxiv.org/pdf/2509.07253>

---

## 8. Uncertainties, stated

- **CPU throughput numbers are not measured on our machine.** The ~8 ms/pair cross-encoder
  figure and every embedding-throughput estimate here come from third-party reports. Task 3.0
  must produce our own numbers; do not quote anyone else's on stage.
- **MTEB v1 vs v2 are not comparable.** The 64.6 (3-large) vs 69.67 (EmbeddingGemma) contrast
  is suggestive, not a measured win. If we ever swap embedders, measure on our golden set.
- **The ICSE-SEIP '26 financial-QA paper's detailed result tables were not retrievable** (the
  arXiv abstract page carries only the abstract; the PDF did not extract cleanly). It is cited
  for its framing — parser × chunker interaction, and TableQuest as a table-QA benchmark — not
  for specific numbers.
- **The +6 % in-corpus / −5 to −53 % in-document split for contextualized (late) chunking is
  from one 2026 paper.** It is consistent with Jina's original BEIR results, but it is a single
  replication. Treat the direction as reliable and the magnitude as provisional.
- **Whether our documents behave like "in-corpus" or "in-document" retrieval on the day is
  unknown**, because the problem statement is unknown. If the jury hands us one large document
  and asks questions inside it, several rankings above (LumberChunker, chunk-size sensitivity,
  late chunking's sign) flip. The golden set is what would tell us — which is another argument
  for building it.
