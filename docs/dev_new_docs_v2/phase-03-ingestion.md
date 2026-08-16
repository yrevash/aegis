# Phase 3 — Real ingestion

**3 days. This is the capability that does not exist and that 30 August requires.**

On the morning of the 30th somebody hands us documents. Today there is nothing in Aegis
that can accept them.

---

## What is actually wrong

Not "the ingestion is weak". There is no ingestion.

### 1. There is no PDF path, and no upload surface of any kind

Verified by grep across `aegis/src` and `backend/src`:

- `docling`, `pypdf`, `PyMuPDF`, `pdfplumber`, `unstructured` — **zero hits**, in source and
  in both `pyproject.toml` files.
- `backend/src/app/api/routes.py` (3066 lines, ~60 routes) has **no ingest route, no
  document upload route, no job route**. The only `UploadFile` on the whole surface is
  `POST /voice/transcribe` at `routes.py:2684`.

### 2. The ingest function that exists is never called by the application

```python
# backend/src/app/retrieval/pipeline.py:97
async def ingest(docs: Sequence[object]) -> IngestReport:
    return await _get_retriever().ingest(docs)
```

`grep -rn "\bingest("` across `backend/src` returns exactly that one line and a docstring.
Nothing in `main.py`, no startup hook, no script under `scripts/` calls it.
`load_seed_corpus()` (`backend/src/app/adapter/corpus/__init__.py:66`) is exported through
the adapter surface and is called by **one test**.

So: the pipeline can ingest, and nothing ever asks it to. Whatever is in the LightRAG
working directory on the demo machine got there by hand.

### 3. The chunker is good, and it can only read Markdown

`aegis/src/aegis/retrieval/chunker.py` is real work and it survives this phase intact:

- `chunk_structured` (`:225`) splits on Markdown headings via `_split_sections` (`:138`),
  packs paragraphs → sentences → word windows, and records `section`, `word_start`,
  `word_count`, `ordinal` per chunk.
- `ChunkPiece.contextualized()` (`:110`) prepends `[A > B]` — free contextual retrieval.
- The `word_start` arithmetic at `:265-269` carries a paragraph of commentary explaining why
  it subtracts the overlap. That comment is load-bearing; leave it.
- `dedup_pieces` (`:325`) does exact and 3-shingle-Jaccard near-duplicate removal, scoped
  **within a section path**.
- `Retriever.ingest` (`aegis/src/aegis/retrieval/pipeline.py:388`) already runs
  `validate_content` (`retrieval/validation.py:53`) on every chunk before any write. Content
  poisoning defence exists. Good.

The one thing it cannot do is read a PDF, because `_split_sections` recovers hierarchy from
`#` characters and a PDF has none.

### 4. Nothing survives a restart, and nothing is watchable

`Retriever._seen_hashes` (`pipeline.py:139`) is a process-local `set`. Restart the backend
and every document looks new. There is no job table, no progress, no log.

---

## What we are fixing now, and what waits

| | |
|---|---|
| **Now** | Docling behind a seam, converting PDF/DOCX/PPTX/HTML to a structured intermediate that keeps `#`/`##`/`###` hierarchy. |
| **Now** | `chunk_structured` accepts pre-structured sections instead of re-parsing hashes. |
| **Now** | `documents`, `ingestion_jobs`, `ingestion_events` in Postgres. A guarded claim. A worker that resumes. |
| **Now** | A live SSE log the tenant watches while their document ingests. |
| **Now** | Every write carries the tenant, and a completed ingest bumps `corpus_version` so Phase 1's caches go stale. |
| **Waits** | Per-tenant LightRAG instances and graph namespaces. Phase 1 already decided this waits; ingestion writes through the same filtered path. |
| **Waits** | Cost preflight and the confirm-before-extraction gate. Nice; not 30 August. |
| **Waits** | `DELETE /ingest/documents/{id}` doing a full three-store purge. Ship soft-delete + retrieval filter now; the real purge is a right-to-be-forgotten story for after. |
| **Waits** | Token-based chunk sizing (tiktoken). The word budget is fine. |

### The two things that drop first if we slip

