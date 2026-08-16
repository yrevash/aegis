# Docling, verified

> **Method.** Everything below was checked against a primary source: the PyPI JSON API,
> the `docling` source as actually installed, Docling's own documentation and CHANGELOG,
> HuggingFace model cards and the HF file API, or a measurement taken on this machine.
> Where a number is an estimate or could not be verified, it says so in the line.
>
> **Measurements were taken on an Apple M3, 8 cores, 16 GB RAM, macOS, Python 3.11,
> `docling==2.120.1`, CPU-only.** The RAM budget and core count match the target Windows
> laptop; the ISA and OS do not. Every place that matters, the macOS-vs-Windows delta is
> called out.
>
> **Companion document:** `ingestion-sota.md` covers the pipeline *around* the parser.
> This document is only about the parser.

---

## 0. Top line

**Use the standard (layout + TableFormer) pipeline. Do not use the VLM pipeline.**
That is the same conclusion `phase-03-ingestion.md` reached, but almost every fact it used
to get there is wrong, and one of the wrong facts — the one about heading levels — will
break task 3.1 on day two if it is not corrected first.

The four things that actually matter:

1. **Heading hierarchy is OFF by default and PDFs come out flat.** Docling's layout model
   emits `SECTION_HEADER` with **no level**; every heading lands at `level=1`. Measured on a
   9-page PDF: defaults gave `{level 1: 20}` — 20 headings, all flat. Setting
   `heading_hierarchy_options.enabled=True` **and** `generate_parsed_pages=True` gave
   `{1:8, 2:6, 3:4, 4:1, 5:1}` — a real 5-level tree, for +5.6% wall clock. Setting only the
   first flag gave `{1:16, 2:4}`, which is the silent-failure case. Phase 3.1's entire
   premise — "Docling's layout model gives real nesting levels" — is false as written, and
   its definition-of-done test would have failed on day two.
2. **The VLM pipeline is a throughput problem, not a memory problem.** I ran
   granite-docling-258M CPU-only on this 16 GB machine: **281 seconds for one page, at
   2,027 MB peak RSS.** The standard pipeline on the same machine and document: **1.10 s per
   page at 2,199 MB.** So the VLM used **less memory than the standard pipeline** and was
   **255× slower**. Phase 3's "flatly impossible at 16 GB" is not overstated, it is
   backwards — and the real reason to refuse is the 255×. (It also returned its headings
   flat, so it does not rescue item 1 either.)
3. **The install string in phase 3 does not do what it says.** All five extras are invalid
   on the `docling` package and are silently ignored. It happens to work anyway, because
   plain `docling` is now a metapackage for `docling-slim[standard]` which already includes
   everything — including torch, which phase 3 believes is opt-in. Separately, the
   `models-onnxruntime` fallback it names installs **onnxruntime-GPU** on Windows.
4. **`do_ocr=False` was right, but not for the reason given.** Per-stage profiling puts
   **88% of runtime in the OCR stage on a fully born-digital PDF**. Phase 3's proposed
   mitigation — per-page auto-enable — is already implemented upstream at finer granularity
   and does *not* make OCR cheap. Decide per document instead. See §3.3.

**Recommended configuration**, all deltas from the real defaults:

```python
po = PdfPipelineOptions()
po.do_table_structure = True                       # default True
po.table_structure_options.mode = TableFormerMode.FAST   # default is ACCURATE
po.heading_hierarchy_options.enabled = True        # default False  <-- REQUIRED
po.generate_parsed_pages = True                    # default False  <-- required by use_style
po.do_ocr = not pdf_has_text_layer(path)           # default True; see §3.3 — decide PER DOCUMENT
po.accelerator_options = AcceleratorOptions(num_threads=4, device=AcceleratorDevice.CPU)
# do_picture_classification / do_code_enrichment / do_formula_enrichment
# are already False by default. Do not set them; just do not prefetch their models.
```

