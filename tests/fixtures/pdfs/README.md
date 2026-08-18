# PDF fixtures — the Phase 4 ingestion baseline

Four real documents, chosen so that each one **fails a different way** if the pipeline is
wrong. They are the baseline the ingestion work is measured against, not a smoke test.

The PDFs themselves are **not committed** — see *Why these are not in git* below. Run
`./fetch.sh` to download them; every file is checksum-verified, so a fixture that silently
changes upstream fails loudly instead of quietly moving the baseline.

| File | Pages | Why it is here |
|---|---:|---|
| `bert-two-column.pdf` | 16 | **The multi-column case — which turned out *not* to fail.** Two-column academic layout is Docling's known weakness ([#2067](https://github.com/docling-project/docling/issues/2067)) and it does not raise when it goes wrong. Measured on `docling==2.120.3` (task 4.6c): it does **not** go wrong here. This document's reading order agrees with the raw text layer at tau **0.997**, and it scores **0.997** on the D-parse gate. See *What they are used for* below for what replaced the assertion this row originally asked for. |
| `transformer-single-column.pdf` | 15 | Single-column control for the one above. Same genre, same era, same kind of tables — so a difference in parse confidence between the two is attributable to **layout**, not to content. Without a control, a low score on the two-column file proves nothing. |
| `irs-1040-instructions-tables.pdf` | 126 | **Table density and scale.** Dense government tables with merged cells and nested headers — the TableFormer `ACCURATE` case (D3b). At 126 pages it is also the cost and duration fixture: ~1.1 s/page means a parse of over two minutes, which is what stage-level resume, the CPU-queue serialisation and the budget preflight all exist for. |
| `census-income-tables.pdf` | 67 | **Statistical tables with footnotes and multi-level headers**, plus real section structure for the heading-hierarchy assertion (D2). The one most likely to expose the `{1: N}` flat-heading failure, because its true structure is genuinely several levels deep. |

## What they are used for

- **Task 4.0** — the spike: one real PDF end to end on the target machine.
- **Task 4.6c** — the parse quality gate. The plan was "`bert-two-column.pdf` must score
  low, `transformer-single-column.pdf` must score high". **The first half is false on
  `docling==2.120.3`**: all four fixtures parse well and score 0.912–1.000. So the gate is
  proved against the *failure* instead — `aegis/tests/ingestion/test_parse_confidence.py`
  re-orders each real parse by position alone (top to bottom, columns not detected, which
  is what a layout model that missed the split produces) and asserts the score collapses.
  That gives a **stronger** control than this row asked for, because the identical
  operation is applied to every fixture and only the multi-column ones move:

  | fixture | Docling's order | read across the columns |
  |---|---|---|
  | `transformer-single-column.pdf` | 1.000 | 1.000 — unchanged |
  | `bert-two-column.pdf` | 0.997 | **0.565 — low** |
  | `census-income-tables.pdf` | 0.919 | **0.724 — low** |
  | `irs-1040-instructions-tables.pdf` | 0.912 | **0.452 — low** |
- **Task 4.11** — the span-anchored gold set is built over these documents.
- **D2** — the heading-level histogram assertion runs on `census-income-tables.pdf`.

## Why these are not in git

`irs-1040-instructions-tables.pdf` and `census-income-tables.pdf` are US Government works and
are public domain. The two arXiv papers are **not** — they are distributed under the authors'
chosen licence, which permits download but does not clearly permit redistribution inside
another repository. Committing them would put a licensing question into the repo for ~3 MB of
convenience.

`fetch.sh` records the exact URL and SHA-256 of each, which is reproducible without the
ambiguity. **Run it once before working on Phase 4**, and once on the demo machine while there
is still network — do not discover on 30 August that the fixtures are missing.

## Adding a fixture

Add the URL and its SHA-256 to `fetch.sh`, and add a row above saying **which failure it
catches**. A fixture that does not have an answer to that question is a file, not a fixture.