The master plan's cut order names these explicitly. **Mark them in the code review, not on
the 29th.**

- **Table-aware chunking** (task 3.7). Tables become prose. Enterprise PDFs lose their
  tables. That is a real quality loss and it is survivable.
- **Page and bbox provenance** (task 3.8). Citations say "section 4.2" instead of "page 14".
  Also a real loss, also survivable.

Everything from 3.0 to 3.6 is load-bearing. Do not cut into it.

---

## Docling, settled

Plan 03 §2.1 did the evaluation. Carrying the conclusions so nobody re-runs the argument:

- **`docling` 2.120.1**, Python `>=3.10,<4.0`. Repo is 3.11. ✅
- **Windows: supported.** x86_64 and arm64, macOS and Linux too.
- **Docker: not required.** **GPU: not required** — it is an optional accelerator.
- **No pandas conflict.** Docling 2.x core dependencies are `pydantic`, `docling-core`,
  `pydantic-settings`, `filetype`, `requests`, `certifi`, `pluggy`, `tqdm`. No torch, no
  pandas, no numpy in the core. Our `pandas>=2.2,<2.4` cap (there for `nemoguardrails`) is
  untouched.
- Torch lives in the `models-local` extra. **On Windows the default PyPI torch wheel is
  CPU-only** — CUDA needs an explicit `--index-url` — so that extra is ~250 MB of CPU torch,
  not multi-GB of CUDA. Acceptable on 16 GB.

**Install target:**

```
docling[format-pdf-docling,format-office,format-web,models-local,feat-chunking]
```

**Pipeline settings — these are the defaults, override only with a measurement:**

| Setting | Value | Why |
|---|---|---|
| `do_ocr` | `False` | OCR on every page of a 100-page text-native PDF is minutes of CPU for no gain. Auto-enable per page only when the page has no text layer. |
| `do_table_structure` | `True` | |
| `TableFormerMode` | `FAST` | Measured **1.74 s per table on x86 CPU** vs 400 ms on an L4. Docling's own CPU guidance is `FAST` + OCR off for text-native input. |
| `do_picture_classification` | `False` | Extra model, extra seconds per page. |
| `do_code_enrichment` | `False` | Same. |
| `do_formula_enrichment` | `False` | Same. |
| VLM pipeline | **UNDER REVIEW — the claim below was wrong** | The original text here said the VLM pipeline is impossible because it spans "258M–27B params". That conflated two very different things. `granite-docling-258M` / `SmolDocling-256M` are ~256M parameters — roughly 500 MB at fp16, which is *nothing* for a 16 GB machine, and they are purpose-built for modest hardware. Only the large catalog entries (Pixtral-12B, Gemma-3-27B) are genuinely out of reach. The real question is **throughput per page on CPU**, not memory, and it is being measured rather than assumed — see `research/docling-verified.md`. Do not act on this row until that lands. |

Layout model: `docling-layout-heron` (the recommended default, and the only one with an
ONNXRuntime path if torch proves troublesome). Models download once into
`~/.cache/docling/models` — **needs internet at setup time, not at demo time**. The
`ds4sd/docling-models` HF repo is ~358 MB total.

`docling-core[chunking]`'s `HybridChunker` is a **reference**, not a replacement. We port
two of its behaviours into `chunk_structured` (task 3.7) and keep our own chunker, because
ours already owns the overlap-offset correctness, the section-scoped dedup, and the
provenance record the citation surface depends on. Swapping chunkers means re-solving all
three.

---

## Tasks

### 3.0 — The spike, on the actual demo machine (0.25d)

**Do this first and do not skip it.** Every timing claim downstream depends on it.

- Install the extras above; run `docling-tools models download` to pre-populate the cache.
- Convert three representative PDFs: text-native, table-heavy, scanned.
- Record wall clock, peak RSS, and output fidelity for each. Write the numbers down — they
  are what you tell the jury when they ask how long a document takes.
- Check `onnxruntime` version compatibility against whatever the vector store pulls in.

If the spike shows torch is a problem, `models-onnxruntime` is the fallback — but note it
currently only covers the heron layout model, so TableFormer would still need torch. Default
is torch-CPU. Escalate only with evidence.

