# PDF fixtures — the Phase 4 ingestion baseline

Four real documents, chosen so that each one **fails a different way** if the pipeline is
wrong. They are the baseline the ingestion work is measured against, not a smoke test.

The PDFs themselves are **not committed** — see *Why these are not in git* below. Run
`./fetch.sh` to download them; every file is checksum-verified, so a fixture that silently
changes upstream fails loudly instead of quietly moving the baseline.

| File | Pages | Why it is here |
|---|---:|---|
| `bert-two-column.pdf` | 16 | **The multi-column silent-failure case.** Two-column academic layout is Docling's known weakness ([#2067](https://github.com/docling-project/docling/issues/2067)) and it does not raise — it scrambles reading order into plausible-looking text. This is the fixture the D-parse quality gate (task 4.6c) must score *low*. A gate that never fires is not a gate. |
| `transformer-single-column.pdf` | 15 | Single-column control for the one above. Same genre, same era, same kind of tables — so a difference in parse confidence between the two is attributable to **layout**, not to content. Without a control, a low score on the two-column file proves nothing. |
| `irs-1040-instructions-tables.pdf` | 126 | **Table density and scale.** Dense government tables with merged cells and nested headers — the TableFormer `ACCURATE` case (D3b). At 126 pages it is also the cost and duration fixture: ~1.1 s/page means a parse of over two minutes, which is what stage-level resume, the CPU-queue serialisation and the budget preflight all exist for. |
| `census-income-tables.pdf` | 67 | **Statistical tables with footnotes and multi-level headers**, plus real section structure for the heading-hierarchy assertion (D2). The one most likely to expose the `{1: N}` flat-heading failure, because its true structure is genuinely several levels deep. |

## What they are used for

- **Task 4.0** — the spike: one real PDF end to end on the target machine.
- **Task 4.6c** — the parse quality gate: `bert-two-column.pdf` must score low,
  `transformer-single-column.pdf` must score high. Both assertions, or the gate is untested.
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