Install `docling[rapidocr]`, not bare `docling` — on Windows the bare install falls through
to a torch-backend OCR engine, and `docling[onnxruntime]` (phase 3's stated fallback) pulls
**onnxruntime-GPU**. See §4.2.

Prefetch **only** what that configuration uses:

```
docling-tools models download layout tableformer rapidocr     # ~575 MB
```

not the bare `docling-tools models download`, which pulls **1.3 GB** including a 631 MB
code/formula model the configuration above never loads.

---

## 1. What Docling actually ships and downloads

### 1.1 The package is now a metapackage

`docling` 2.120.1 (released **2026-08-14**, three days before this was written) has exactly
one dependency:

```
docling-slim[standard]==2.120.1
```

*Source:* [PyPI `docling` JSON](https://pypi.org/pypi/docling/json). The split landed in
**v2.92.0** — "Introduce modular docling-slim package"
([CHANGELOG](https://github.com/docling-project/docling/blob/main/CHANGELOG.md)).

This matters for two reasons.

**The extras moved.** `docling`'s own extras are only:
`asr, easyocr, htmlrender, ocrmac, onnxruntime, rapidocr, remote-serving, tesserocr, vlm, xbrl`.
The granular `format-*` / `models-*` / `feat-*` extras belong to **`docling-slim`**.

Verified by resolving the exact string phase 3 specifies:

```
$ uv pip compile 'docling[format-pdf-docling,format-office,format-web,models-local,feat-chunking]==2.120.1'
warning: The package `docling==2.120.1` does not have an extra named `format-web`
warning: The package `docling==2.120.1` does not have an extra named `format-office`
warning: The package `docling==2.120.1` does not have an extra named `models-local`
warning: The package `docling==2.120.1` does not have an extra named `feat-chunking`
warning: The package `docling==2.120.1` does not have an extra named `format-pdf-docling`
```

The resolved lockfile is **byte-identical** to plain `docling==2.120.1`. A control run with
a deliberately fake extra (`docling[this-extra-does-not-exist-at-all]`) produced the same
329-line lock. The extras are no-ops.

**`standard` already contains everything phase 3 was reaching for** — `docling-parse`,
`pypdfium2`, `python-docx`, `python-pptx`, `openpyxl`, `beautifulsoup4`, `marko`,
`docling-core[chunking]`, `docling-ibm-models`, **`torch`**, `torchvision`, `accelerate`,
`rapidocr`. So `pip install docling` is correct and sufficient. The install string is
wrong; the outcome is right by accident.

### 1.2 Models: what is downloaded, and how big

Nothing is bundled in the wheel. PDF conversion downloads model weights from HuggingFace on
first use into **`~/.cache/docling/models`** (Windows: `%USERPROFILE%\.cache\docling\models`).
Non-PDF formats (docx, pptx, html, md) need **no** models at all — that is what makes the
"Docling absent → Markdown still works" degradation in phase 3.1 cheap.

Prefetch with `docling-tools models download`. **Measured on disk** after running it, with
parameter counts read directly out of the safetensors headers:

| Model | Repo | Params | On disk | dtype | CPU viable? |
|---|---|---|---|---:|---|
| **Layout (default)** | `docling-project/docling-layout-heron` | **42.9 M** | **171.7 MB** | F32 | **Yes.** RT-DETRv2. This is the workhorse. |
| Layout, ONNX build | `…docling-layout-heron-onnx` | — | 163 MB | ONNX | Yes — but see §4.2 before reaching for `docling[onnxruntime]` on Windows. |
| **TableFormer ACCURATE** | `docling-project/docling-models` | **53.2 M** | **212.8 MB** | F32 | Yes, but ~0.8 s/table slower than FAST (measured). |
| **TableFormer FAST** | `docling-project/docling-models` | **36.4 M** | **145.5 MB** | F32 | **Yes. Recommended.** |
| Picture classifier | `…DocumentFigureClassifier-v2.5` | 4.1 M | 16.4 MB | F32 | Yes, but off by default and not needed. |
| Code/formula enrichment | `docling-project/CodeFormulaV2` | **315.5 M** | **631.0 MB** | BF16 | Runs, but it is a generative VLM — same throughput objection as §2. Off by default. **Do not prefetch.** |
| RapidOCR (PP-OCRv6) | bundled checkpoints | — | 61 MB | ONNX/torch | Yes. Default OCR engine on Windows. |
| **Default prefetch total** | | | **1.3 GB** | | |
| **What we actually need** | `layout` + `tableformer` + `rapidocr` | | **~575 MB** | | |

`docling-tools models download` takes explicit model names — verified working — which is how
you avoid the 631 MB you do not want:

```
layout | tableformer | tableformerv2 | code_formula | picture_classifier | smolvlm |
granitedocling | granitedocling_mlx | smoldocling | smoldocling_mlx | granite_vision |
granite_chart_extraction | granite_chart_extraction_v4 | rapidocr | easyocr | nemotron_ocr_v2
```

Flags: `--output-dir/-o`, `--all`, `--force`, `--quiet`, `--easyocr-lang`,
`--rapidocr-backend-lang`.

**Offline is genuinely supported.** Point the pipeline at the cache with
`PdfPipelineOptions(artifacts_path=...)`, the `DOCLING_ARTIFACTS_PATH` environment variable,
or the `--artifacts-path` CLI flag. Docling's FAQ states it plainly: *"Docling is not using
any remote service, hence it can run in completely isolated air-gapped environments."*
([FAQ](https://docling-project.github.io/docling/faq/),
[advanced options](https://docling-project.github.io/docling/usage/advanced_options/)).

### 1.3 OCR engines

`OcrAutoOptions` is the default, and it probes at pipeline-init time. Selection order, read
from `docling/models/stages/ocr/auto_ocr_model.py`:

1. `ocrmac` — **darwin only**, and not installed by `standard` (it is in the `ocrmac` extra).
2. `nemotron` — **linux only**, and its dependency is gated to `python==3.12 and linux and x86_64`.
3. **`rapidocr` + `onnxruntime`** — requires an `onnxruntime` import to succeed.
4. `easyocr` — requires the `easyocr` extra.
5. **`rapidocr` + `torch`** — the fallback that always fires, because `standard` ships `rapidocr` and `torch`.

**On Windows with a plain `docling` install you land on step 5: RapidOCR on the torch
backend.** That works but is the slow path — the 33.46 s OCR figure in §3.3 is this backend.
`pip install "docling[rapidocr]"` adds CPU `onnxruntime` and promotes you to step 3.

**Reach step 3 via `docling[rapidocr]`, not `docling[onnxruntime]`** — the latter installs
`onnxruntime-gpu` on Windows (§4.2). This is a free win and phase 3 does not mention it.

| Engine | Install as | Size | Notes |
|---|---|---|---|
| **RapidOCR (PP-OCRv6)** | **`docling[rapidocr]`** | **61 MB** (measured) | Default on Windows/Linux without GPU. **Recommended.** |
| EasyOCR | `docling[easyocr]` | not measured; pulls `scikit-image` | 80+ languages; the historical default; heavier. |
| Tesseract | `docling[tesserocr]` | needs a system Tesseract | Also drags in **pandas** as a direct dependency. |
| macOS Vision | `docling[ocrmac]` | 0 (OS) | darwin only — irrelevant to the target machine. |
| Nemotron OCR v2 | `docling-slim[feat-ocr-nemotron]` | not measured | linux + py3.12 + x86_64 only. Not available to us. |

---

## 2. The VLM pipeline, assessed fairly

### 2.1 The memory objection was wrong

The user's pushback is correct and phase 3 should be corrected on this point.

| Model | Params | Weights on disk | Source |
|---|---|---:|---|
| `ibm-granite/granite-docling-258M` | 0.3 B | **515.1 MB** (BF16) | [HF file API](https://huggingface.co/ibm-granite/granite-docling-258M) |
| `ds4sd/SmolDocling-256M-preview` | 0.3 B | **513.0 MB** (BF16) | [HF](https://huggingface.co/ds4sd/SmolDocling-256M-preview) |
| `lightonai/LightOnOCR-2-1B` | 1 B | 2,011 MB | HF file API |

*(SmolDocling's HF repo also ships quantised ONNX builds down to ~132 MB — but **Docling
cannot use them**. `InferenceFramework` is `MLX | TRANSFORMERS | VLLM`; there is no ONNX
backend for the VLM pipeline. Do not plan around those files.)*

Granite-Docling-258M is Idefics3: a `siglip2-base-patch16-512` vision encoder, an Idefics3
pixel-shuffle connector, and a **Granite 165M** language backbone. Half a gigabyte of
weights.

I did not have to estimate the runtime footprint — I measured it (§2.2a):
**2,027 MB peak RSS**, against **2,199 MB** for the standard pipeline on the same document.
**The VLM pipeline used less memory than the pipeline we are shipping.** "Impossible at
16 GB" should be struck from the phase file, not softened.

### 2.2 The real objection is decode throughput

DocTags output is generated **autoregressively**, and the `GRANITEDOCLING_TRANSFORMERS`
spec sets **`max_new_tokens=8192`** with the page rendered at `scale=2.0`. A whole page of
structured markup is decoded one token at a time. That is the cost, and it does not shrink
because the model is small — a 258M model still has to run a forward pass per output token.
(The spec does list `AcceleratorDevice.CPU` in `supported_devices`, so this is a supported
configuration, just a slow one.) Docling publishes per-page inference times on a
**MacBook M3 Max**
([vision models](https://docling-project.github.io/docling/usage/vision_models/)):

| Preset | Backend | Device | Seconds/page |
|---|---|---|---:|
| `SMOLDOCLING_MLX` | MLX | Apple GPU | 6.15 |
| `QWEN25_VL_3B_MLX` | MLX | Apple GPU | 23.5 |
| **`SMOLDOCLING_TRANSFORMERS`** | **transformers** | **MPS** | **102.2** |
| `GRANITE_VISION_TRANSFORMERS` | transformers | MPS | 104.75 |
| `PIXTRAL_12B_MLX` | MLX | Apple GPU | 308.9 |
| `GEMMA3_12B_MLX` | MLX | Apple GPU | 378.5 |
| **`PHI4_TRANSFORMERS`** | **transformers** | **CPU** | **1,175.7** |
| **`PIXTRAL_12B_TRANSFORMERS`** | **transformers** | **CPU** | **1,828.2** |

Read the two backends against each other, and note that **on Windows CPU there is only one
backend**. `InferenceFramework` offers `MLX | TRANSFORMERS | VLLM`: MLX is Apple-Silicon-GPU
only, vLLM needs a CUDA server, so **transformers is the only reachable option** — the slow
column. Even *with MPS acceleration* SmolDocling costs 102 s/page there.

### 2.2a Measured: granite-docling-258M, CPU-only, on this 16 GB machine

Rather than extrapolate, I ran it. `GRANITEDOCLING_TRANSFORMERS`, `AcceleratorDevice.CPU`
forced (no MPS), 4 threads, one page of the same born-digital PDF:

```json
{ "spec": "GRANITEDOCLING_TRANSFORMERS", "device": "cpu", "threads": 4,
  "pages": 1, "wall_s": 281.0, "sec_per_page": 281.0, "pages_per_sec": 0.0036,
  "peak_rss_mb": 2026.7, "heading_levels": {"1": 3} }
```

**281 seconds — 4.7 minutes — for a single page. Peak RSS 2,026.7 MB.**

Set that against the standard pipeline on the identical machine and document:

| | Standard pipeline | granite-docling-258M (CPU) | Ratio |
|---|---:|---:|---:|
| **Seconds per page** | **1.10** | **281.0** | **255× slower** |
| **Peak RSS** | 2,199 MB | **2,027 MB** | **0.9× — *less* memory** |

This settles the argument in both directions at once. **The VLM pipeline used *less* memory
than the standard pipeline** — the "impossible at 16 GB" claim is not merely overstated, it
is backwards. And it is **255× slower**, which is why the answer is still no.

A 12-page jury PDF: **13 seconds** standard, **56 minutes** VLM.

One more thing the run showed: `heading_levels: {1: 3}` — the VLM path returned its headings
**flat too**. It does not solve C1 either.

*(macOS arm64. A Windows x86 laptop would differ, but not by two orders of magnitude.)*

### 2.3 Where the genuine cutoff is

Not where phase 3 puts it.

- **Memory** is not the binding constraint until ~7B at BF16 (~14 GB), and with the OS and
  Postgres and Neo4j resident, ~3B is the practical ceiling on a 16 GB box.
- **Throughput** binds far earlier — at essentially *any* generative VLM on CPU. Even the
  smallest 256M model is 100× too slow.
- Several presets phase 3 might reach for are **not loadable on Windows at all**:
  `GEMMA3_27B_MLX`, `GEMMA3_12B_MLX`, `PIXTRAL_12B_MLX`, `QWEN25_VL_3B_MLX`,
  `NANONETS_OCR2_MLX`, `SMOLDOCLING_MLX`, `GRANITEDOCLING_MLX` are MLX presets, and MLX is
  Apple Silicon only. Phase 3's "Gemma-3-27B" example is real but is an MLX entry, so on
  Windows it fails at import, not at allocation.

### 2.4 Quality, for completeness

Granite-Docling-258M superseded SmolDocling-256M and beats it everywhere
([model card](https://huggingface.co/ibm-granite/granite-docling-258M)):

| Task | Metric | SmolDocling-256M | Granite-Docling-258M |
|---|---|---:|---:|
| Layout | F1 | 0.85 | **0.86** |
| OCR | F1 | 0.80 | **0.84** |
| Code recognition | F1 | 0.915 | **0.988** |
| Equation recognition | F1 | 0.947 | **0.968** |
| **Table recognition** | **TEDS w/content** | **0.76** | **0.96** |
| OCRBench | score | 338 | **500** |

If the VLM pipeline is ever revisited, **granite-docling-258M, never SmolDocling** — the
table gap alone (0.76 → 0.96) settles it. Both are English-primary; granite lists Japanese,
Arabic and Chinese as *experimental*.

### 2.5 Recommendation

**Standard pipeline. Not the VLM pipeline.** The reasoning, in one line for the phase file:

> Measured on a 16 GB CPU-only machine: granite-docling-258M took **281 s/page at 2,027 MB
> peak RSS**; the standard pipeline took **1.10 s/page at 2,199 MB** on the same document.
> The VLM used *less* memory and was **255× slower**. Memory was never the objection.
> Throughput is, and it is not close.

Two caveats worth writing down rather than discovering later:

- If the jury hands us a **scanned or photographed** document, the VLM pipeline is
  genuinely better at it than layout+OCR. The mitigation is not the VLM pipeline; it is the
  per-document OCR probe in §3.3 — detect the missing text layer, turn OCR on for that
  document, and say so in the log.
- If a GPU box or an Ollama/vLLM endpoint ever becomes available, `GRANITEDOCLING_OLLAMA` /
  `GRANITEDOCLING_VLLM_API` are one-line swaps. Keep the conversion seam (task 3.1) clean
  enough that this stays a one-liner. That is an argument *for* the seam, not against it.

---

## 3. Real-world quality and performance

### 3.1 Measured, on this machine

Apple M3 / 8 cores / 16 GB / macOS / py3.11 / `docling==2.120.1` / CPU-only.
Document: the Docling technical report PDF — **9 pages, 3 tables**, born-digital.
Each configuration converted the same document 3× in one process; "cold" is run 1
(model load + `torch.compile`), "warm" is the mean of runs 2–3.

| Threads | OCR | TableFormer | Cold (s) | Warm (s) | **s/page** | **pages/s** | Peak RSS |
|---:|---|---|---:|---:|---:|---:|---:|
| 4 | off | ACCURATE | 49.7 | 12.29 | **1.37** | **0.73** | 2,051 MB |
| 8 | off | ACCURATE | 118.6 | 12.70 | 1.41 | 0.71 | 2,118 MB |
| 4 | off | **FAST** | 60.6 | **9.91** | **1.10** | **0.91** | 2,199 MB |
| 4 | **on** | ACCURATE | 127.1 | 53.78 | 5.98 | 0.17 | 2,299 MB |
| 8 | **on** | ACCURATE | 75.1 | 41.42 | 4.60 | 0.22 | **3,283 MB** |

Four conclusions:

- **Peak RSS 2.0–3.3 GB.** Comfortable on 16 GB alongside Postgres, Neo4j and Memurai.
  Memory was never the risk in this phase. The high end is the OCR-on, 8-thread case.
- **`num_threads` only helps when OCR is on.** With OCR off, 4 → 8 threads changed nothing
  (12.29 s → 12.70 s) and made cold start 2.4× worse. With OCR on, 4 → 8 threads saved 23%
  (53.78 s → 41.42 s) at the cost of 1 GB more RSS. Docling's default of 4 is right for the
  configuration we are shipping; do not "tune" it up.
- **TableFormer FAST saved 2.4 s across 3 tables ≈ 0.8 s/table.** Real, worth taking, and
  smaller than the 1.74 s/table phase 3 quotes — that figure is a *delta on x86*, not a
  per-table absolute, and should not be extrapolated to "a 40-table document is over a
  minute in table recovery alone" without re-measuring.
- **OCR on costs 4.4×** — 12.29 s → 53.78 s — on a document that is *entirely born-digital*,
  and per-stage profiling attributes **88% of the runtime to the OCR stage alone**. This
  vindicates phase 3's `do_ocr=False`, for a reason phase 3 did not give. See §3.3.

**Cold start is 50–120 s and is paid once per process.** This is the single most
demo-relevant number in this document and phase 3 does not mention it at all. The ingestion
worker must **warm the converter at startup**, not on the first upload, or the jury watches
a 60-second stall on a document that afterwards takes 12 seconds.

### 3.2 Docling's published CPU numbers

From [the GPU page](https://docling-project.github.io/docling/usage/gpu/), standard pipeline,
no OCR, on an AMD Ryzen 7 9800 (16 vCPU, 128 GB), **16 PyTorch threads**:

| Configuration | PDF doc | ViDoRe V3 HR |
|---|---:|---:|
| **CPU-only** | **1.5 pages/s** | **1.2 pages/s** |
| RTX 5070 | 4.2 pages/s | — |
| RTX 5090 | 7.9 pages/s | — |
| RTX 5090, OCR on | 1.6 pages/s | — |

My 0.73–0.91 pages/s on a 4-thread laptop is the same order of magnitude as their
1.2–1.5 pages/s on a 16-thread desktop. Both numbers are consistent; use mine for planning,
because it is the laptop-shaped one.

Note the GPU-with-OCR row: **turning OCR on costs more than turning the GPU off.** That is
worth knowing before §3.3.

### 3.3 The OCR default is not what phase 3 thinks — but phase 3's setting is still right

Dumped from the installed `PdfPipelineOptions()`:

```
do_ocr        = True                      # NOT False
ocr_options   = OcrAutoOptions(mode=OcrMode.DEFAULT)
```

And from `docling/datamodel/pipeline_options.py:95-110`, verbatim:

```python
class OcrMode(str, Enum):
    FULL_PAGE = "full_page"                       # Force OCR to work on the full page
    LAYOUT_REGIONS = "layout_regions"             # Layout detections only.
    PDF_AWARE_LAYOUT_REGIONS = "pdf_aware_layout_regions"
                # Eliminate those clusters that contain exclusively text PDF cells
    DEFAULT = "default"
                # Currently DEFAULT is wired to run PDF_AWARE_LAYOUT_REGIONS
```

**So Docling already does the per-region auto-OCR that phase 3 proposes to build.** The
"auto-enable per page only when the page has no text layer" work in the phase 3 table is
upstream behaviour, at region granularity, already.

**But it does not make OCR free on a born-digital PDF, and I initially assumed it would.**
Per-stage profile of the same 9-page born-digital document, TableFormer FAST, 4 threads,
via `settings.debug.profile_pipeline_timings`:

| Stage | OCR off | OCR on |
|---|---:|---:|
| **`ocr`** | — | **33.46 s** |
| `layout` | 5.73 s | 6.97 s |
| `table_structure` | 3.86 s | 2.93 s |
| `page_parse` | 1.51 s | 1.69 s |
| `layout_postprocess` | 0.16 s | 0.19 s |
| **`pipeline_total`** | **7.64 s** | **38.09 s** |

*(Stages run concurrently, so they sum to more than the total.)*

**OCR is 88% of wall clock — 3.7 s/page — on a document that contains no scanned text at
all.** The reason is that `PDF_AWARE_LAYOUT_REGIONS` eliminates clusters *made of text PDF
cells*; **figure and picture regions contain no text cells, so they are OCR'd anyway.** A
born-digital paper full of diagrams pays nearly the full OCR bill for nothing.

**Conclusion: phase 3's `do_ocr = False` is the right setting. Its stated reason and its
proposed mitigation are both wrong.** Do not "fix" it to `True` — I nearly did, off the
`OcrMode` source alone, before measuring.

The better answer is neither fixed value. Decide **per document, once, at parse time**:

```python
# pypdfium2 is already a dependency — this costs milliseconds, not seconds.
has_text_layer = any(page_has_extractable_text(p) for p in doc_sample_pages)
po.do_ocr = not has_text_layer
```

- **Text-native →** `do_ocr=False`. Measured **1.1 s/page**.
- **Scanned / no text layer →** `do_ocr=True`. Measured **4.6–6.0 s/page**, and it is the
  only way to get any content at all.

Emit which branch was taken into the SSE log that task 3.5 is already building — "no text
layer detected, OCR enabled, this will take ~5 s/page" is exactly the kind of line that
reads as competence rather than as a hang.

Either way, ship **`docling[rapidocr]`** so the auto-selector reaches the onnxruntime
backend instead of falling through to the torch one (§1.3). I did not isolate that
speedup — the 33.46 s above is the torch backend, which is what a plain install gives you.

### 3.4 Accuracy, published and third-party

- **TableFormer: 93.6% TEDS on all tables**, self-reported on the model card
  ([`ds4sd/docling-models`](https://huggingface.co/ds4sd/docling-models)).
- **Layout heron-101: 78% mAP, 28 ms/image on an A100**, and 20.6–23.9% mAP over the
  previous Docling baseline ([arXiv:2509.11720](https://arxiv.org/abs/2509.11720)). Note
  that heron-101 is *not* our default; `docling-layout-heron` (42.9 M) is the smaller
  balanced one. I could not find a published mAP for the default heron specifically.
- Third-party benchmarks put Docling around **97.9% on table boundary/structure** and
  behind Marker on multi-column reading order (~96.1% for Marker). These are blog
  benchmarks on undisclosed corpora — treat as directional, not as evidence.

### 3.5 Known weaknesses — all confirmed from the issue tracker

| Weakness | Evidence |
|---|---|
| **Multi-column** flattening: text flows left-to-right across column boundaries, merging sentences from different columns | [issue #2067](https://github.com/docling-project/docling/issues/2067) |
| **Rotated scans**: pages rotated via PDF *metadata* rather than physically are not handled by `get_bitmap_rects` in the pypdfium2 backend | [issue #2038](https://github.com/docling-project/docling/issues/2038) |
| **Forms vs tables**: in scanned PDFs a table is sometimes classified `Form`, so it becomes a `group` rather than a `table` object | [issue #3454](https://github.com/docling-project/docling/issues/3454) |
| **Scanned OCR quality**: paragraphs degrading into "meaningless letters" | [issue #3569](https://github.com/docling-project/docling/issues/3569) |
| **Nested / multi-span tables**: structural preservation is weakest here | third-party benchmarks |
| **Text styles** (bold/underline) are unsupported for PDF — declarative backends only | [FAQ](https://docling-project.github.io/docling/faq/) |
| **Non-English**: layout is language-agnostic, but OCR quality is engine-dependent; RapidOCR runs **one language per invocation** | `RapidOcrOptions` docstring |

The multi-column one is the relevant risk for us: an academic-paper-shaped jury PDF is
exactly the failure case, and it degrades silently into scrambled chunks rather than
erroring. Worth one golden fixture.

---

## 4. Installation reality

### 4.1 What actually installed

`uv venv --python 3.11 && uv pip install docling==2.120.1`, clean venv, macOS arm64:

- **105 packages, 1.0 GB** of site-packages.
- Largest: `torch` 475 MB · `cv2` (opencv-python 5.0.0.93) 120 MB · `scipy` 73 MB ·
  `transformers` 46 MB · `pandas` 40 MB · `rapidocr` 31 MB · `docling_parse` 30 MB ·
  `sympy` 29 MB · `numpy` 22 MB · `lxml` 19 MB.
- Resolved versions: `torch 2.13.0`, `transformers 5.8.1`, `numpy 2.4.6`, `pandas 3.0.5`,
  `docling-core 2.91.0`, `docling-ibm-models 3.14.0`, `docling-parse 7.13.0`.

**Yes, it pulls torch, unconditionally.** Not via an opt-in extra —
`docling-slim[standard]` lists `torch<3.0.0,>=2.2.2` and `torchvision<1,>=0` directly.

### 4.2 Windows deltas

- **PyPI's `torch` on Windows is CPU-only. Phase 3 is correct on this.** Verified: every
  `nvidia-*` and `triton` dependency of `torch` 2.13.0 is gated
  `platform_system == "Linux"`. The `win_amd64` cp311 wheel is a **122 MB download**
  (macOS arm64 is 111 MB and unpacks to 475 MB, so budget ~450–500 MB on disk).
  Expect a total install in the same **~1.0–1.2 GB** band as measured here.
- **`docling[onnxruntime]` installs `onnxruntime-GPU` on Windows.** This is a trap and it
  is the exact fallback phase 3 names. Resolved with
  `--python-platform x86_64-pc-windows-msvc`:

  | Install target | What lands on Windows |
  |---|---|
  | `docling[onnxruntime]` | **`onnxruntime-gpu==1.23.2`** — wants CUDA, useless here |
  | `docling[rapidocr]` | `onnxruntime==1.28.0` — the CPU build, correct |

  The `models-onnxruntime` extra declares `onnxruntime-gpu<1.24` under
  `sys_platform == "linux" or sys_platform == "win32"`, and plain `onnxruntime<1.24` only
  under `sys_platform == "darwin"`. **Use `docling[rapidocr]`.**
- **A different `transformers` resolves on Windows than on macOS.** `docling-slim` caps
  `transformers<5.9.0` on darwin but `<6.0.0` everywhere else. Measured here: **5.8.1** on
  macOS, **5.15.0** on Windows. Anything I measured on macOS ran against a different
  transformers than the demo machine will. Pin it in the lockfile.
- **Clean Windows resolution, with the repo's own pins applied**
  (`docling[rapidocr]==2.120.1` + `pandas>=2.2,<2.4` + `numpy>=1.26,<2.5`, py3.11,
  x86_64-pc-windows-msvc): **110 packages**, `pandas==2.3.3`, `numpy==2.4.6`,
  `torch==2.13.0`, `onnxruntime==1.28.0`, `opencv-python==5.0.0.93`, **no `nvidia-*`, no
  `triton`**. No conflicts.
- `ocrmac` and MLX are darwin-only and simply will not be selected.
- Nemotron OCR is linux+py3.12+x86_64-only and will not be selected.
- Docling's FAQ notes **WMF images inside DOCX/PPTX are only processable on Windows** —
  which is in our favour.
- Docling states Windows x86_64 and arm64 are supported
  ([README](https://github.com/docling-project/docling)). MIT licensed.

### 4.3 The `pandas<2.4` question — phase 3's reasoning is wrong, its conclusion survives

Phase 3 says: *"Docling 2.x core dependencies are pydantic, docling-core, pydantic-settings,
filetype, requests, certifi, pluggy, tqdm. No torch, no pandas, no numpy in the core."*

That is the dependency list of **bare `docling-slim`**, not of `docling`. And even bare
`docling-slim` depends on `docling-core`, which declares **`pandas<4.0.0,>=2.1.4` as a hard,
non-optional dependency** ([PyPI `docling-core`](https://pypi.org/pypi/docling-core/json)).
Pandas is unavoidable.

Measured consequence: **an unconstrained `pip install docling` resolves `pandas==3.0.5`**,
which violates the `pandas>=2.2,<2.4` cap that `nemoguardrails` forces.

Re-resolved with both constraints together:

```
docling==2.120.1
pandas>=2.2,<2.4
```

→ resolves cleanly to **`pandas==2.3.3`**, `numpy==2.4.6`, `torch==2.13.0`. **No conflict.**

So the conclusion holds — but only because the repo already pins pandas. If Docling is
added to a `pyproject.toml` that does not carry the cap, or is installed ad hoc into a
fresh venv during the 3.0 spike, pandas 3.0.5 lands and `nemoguardrails` breaks. Say
"compatible **because we pin it**", not "no pandas in the core".

Other pins: `numpy` resolves to 2.4.6, inside the repo's `numpy>=1.26,<2.5`. ✅
Python 3.11 is inside `>=3.10,<4.0`. ✅ `transformers 5.8.1` is new and unconstrained by
anything else in the repo — worth a lockfile pin so a rebuild does not drift.

### 4.4 Two API notes for task 3.1

- **`LayoutOptions` and the `DOCLING_LAYOUT_*` constants are deprecated** and emit a
  `DeprecationWarning` on construction. The current API is
  `LayoutObjectDetectionOptions.from_preset("layout_heron_default")`. Presets:
  `layout_heron_default` (the default), `layout_heron_101`, `layout_egret_medium`,
  `layout_egret_large`, `layout_egret_xlarge`.
- `docling` ships a **plugin system** (`allow_external_plugins=False` by default). Leave it
  false — an uploaded document must not be able to reach a plugin path.
- **`docling-core[chunking]` is already installed** by plain `docling` (verified: `semchunk`,
  `tree-sitter`, `tree-sitter-{python,c,javascript,typescript}` all present in the measured
  venv). Task 3.7 can read `HybridChunker` as a reference with no extra install, exactly as
  phase 3 intends.

---

## 5. The alternatives

**Marker** (`datalab-to/marker`). Code is Apache-2.0 but the **model weights are a modified
AI Pubs Open RAIL-M: free only for research, personal use, and organisations under $5 M
funding/revenue**. It leads on reading order — Datalab's own olmocr-bench numbers are 76.0%
overall / 2.9 pg/s on a GPU. The CPU story is a trap: `fast --disable_ocr` hits 23.7 pg/s on
CPU but scores **43.6%**, because CPU speed comes from switching the models off. Fast and
accurate needs a GPU. The licence alone should end the conversation for a competition
entry; the CPU quality cliff ends it twice.

**MinerU** (`opendatalab/MinerU`). Relicensed from AGPL-3.0 to the Apache-2.0-based "MinerU
Open Source License" in 3.1.0 (April 2026). Pure-CPU is supported but **only via the
`pipeline` backend**, and its own README states requirements of **"RAM: Min 16GB,
Recommended 32GB or more"** and **"Disk: Min 20GB"** — on a 16 GB laptop already hosting
Postgres, Neo4j and Memurai, that is at the floor, not comfortably inside it. Windows
supports Python 3.10–3.12 only (`ray` has no Windows 3.13 wheel), so 3.11 is fine. The
CPU-reachable `pipeline` backend scores **86.47 on OmniDocBench v1.6** against **95.39 for
the hybrid backend**, which needs 8 GB of VRAM — so the accuracy people cite for MinerU is
not the accuracy we could have. Genuinely the best of these at CJK and dense Asian layouts.
Buys nothing we need, costs a second heavy ML stack in the same venv.

**unstructured** (`Unstructured-IO/unstructured`). `unstructured[local-inference]` +
`hi_res` gets a layout model, but the output is a flat list of typed elements rather than a
document tree with real nesting, which is precisely the thing task 3.1 needs. Heavy
transitive tree, and the good parts increasingly live behind their hosted platform. It
would be a downgrade.

**PyMuPDF4LLM** (Artifex). Very fast (PyMuPDF base does ~180 pages/s on text extraction),
tiny, no models, no torch — genuinely attractive for born-digital PDFs. Two disqualifiers:
it is **AGPL-3.0**, which is a real problem for a demoed product, and its heading detection
is font-size heuristics rather than a layout model, so it fails on exactly the documents
where structure matters. Worth remembering as an emergency fallback if Docling will not
install on the demo machine, with the licence flagged.

**Docling's own non-inline options.** `models-remote` (Triton/KServe), `service-client`
(docling-serve over HTTP), `remote-serving`, and the VLM-over-API presets
(`GRANITEDOCLING_VLLM_API`, `GRANITEDOCLING_OLLAMA`). All of them move the compute to a
server. **No Docker and one laptop means all of them are out**, but they are the reason to
keep task 3.1's seam clean: if a GPU appears, this is a config change, not a rewrite.

| | Licence | CPU-only? | Windows, no Docker | Document tree | Verdict |
|---|---|---|---|---|---|
| **Docling** | **MIT** + Apache-2.0 weights | **Yes**, 0.7–0.9 pg/s | **Yes** | **Yes** (`DoclingDocument`, page + bbox) | **Use this** |
| Marker | Apache-2.0 code, **RAIL-M weights, <$5M only** | Only by disabling the models (43.6%) | Yes | Yes | Licence + CPU cliff |
| MinerU | MinerU OSL (Apache-based) | Yes, `pipeline` only, 86.47 vs 95.39 | Yes, py≤3.12 | Yes | 16 GB floor, no gain |
| unstructured | Apache-2.0 | Yes | Yes | **No** — flat element list | Downgrade |
| PyMuPDF4LLM | **AGPL-3.0** | Yes, very fast | Yes | Heuristic only | Licence; emergency only |
| docling-serve / vLLM / Ollama | MIT | n/a | **No** — needs a server | Yes | Out; keep the seam for later |

**Recommendation: Docling, standard pipeline.** MIT-licensed end to end, runs CPU-only,
installs on Windows without Docker, air-gappable, and it is the only one of these that
emits a real document tree with page and bbox provenance — which is what tasks 3.7 and 3.8
are built on. Nothing here beats it under our constraints.

---

## 6. Corrections to `phase-03-ingestion.md`

Ordered by how much damage each one does if left in.

### C1 — "Docling's layout model gives real nesting levels" is FALSE. *(§3.1, blocking)*

Phase 3.1 says:

> "The heading hierarchy is the user's explicit requirement. Docling's layout model gives
> real nesting levels; map them straight onto `heading_path` and do not re-derive anything
> from character counts."

The layout model gives **no** levels. Verbatim from
`docling/datamodel/pipeline_options.py:1797-1829`:

> *"The layout model only flags regions as `SECTION_HEADER` without a level, so every
> heading produced by the PDF path defaults to `level=1` and the document hierarchy is
> flattened. When `enabled`, `HeadingHierarchyModel` runs right after the reading-order
> model and assigns `SectionHeaderItem.level` from (in precedence order) PDF bookmarks/ToC,
> numbering and font style."*
>
> `enabled` … *"When disabled (default), all detected headings remain at level 1 (unchanged
> behavior)."* → **`= False`**

Measured on the same 9-page PDF, three configurations. **Both flags are required; neither
alone is enough:**

| Configuration | Heading levels produced | Markdown | Warm |
|---|---|---|---:|
| Defaults | `{1: 20}` — **completely flat** | 20 × `##` | — |
| `heading_hierarchy.enabled=True` only | `{1:16, 2:4}` — **still nearly flat** | 16 × `##`, 4 × `###` | 6.29 s |
| `heading_hierarchy.enabled=True` **+ `generate_parsed_pages=True`** | `{1:8, 2:6, 3:4, 4:1, 5:1}` — **real 5-level tree** | 8 × `##`, 6 × `###`, 4 × `####` | 6.64 s |

**Fix:** set both flags. `generate_parsed_pages=True` is not optional and its omission fails
*quietly* — the docstring says *"`use_style` requires the parsed PDF cells to still be
available … Without them, style inference is silently skipped (numbering still applies)"*.
The middle row above is what that silent skip looks like: 16 of 20 headings stuck at level 1
because only the numbering signal survived.

It costs **+5.6% wall clock** (6.29 s → 6.64 s). Take it.

Also note the feature is **new**: v2.106.0 "Infer PDF heading levels so the hierarchy isn't
flattened", v2.109.0 bookmarks/ToC, v2.120.0 font weight/slant/case — the last of those
shipped three days ago. It is heuristic (bookmarks → numbering → font style), not a model
output, so the definition-of-done line *"A converted PDF's `#`/`##`/`###` hierarchy is
intact … checked against a golden fixture"* needs a fixture with a real ToC, and needs a
documented answer for what `heading_path` does when a PDF has no bookmarks, no numbering
and uniform fonts. Right now phase 3 assumes that case cannot happen. It is the common case.

### C2 — The install target's extras are all invalid. *(§"Docling, settled")*

```
docling[format-pdf-docling,format-office,format-web,models-local,feat-chunking]
```

All five produce `does not have an extra named …` warnings and are ignored; the resolution
is byte-identical to plain `docling`. Those are **`docling-slim`** extras. `docling` is a
metapackage for `docling-slim[standard]` (since v2.92.0), which already provides all five.

**Fix:** `docling` — plus `docling[rapidocr]` if you want the fast onnxruntime OCR backend
(§1.3), which you do.

### C3 — "No torch … in the core" is wrong; torch is not opt-in. *(§"Docling, settled")*

> "Torch lives in the `models-local` extra."

It does — but `docling` → `docling-slim[standard]` lists `torch<3.0.0,>=2.2.2` and
`torchvision` **directly**. `pip install docling` installs torch whether you ask or not.
Measured: 475 MB on disk (macOS arm64), 122 MB wheel on Windows.
The "~250 MB of CPU torch" estimate is low; budget ~450–500 MB on disk on Windows, and
**~1.0–1.2 GB for the whole venv** (measured 1.0 GB / 105 packages here).

The *conclusion* — Windows PyPI torch is CPU-only — is **correct**, and I verified it: all
`nvidia-*`/`triton` deps are gated `platform_system == "Linux"`.

### C4 — "No pandas … in the core" is wrong; the conclusion survives anyway. *(§"Docling, settled")*

`docling-core` declares **`pandas<4.0.0,>=2.1.4` as a hard dependency**. An unconstrained
`pip install docling` resolves **`pandas==3.0.5`**, which breaks `nemoguardrails`.
Co-resolved with the repo's `pandas>=2.2,<2.4` it lands on **`pandas==2.3.3`** — clean.

**Fix:** rewrite the bullet as *"compatible, because we already pin `pandas<2.4`; without
that pin Docling resolves pandas 3.x and breaks nemoguardrails."* Add a spike step that
asserts the resolved pandas version.

### C5 — The "pipeline settings" table is labelled as defaults and mostly is not. *(§"Docling, settled")*

> "**Pipeline settings — these are the defaults**, override only with a measurement"

Actual defaults, dumped from `PdfPipelineOptions()` in the installed 2.120.1:

| Setting | Phase 3 says | **Actual default** |
|---|---|---|
| `do_ocr` | `False` | **`True`** *(keep phase 3's `False` — see C6)* |
| `TableFormerMode` | `FAST` | **`ACCURATE`** |
| `do_table_structure` | `True` | `True` ✅ |
| `do_picture_classification` | `False` | `False` ✅ |
| `do_code_enrichment` | `False` | `False` ✅ |
| `do_formula_enrichment` | `False` | `False` ✅ |
| — *(not mentioned)* | — | `heading_hierarchy_options.enabled = False` ← **C1** |
| — *(not mentioned)* | — | `generate_parsed_pages = False` |
| — *(not mentioned)* | — | `accelerator_options.num_threads = 4` |
| — *(not mentioned)* | — | `layout_batch_size / ocr_batch_size / table_batch_size = 4` |

**Fix:** relabel as "our settings, and how they differ from the defaults", and add the four
missing rows.

### C6 — `do_ocr=False` is the right setting for the wrong reason, and its mitigation is redundant. *(§"Docling, settled" + §Risks)*

> `do_ocr | False` — "OCR on every page of a 100-page text-native PDF is minutes of CPU for
> no gain. **Auto-enable per page only when the page has no text layer.**"

Two separate claims; they do not both survive.

**The setting is right, and the cost estimate is nearly right.** Measured: OCR accounted for
**33.46 s of 38.09 s (88%)** of pipeline time on a 9-page *born-digital* document —
**3.7 s/page for nothing**. On a 100-page text-native PDF that is ~6 minutes. Phase 3's
"minutes of CPU for no gain" is accurate. **Keep `do_ocr=False` as the default.**

**The stated mechanism is wrong, and the proposed mitigation already exists.**
`OcrMode.DEFAULT` is wired to `PDF_AWARE_LAYOUT_REGIONS`, whose source comment reads
*"Eliminate those clusters that contain exclusively text PDF cells"* — Docling already
auto-enables OCR at **region** granularity, finer than the per-page scheme phase 3 proposes
to build. **Delete that work item.** It also explains why OCR is not free on a born-digital
PDF: figure and picture regions have no text cells, so they get OCR'd regardless.

**What to build instead** is a per-*document* decision, which is cheaper than either fixed
value and cheaper than the per-page scheme: probe once for an extractable text layer with
`pypdfium2` (already a dependency), set `do_ocr` accordingly, and emit the branch into the
task 3.5 SSE log.

**The risk note stays, reworded.** *"A scanned PDF with OCR off produces empty sections"* is
real and the per-document probe is the mitigation — but the note should say the OCR path
costs a measured **4.6–6.0 s/page**, not "minutes", so the log can set an honest
expectation.

Independently: install **`docling[rapidocr]`** so the auto-selector reaches the onnxruntime
backend rather than the torch one (§1.3). The 33.46 s above is the torch backend.

### C7 — The VLM dismissal is right for the wrong reason. *(§"Docling, settled")*

> "`granite-docling-258M` / `SmolDocling-256M` doing full-page inference on CPU is far too
> slow for interactive ingestion. The larger catalog entries (Pixtral-12B, Gemma-3-27B) are
> **flatly impossible at 16 GB with no GPU**."

The first sentence is right. The second is wrong twice over, and the 258M/27B framing
implies the small model shares the large model's problem, which it does not.

- **Measured, not argued (§2.2a):** granite-docling-258M CPU-only on a 16 GB machine used
  **2,027 MB peak RSS** — *less* than the 2,199 MB the standard pipeline used on the same
  document. "Flatly impossible at 16 GB" is backwards. Concede this explicitly; the
  pushback that prompted this research was correct.
- Pixtral-12B and Gemma-3-27B are **MLX presets** — Apple Silicon only. On Windows they
  fail at import, not at allocation. `PIXTRAL_12B_TRANSFORMERS` exists for CPU and Docling
  clocks it at **1,828 s/page**.

**Fix:** replace the whole bullet with the measurement — **281 s/page vs 1.10 s/page,
255×, at equal memory.** That is a stronger argument than the one being replaced, and it
survives the obvious objection instead of inviting it. Add that on Windows CPU the
transformers backend is the *only* one available (MLX is Apple-only, vLLM needs a server,
there is no ONNX backend), and that the VLM run returned **flat headings too**, so it does
not rescue C1. If the VLM path is ever revisited it is **granite-docling-258M**, not
SmolDocling (table TEDS 0.96 vs 0.76).

### C8 — The model download is 1.3 GB, not 358 MB, and the repo moved. *(§"Docling, settled" + §Risks)*

> "The `ds4sd/docling-models` HF repo is ~358 MB total."

Measured after a real `docling-tools models download`: **1.3 GB** in
`~/.cache/docling/models`, across six directories — including a **631 MB CodeFormulaV2**
that our configuration never loads. The 358 MB figure matches only the TableFormer repo
(342 MB measured), which is now published as **`docling-project/docling-models`**, not
`ds4sd/`.

**Fix:** state 1.3 GB for the default prefetch, and prefetch selectively instead:

```
docling-tools models download layout tableformer rapidocr    # ~575 MB
```

### C9 — Cold start is 50–120 s and is not mentioned anywhere. *(§3.0, §Risks — new)*

Measured: the first conversion in a process costs **49.7–118.6 s** for a 9-page document
(model load + `torch.compile`); subsequent conversions in the same process cost **9.9–12.7 s**.

Nothing in phase 3 accounts for this. The worker in task 3.4 must **warm the
`DocumentConverter` at startup**, or the first jury upload stalls for a minute and the SSE
log in task 3.5 shows nothing while it does. Add it to the definition of done.

### C10 — More threads do not help the configuration we are shipping. *(§3.0 — new)*

Measured 4 vs 8 threads:

| | 4 threads | 8 threads |
|---|---:|---:|
| Warm, OCR **off** | 12.29 s | 12.70 s — **no gain** |
| Cold, OCR off | 49.7 s | 118.6 s — **2.4× worse** |
| Warm, OCR **on** | 53.78 s | 41.42 s — 23% better, +1 GB RSS |

Layout and TableFormer do not scale past 4 threads on an 8-core laptop; only OCR does, and
we are shipping with OCR off by default (C6). **Leave `num_threads` at the default 4.**

Docling's published 1.5 pages/s CPU figure was taken at **16 threads on a 16-vCPU desktop**
and does not transfer to a laptop — plan against **0.7–0.9 pages/s**.

### C11 — "1.74 s per table" is a delta, not an absolute. *(§Risks)*

> "TableFormer at 1.74 s per table adds up. A 40-table document is over a minute in table
> recovery alone."

Measured here, FAST vs ACCURATE differed by 2.4 s across 3 tables ≈ **0.8 s/table saved**.
That is the marginal cost of ACCURATE, not the absolute cost of doing tables at all. The
40-table arithmetic is unsupported. Either re-measure in the 3.0 spike or drop the number.
*(The architectural conclusion it supports — ingestion is a durable job, not a blocking
POST — is right regardless, and is right for better reasons: cold start, §C9.)*

### C12 — The `models-onnxruntime` fallback installs a **GPU** runtime on Windows. *(§3.0)*

> "If the spike shows torch is a problem, `models-onnxruntime` is the fallback."

On Windows that extra resolves **`onnxruntime-gpu==1.23.2`**, not the CPU build —
`docling-slim` declares `onnxruntime-gpu<1.24` under
`sys_platform == "linux" or sys_platform == "win32"` and plain `onnxruntime` only under
`darwin`. Verified with `uv pip compile --python-platform x86_64-pc-windows-msvc`. On a
laptop with no CUDA that is a heavy install that cannot execute.

The extra you want is **`docling[rapidocr]`**, which resolves `onnxruntime==1.28.0` (CPU)
on Windows and simultaneously promotes the OCR auto-selector off the slow torch backend
(§1.3, C6).

**Fix:** replace `models-onnxruntime` with `docling[rapidocr]` as the stated fallback, and
add the ONNX-vs-GPU distinction to the 3.0 spike checklist. The rest of that sentence —
that ONNX only covers the layout model and TableFormer still needs torch — is **correct**
(see C13).

### C13 — Smaller factual notes

- `LayoutOptions` / `DOCLING_LAYOUT_*` are **deprecated**; use
  `LayoutObjectDetectionOptions.from_preset("layout_heron_default")`. Phase 3's choice of
  `docling-layout-heron` as the default is **correct** (42.9 M params, 171.7 MB).
- The coverage half of the `models-onnxruntime` claim — *"it currently only covers the
  heron layout model, so TableFormer would still need torch"* — is **correct, verified**
  (the *packaging* half is C12). After a full
  `docling-tools models download` the only `.onnx` files in the cache are
  `docling-layout-heron-onnx/model.onnx`, `DocumentFigureClassifier-v2.5/model.onnx`, and
  the three RapidOCR checkpoints. TableFormer ships **safetensors only**, and there is no
  ONNX engine option under `docling/models/stages/table_structure/`. Torch stays.
- Docling is **MIT**; model weights are Apache-2.0 (`docling-layout-heron`). Clean for a
  competition entry. Phase 3 does not state this and it is worth a line for the jury.
- Add a **multi-column** golden fixture. [Issue #2067](https://github.com/docling-project/docling/issues/2067)
  is an open, silent failure mode that produces scrambled chunks rather than an error, and
  an academic-paper-shaped jury PDF walks straight into it.

---

## 7. What I could not verify

Listed rather than estimated.

- **CPU seconds-per-page for SmolDocling-256M.** Not measured; I measured
  granite-docling-258M instead (§2.2a), which supersedes it and is the one anyone would
  actually use. Docling's published SmolDocling figures are MPS/MLX, not CPU.
- **Whether 281 s/page transfers to Windows x86.** §2.2a is macOS arm64 with
  `AcceleratorDevice.CPU` forced. A Windows laptop will differ — but the standard pipeline
  was measured on the same machine under the same conditions, so the **255× ratio** is the
  robust part of that finding, not the absolute.
- **VLM output quality.** §2.2a was one page and I did not score it. The claim in §2.4 that
  granite beats SmolDocling is from IBM's model card, not my own evaluation.
- **mAP for `docling-layout-heron` specifically.** arXiv:2509.11720 publishes 78% mAP for
  heron-101; the default heron's own figure I could not find.
- **How much `docling[rapidocr]` (onnxruntime backend) actually beats the torch backend.**
  The 33.46 s OCR figure in §3.3 is the torch backend. I verified the *packaging* difference
  on Windows but did not benchmark the two backends against each other. The 3.0 spike should.
- **Windows-native timings.** Every measurement here is macOS arm64. The 3.0 spike must
  re-run them on the demo machine — that instruction in phase 3 is correct and should stay.
- Third-party accuracy percentages in §3.4 (97.9% tables, 96.1% multi-column) come from
  vendor and blog benchmarks on undisclosed corpora. Directional only.