### 3.1 — The conversion seam (0.5d)

New module `aegis/src/aegis/retrieval/convert.py`.

```python
@dataclass(frozen=True, slots=True)
class ParsedSection:
    heading_path: tuple[str, ...]   # ("Guide", "Refunds") — the # / ## / ### hierarchy
    level: int
    text: str
    page_no: int | None = None
    bbox: tuple[float, float, float, float] | None = None

@dataclass(frozen=True, slots=True)
class ParsedDocument:
    sections: list[ParsedSection]
    tables: list[ParsedTable]
    pages: int
    warnings: list[str]
```

**Docling never leaves this module.** The chunker must not import it, the tests must run
with no Docling installed, and the plain-Markdown path must not touch it at all. This is the
same discipline `LightRAGBackend` already uses for its lazy LightRAG import — follow it.

Declare it as an **optional extra** (`aegis[ingest]`). A missing Docling degrades Markdown
and text upload to full function and fails PDF upload **loudly**, with a message naming the
extra. No silent fallback: a PDF that silently ingests as garbage is worse than a refusal.

The heading hierarchy is the user's explicit requirement. Docling's layout model gives real
nesting levels; map them straight onto `heading_path` and do not re-derive anything from
character counts.

### 3.2 — Teach `chunk_structured` to take structure (0.25d)

`chunk_structured` (`chunker.py:225`) currently calls `_split_sections(body)` at `:255` to
recover `(section, body)` pairs from `#` characters. Add an overload that takes that list
directly:

```python
def chunk_structured(text: str, *, chunk_size=400, overlap=60) -> list[ChunkPiece]: ...
def chunk_sections(sections: Sequence[ParsedSection], *, chunk_size=400, overlap=60) -> list[ChunkPiece]: ...
```

Both feed the same packing loop. **Everything downstream is unchanged** — `_pack_units`, the
`word_start` overlap arithmetic, `contextualized()`, `content_id()`, `dedup_pieces`. This is
the smallest change that unlocks PDFs and it preserves every correctness property the
existing tests pin.

Keep `chunk_structured` as the public name for Markdown. Do not break `Retriever.ingest`.

### 3.3 — The document model and the job tables (0.75d)

Four tables. All carry `tenant_id` and all get an RLS policy — Phase 1 task 1.5 already
commits to covering every tenant-scoped table, so these must land inside that rule, not
beside it.

**`documents`** — `id`, `tenant_id`, `uploaded_by`, `filename`, `content_sha256`, `mime`,
`byte_size`, `page_count`, `visibility`, `status`, `corpus_version_at_ingest`, `created_at`,
`deleted_at`.

**`document_chunks`** — `id`, `document_id`, `tenant_id`, `ordinal`, `section_path`,
`page_no`, `bbox`, `word_start`, `word_count`, `content_hash`, `text`, and a generated
`tsvector` column with a GIN index.

> That `tsvector` is worth a sentence on stage. Plan 03 §1.4 found that
> `LightRAGBackend` does not implement `KeywordBackend`, so the BM25 arm never fires in
> production — the only `keyword_recall` implementation is in the lite backend
> (`aegis/src/aegis/retrieval/memory.py:531`). Postgres full-text search on this column is a
> real third arm for zero new dependencies. **Do not build the retrieval side of it in this
> phase** — just create the column and the index so Phase 5 or the backlog can light it up.

**`ingestion_jobs`** — `id`, `document_id`, `tenant_id`, `stage`, `status`, `progress`,
`attempts`, `claimed_at`, `error`.

**`ingestion_events`** — append-only: `ts`, `job_id`, `tenant_id`, `stage`, `level`,
`message`, `payload jsonb`. **The live log stream is a tail of this table**, which is what
makes a reconnecting browser replay history instead of staring at a blank panel.

Add an `ingestion_ledger` (content_hash → document_id, tenant-scoped) to replace
`Retriever._seen_hashes` (`pipeline.py:139`). A process-local set is not an idempotency
record.

No Alembic (master plan defers it), so these go in the same `DB_BOOTSTRAP` table-creation
path `main.py:148` already uses.

