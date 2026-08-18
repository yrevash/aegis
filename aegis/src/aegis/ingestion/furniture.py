"""Running headers, footers and page numbers — found by repetition, and removed.

A running header is text the reader's eye skips and a retriever cannot. Left in, "2025
Instructions for Form 1040 — Page 43" is prepended to every chunk on every page: it
dilutes each embedding with the same 40 characters, it makes the corpus-wide keyword arm
match the header instead of the content, and — the concrete failure this task exists to
prevent — it pushes near-identical chunks over the Jaccard threshold the existing dedup
uses, so genuinely different passages start deduplicating each other. That looks like a
retrieval bug on stage and it is a parsing bug.

Position alone is not evidence, and neither is the label
--------------------------------------------------------

Two signals, because each misses on its own:

* **The parser's own label.** ``page_header`` / ``page_footer`` is authoritative when it
  is there. Measured on the four Phase 4 fixtures under Docling 2.120.3, it is not:
  those documents' running furniture is either dropped by the layout model before we see
  it or emitted as ordinary ``text``. A stripper that trusted the label would be
  untested code that looks correct.
* **Repetition in a margin band.** Text near the top or bottom edge that recurs across
  many pages is furniture; text that appears once there is a title or a first line. So
  the test is repetition *and* position, never either alone — a section heading at the
  top of its page must survive, and it does, because it happens once.

Page numbers are the same rule, not a special case
---------------------------------------------------

Comparison runs on a normalised form with digit runs collapsed to ``#``, so "Page 43"
and "Page 44" are one pattern rather than 126 unique strings — which also means a bare
page number normalises to ``#`` and is caught by the ordinary repetition test. No
separate numeric branch exists, because a second code path is a second thing to get
wrong.

What is removed is recorded
---------------------------

Every removal returns a :class:`~aegis.ingestion.blocks.FurnitureRun` naming the
pattern, a verbatim sample and the pages it came off. Deleting text from a tenant's
document silently is not acceptable at any scale, and the ingest log (task 4.12) shows
this for the same reason it shows the OCR decision: a wrong call must be visible, not
inferred later from a bad answer.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence

from aegis.ingestion.blocks import BlockKind, FurnitureRun, ParsedBlock, ParsedPage

__all__ = [
    "BAND_FRACTION",
    "MIN_PAGES",
    "MIN_REPEATS",
    "MIN_REPEAT_RATIO",
    "normalise_furniture_text",
    "strip_running_furniture",
]

#: How much of the page height, top and bottom, counts as the margin band.
BAND_FRACTION = 0.08

#: Documents shorter than this cannot establish a run, so only labelled furniture goes.
MIN_PAGES = 3

#: A pattern must appear on at least this many distinct pages.
MIN_REPEATS = 3

#: …and on at least this fraction of the document. A phrase on 3 pages of 126 is
#: content that happens to recur; a running header is on most of the document.
MIN_REPEAT_RATIO = 0.2

_DIGITS = re.compile(r"\d+")
_WHITESPACE = re.compile(r"\s+")
_EDGE_NOISE = re.compile(r"^[^0-9a-z#]+|[^0-9a-z#]+$")

_LABELLED_FURNITURE = {
    BlockKind.PAGE_HEADER: "header",
    BlockKind.PAGE_FOOTER: "footer",
}


def normalise_furniture_text(text: str) -> str:
    """Reduce a line to the form two pages' worth of the same header share.

    Digit runs collapse to ``#`` so the page number does not make every occurrence
    unique; case and whitespace are flattened; leading/trailing punctuation is dropped
    so "— Page 4 —" and "Page 4" are one pattern.

    Args:
        text: The block's verbatim text.

    Returns:
        The normalised comparison key, ``""`` if nothing survives.
    """
    folded = _WHITESPACE.sub(" ", text.casefold()).strip()
    collapsed = _DIGITS.sub("#", folded)
    return _EDGE_NOISE.sub("", collapsed)


def _band_of(block: ParsedBlock, page_height: float, band_fraction: float) -> str | None:
    """Return which margin band the block sits in, or ``None`` for the body.

    Args:
        block: The block to place.
        page_height: Height of the page it sits on, in points.
        band_fraction: Fraction of the page height each band occupies.

    Returns:
        ``"header"``, ``"footer"`` or ``None``.
    """
    if block.bbox is None or page_height <= 0:
        return None
    band = page_height * band_fraction
    if block.bbox.bottom <= band:
        return "header"
    if block.bbox.top >= page_height - band:
        return "footer"
    return None


def strip_running_furniture(
    blocks: Sequence[ParsedBlock],
    pages: Sequence[ParsedPage],
    *,
    band_fraction: float = BAND_FRACTION,
    min_repeats: int = MIN_REPEATS,
    min_repeat_ratio: float = MIN_REPEAT_RATIO,
) -> tuple[tuple[ParsedBlock, ...], tuple[FurnitureRun, ...]]:
    """Remove running headers, footers and page numbers, and say what was removed.

    Args:
        blocks: Every block, in reading order.
        pages: The document's pages, for their heights.
        band_fraction: Fraction of page height, top and bottom, treated as margin.
        min_repeats: Distinct pages a pattern must appear on to count as a run.
        min_repeat_ratio: …and the fraction of the document it must span.

    Returns:
        The surviving blocks in their original order, and one
        :class:`~aegis.ingestion.blocks.FurnitureRun` per removed pattern.
    """
    heights = {page.page_no: page.height for page in pages}
    page_count = len(pages) or len({block.page_no for block in blocks})

    #: (band, normalised text) -> positions of the blocks carrying it
    candidates: dict[tuple[str, str], list[int]] = defaultdict(list)
    labelled: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, block in enumerate(blocks):
        band = _LABELLED_FURNITURE.get(block.kind)
        if band is not None:
            labelled[(band, normalise_furniture_text(block.text))].append(index)
            continue
        if page_count < MIN_PAGES:
            continue
        band = _band_of(block, heights.get(block.page_no, 0.0), band_fraction)
        if band is None:
            continue
        key = normalise_furniture_text(block.text)
        if not key:
            continue
        candidates[(band, key)].append(index)

    threshold = max(min_repeats, round(page_count * min_repeat_ratio))
    doomed: set[int] = set()
    runs: list[FurnitureRun] = []
    for repeated, is_run in ((labelled, False), (candidates, True)):
        for (band, pattern), found in sorted(repeated.items()):
            on_pages = sorted({blocks[index].page_no for index in found})
            if is_run and len(on_pages) < threshold:
                continue
            doomed.update(found)
            runs.append(
                FurnitureRun(
                    pattern=pattern,
                    sample=blocks[found[0]].text,
                    pages=tuple(on_pages),
                    band=band,
                )
            )
    kept = tuple(block for index, block in enumerate(blocks) if index not in doomed)
    return kept, tuple(runs)