### 3.4 — Upload and the worker (0.5d)

**`POST /ingest/documents`** (multipart). Returns `{document_id, job_id}` immediately.

Copy the shape of `POST /voice/transcribe` (`routes.py:2684-2760`) — it already solved this
exact problem and its docstring explains why multipart beats base64 for a streaming size
check. `python-multipart` is already declared in `backend/pyproject.toml`. Reuse:

- the `read_upload` pattern that abandons the read the moment the cap is passed → 413,
- `_resolve_governance(auth)` + `set_governance_context` so the ingest is budget-enforced and
  ledgered against the tenant like any other spend,
- `_safe_audit` with the filename and byte count, never the content.

MIME allowlist, byte cap, content-hash dedup against `ingestion_ledger`.

**Two-stage commit.** Stage 1 = parse → chunk → validate → embed → vector write. The
document is **answerable** at the end of stage 1. Stage 2 = entity/relation extraction into
the graph; the document becomes **connected**. Report the stages separately. A stage-2
failure degrades the answer rather than losing the document — and it is what makes a live
jury-PDF demo survivable, because stage 1 is fast and deterministic and stage 2 is neither.

**Claim the job with a guarded update.** The pattern already exists and is correct — copy it
verbatim from `aegis/src/aegis/memory/consolidate.py:996-1010`:

```sql
UPDATE ingestion_jobs SET status='running', attempts=attempts+1
 WHERE id=:id AND status='pending'
```

`rowcount == 0` means another worker won. Default concurrency **1**. Bounded retries with
backoff; a poisoned document must not wedge the queue.

### 3.5 — The live log the tenant watches (0.5d)

The user asked to *"see the logs how they are being ingested"*. This is that.

**`GET /ingest/jobs/{id}/events`** — SSE. Replay every row from `ingestion_events` for the
job, then live-tail.

Use `EventSourceResponse` from `sse_starlette`, exactly as `POST /query` does at
`routes.py:901,936`. **Put the events on the `StreamEvent` discriminated union**
(`backend/src/app/api/schemas.py:448`, mirrored in `web/src/lib/stream.ts:334`) — that is the
live console wire. Do **not** build this on `AegisEmitter`/AG-UI: plan 02 §1.2 verified that
primitive serves nothing but tests and one demo route, so anything built there is invisible.

Emit at real boundaries, not on a timer:

```
converted    → "12 pages, 47 sections, 6 tables, 3.1 s"
chunked      → "182 chunks, 9 near-duplicates dropped"
validated    → "180 accepted, 2 rejected (injection signature)"
embedded     → progress, n of 180
vectors      → written, tenant-scoped
graph        → entities/relations added (the measured delta, not a hardcoded zero —
                LightRAGBackend.ingest_chunks already returns the real numbers)
done         → corpus_version bumped to N
```

An unknown event `type` is already safe on the client — `runReducer.ts` ends with
`default: return next` — so backend can land before frontend.

### 3.6 — Scope and cache invalidation (0.25d)

This is where Phase 1 and Phase 3 must actually meet, and it is the task most likely to be
skipped and then discovered on stage.

- Every ingest write takes the `RetrievalScope` from Phase 1 task 1.1. `Retriever.ingest`
  gets the same **required** scope parameter that `retrieve` got. No `None` default.
- Chroma writes: remember Phase 1's finding — **Chroma silently drops `None` metadata
  keys**. A null tenant needs a sentinel, not `None`.
- On a **completed** job (stage 1 is enough), bump the tenant's `corpus_version`. Phase 1
  task 1.2 folds that counter into both cache keys, so the tenant's cached answers become
  unreachable and age out on their own TTL. No eviction, no sweep.

**Test this specific sequence, because it is the demo:** ask a question → get an answer →
upload the PDF that answers it better → ask the same question → get a *different* answer. If
`corpus_version` is not wired, step four returns the cached pre-upload answer and the whole
demo dies in the most embarrassing way available.

### 3.7 — Table-aware chunking (0.25d) — **first thing to cut**

A table becomes its own chunk, serialised as Markdown, never packed into surrounding prose.
Port `HybridChunker`'s two behaviours: repeat the header row when a table spans chunks, omit
the header for a row that would overflow with it. Prepend the caption and the section path.

Also generate a one-line natural-language summary per table for the *embedding*. A Markdown
pipe-table embeds terribly, and this is where "maximise the quality" actually pays on
enterprise PDFs.

### 3.8 — Page and bbox provenance (0.25d) — **second thing to cut**

`ChunkPiece` gains `page_no` and `bbox`. They flow into `Chunk.metadata`, the vector payload,
`Source.metadata`, and out to the console, so a citation can say "page 14".

Adding two optional fields to a frozen dataclass with defaults is cheap. The cost is in
threading them through the write path and the citation surface, which is why it is cut
second rather than first.

---

## Trust: an uploaded document is hostile input

Ingestion is the prompt-injection front door and a jury-supplied PDF is hostile by default.
Two things, both cheap, both required:

1. `validate_content` already runs per chunk before any write
   (`pipeline.py:428-432`) and rejections already surface in `IngestReport.rejections`.
   Surface them in the **live log** too, with the reason. A rejected chunk visible on screen
   is a security demo, not an error.
2. Carry a `trust` label from upload through to `build_spotlighted_context`, so
   tenant-uploaded content is always spotlighted. Extracted document text must never reach
   an extraction *system* prompt unfenced.

---

## Definition of done

- [ ] `POST /ingest/documents` accepts a PDF and returns a job id in under a second.
- [ ] `GET /ingest/jobs/{id}/events` replays history then live-tails, and a browser
      reconnect shows the full log rather than a blank panel.
- [ ] Kill the backend mid-job, restart it, and the job resumes — proven by the guarded
      claim, not by hope.
- [ ] A converted PDF's `#`/`##`/`###` hierarchy is intact in `document_chunks.section_path`,
      checked against a golden fixture.
- [ ] `Retriever.ingest` cannot be called without a `RetrievalScope`.
- [ ] Upload → ask the same question → **different answer**. `corpus_version` proven, twice
      in a row.
- [ ] Tenant A's uploaded document is not retrievable by tenant B — the Phase 1 isolation
      test extended to cover an uploaded document, not just a seeded one.
- [ ] Docling is absent from the environment → Markdown upload still works, PDF upload fails
      with a message naming the extra. No silent fallback.
- [ ] The spike numbers from 3.0 are written down somewhere a human can read them.

## Demo at the end of this phase

Drag a PDF into the console. Watch the log stream: pages converted, sections found, chunks
made, duplicates dropped, one chunk rejected by the content validator with its reason on
screen, vectors written, graph entities added. Then ask a question only that document can
answer, and get it back cited.

Then ask the same question as the other tenant and get nothing.

## Risks

**The Docling model download is a demo-day dependency if you forget it.** The models land in
`~/.cache/docling/models` once and need internet. Pre-populate the cache on the demo machine
during the spike, then verify by ingesting with the network off. Do not discover this on the
30th.

**Three days is a compression of nine.** Plan 03's Phase 2 is 5–6 days and its Phase 3 is
4–5. This phase is those two phases with the cost preflight, the full delete path, token
sizing and per-tenant vector stores taken out. That is the honest accounting. If day two
ends without a working guarded claim, cut 3.7 and 3.8 immediately rather than at the end.

**TableFormer at 1.74 s per table adds up.** A 40-table document is over a minute in table
recovery alone. That is fine for a background job and fatal for a request — which is exactly
why ingestion is a durable job (task 3.4) and not a POST that blocks.

**A scanned PDF with OCR off produces empty sections.** The per-page auto-enable is the
mitigation, but it makes a scanned document minutes long. Detect it, say so in the log
stream, and let the tenant decide. Silence here reads as a bug.

**Nothing has ever ingested in this application.** `Retriever.ingest` has test coverage and
zero production callers. Expect the first real run to surface something the tests never
exercised — a metadata key the vector store rejects, a LightRAG write that needs a warm-up.
Budget for one surprise, and run the first end-to-end ingest on day two, not day three.
