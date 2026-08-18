"""The parse quality gate — because a bad parse does not raise (D-parse).

Docling fails **silently** on layouts it reads wrongly
([#2067](https://github.com/docling-project/docling/issues/2067)): no exception, no
partial-success status, just scrambled reading order that chunks, embeds and indexes
exactly like good text. Every stage after ``parse`` faithfully processes the scramble,
the vector store accepts it, the lexical arm matches it, and the first sign anything is
wrong is an answer that quotes two half-sentences from opposite columns and cites them
both correctly. There is nothing downstream that can tell.

So the parse is scored where the evidence still exists — at parse time, against the
document itself — and the score is recorded on the ``documents`` row as
``parse_confidence``.

Three signals, and what each is actually for
--------------------------------------------

**1. The ordering cross-check — the primary signal.** The raw text layer is extracted
independently (:func:`aegis.ingestion.probe.probe_page_text`, PDFium, content-stream
order) and its *ordering* is compared with Docling's block order. Ordering is the signal
and overlap is not: column interleaving keeps every token and loses only the sequence,
so a token-overlap check on a scrambled two-column page scores ~1.0 and says nothing.

The comparison is **Kendall's tau over anchor tokens** — tokens of four characters or
more that occur exactly once in *both* readings, so a match is unambiguous and no
alignment heuristic is needed. Tau counts inverted pairs, which is precisely what column
interleaving produces (every left-column token after the interleave point sits behind
right-column tokens it should precede) and precisely what a rank correlation like
Spearman blurs by squaring distances instead of counting order violations. With unique
anchors there are no ties, so tau-a and tau-b coincide.

It is computed **per page and then averaged, weighted by anchor count**, not once over
the whole document — and that is not a detail. Measured on ``bert-two-column.pdf`` with
its blocks deliberately re-ordered into the naive top-to-bottom sequence a layout model
that has not detected the columns produces: document-wide tau is **0.967**, per-page tau
is **0.565**. Reading order is scrambled *within* a page and never across pages, so the
document-wide figure is dominated by the cross-page pairs that cannot invert (on a
16-page document, fifteen sixteenths of them), and it dilutes the one thing being
measured almost to nothing.

**2. Fragment rate.** Prose blocks that end mid-clause — no terminal punctuation —
spike when a paragraph is cut at a column boundary and its remainder is filed elsewhere.
A real document has some: a lead-in before a list, a line ending in a footnote marker,
a paragraph broken across a page. So the rate is scored against a floor drawn above what
correct parses actually measure rather than against zero.

**3. The heading histogram — for the FLAT case only.** Everything at level 1 across a
long, structured document means the heading hierarchy is not running, which is a real
failure mode if :mod:`aegis.ingestion.convert`'s configuration is ever regressed
(``{1: 33}`` on Docling's defaults, measured on ``census-income-tables.pdf``).

**It is emphatically not the defence against the half-configured case**, and this
docstring says so because the phase's original design assumed it was. Measured on
``docling==2.120.3``: enabling ``heading_hierarchy_options.enabled`` *alone* yields
``{1: 13, 2: 12, 3: 8}`` — a plausible three-level tree with eleven headings at the wrong
depth. No histogram check can separate well-shaped-and-wrong from correct. The defence
against that case is setting **both** switches, which ``convert.py`` does and asserts.

Why the score is the minimum, not an average
--------------------------------------------

:attr:`ParseQuality.confidence` is the **weakest** of the three sub-scores. The three
detect disjoint failures, so averaging would let a perfect ordering score hide a heading
tree that is entirely flat — which is exactly the arithmetic that turns a gate into a
decoration. A parse is worth what its worst check says it is worth.

Flag, do not block — and why that is the safer choice
------------------------------------------------------

A low score **flags** the document. It does not fail the stage, and the ingest proceeds.
Three reasons, in order of weight:

1. **The signal measures disagreement, not correctness.** Two orderings disagree; the
   cross-check cannot say which one is right. A PDF whose content stream is emitted out
   of visual order — some word processors and many re-assembled scans do exactly that —
   makes PDFium the wrong one and Docling the right one, and blocking would reject a
   correctly parsed document. A gate that blocks a legitimate document is its own
   failure, and this gate has a known false-positive mode.
2. **Blocking would burn the retry budget.** ``parse`` is the most expensive stage in
   the pipeline (0.43–3.20 s/page, measured). Raising here would fail the activity, and
   the orchestrator would re-parse a 126-page document to reach an identical conclusion,
   twice more.
3. **There is no automatic remedy to hand the document to.** The useful next step is a
   human deciding to re-parse with OCR, to supply a different source file, or to accept
   it. A refusal that a tenant cannot act on is worse than a warning they can.

"Not blocked" is not "silent". The score is on the row, the reasons are on the parse
artifact, and :func:`app.ingestion.stages.parse_stage` logs a WARNING naming them when
the score is low — so a low-confidence parse is *visible* everywhere the ingest is, which
is what D-parse asks for.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aegis.ingestion.blocks import BlockKind

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from aegis.ingestion.blocks import ParsedBlock, ParsedDocument

__all__ = [
    "FRAGMENT_CEILING",
    "FRAGMENT_FLOOR",
    "LOW_CONFIDENCE",
    "MIN_ANCHORS_PER_PAGE",
    "MIN_ANCHOR_LENGTH",
    "MIN_FLAT_HEADINGS",
    "MIN_FLAT_PAGES",
    "ParseQuality",
    "assess_parse",
    "fragment_rate",
    "headings_are_flat",
    "ordering_agreement",
]

#: Below this, the parse is reported as low confidence. Calibrated against measurement
#: rather than taste. On ``docling==2.120.3`` the four real fixtures score **0.912, 0.919,
#: 0.997 and 1.000**; the three multi-column ones re-ordered into the failure this gate
#: exists to catch score **0.452, 0.565 and 0.724**; and a heading tree that is entirely
#: flat scores 0.50. The gap is therefore 0.724–0.912.
#:
#: The threshold sits at the *lower* end of that gap rather than in its middle, on
#: purpose: 0.912 is the lowest score four correct documents happened to produce, not a
#: floor for correct parses in general, while 0.724 comes from a scramble whose severity
#: is ours to choose. Anchoring to the real side leaves the margin where the uncertainty
#: actually is.
LOW_CONFIDENCE = 0.75

#: Shortest token that may serve as an ordering anchor. Short words repeat, and a
#: repeated token is not an anchor at all — it is a guess about which occurrence matched.
MIN_ANCHOR_LENGTH = 4

#: Anchors a page needs before its ordering is scored. Below this the tau of a page is
#: one or two pairs of tokens, which is noise with a decimal point on it.
MIN_ANCHORS_PER_PAGE = 8

#: Fragment rate that draws no penalty. Above the highest rate measured on a parse known
#: to be correct — **0.221 on ``census-income-tables.pdf``**, 67 pages of statistical
#: tables whose reading order Docling gets right — because a real document ends
#: paragraphs without full stops all the time: list lead-ins, footnote markers, formulas,
#: table stubs, prose that continues on the next page. The other three fixtures measure
#: 0.029, 0.094 and 0.119.
FRAGMENT_FLOOR = 0.25

#: Fragment rate at which the signal is fully spent. Past three in five prose blocks
#: ending mid-clause, the parse is cutting paragraphs rather than reading them — nearly
#: three times the worst a correct parse of these four documents produces.
FRAGMENT_CEILING = 0.60

#: Headings a document needs before an all-level-1 histogram means anything. A short
#: memo with three headings is legitimately flat.
MIN_FLAT_HEADINGS = 8

#: Pages a document needs before an all-level-1 histogram means anything, for the same
#: reason.
MIN_FLAT_PAGES = 8

#: The confidence a flat heading tree is worth on its own. Not zero — the *text* of such
#: a parse is fine, and only its structure is lost — but firmly under
#: :data:`LOW_CONFIDENCE`, because an entirely flat tree on a long structured document is
#: a configuration regression rather than a document's own shape.
_FLAT_HEADING_SCORE = 0.5

#: Prose blocks shorter than this are not scored for fragmentation. A four-word block is
#: a label, an axis title or a stray line, and it has no clause to end in the middle of.
_MIN_FRAGMENT_WORDS = 10

#: What a completed clause ends with, after any closing quote or bracket is peeled off.
_TERMINAL = frozenset(".!?:;…")

#: Closing punctuation that may follow the terminal mark.
_CLOSERS = "\"')]}”’»"

#: Word characters, after case folding. Deliberately ASCII-alphanumeric: an anchor has to
#: mean the same thing in two extractions that disagree about every other character
#: class, and PDFium and Docling disagree about plenty of them.
_WORD = re.compile(r"[a-z0-9]+")

#: A word broken across a line by a hyphen. Both readers report this differently — PDFium
#: substitutes ``U+FFFE`` for the break and drops the newline, Docling keeps the hyphen —
#: so both forms are healed before tokenising or "representation" is two tokens in one
#: reading and one in the other, and neither is an anchor.
_HYPHEN_BREAK = re.compile(r"[-­]\s*\n\s*")

#: Characters that only ever stand for a removed soft hyphen in these two extractions.
_SOFT_HYPHENS = ("￾", "­")


@dataclass(frozen=True, slots=True)
class ParseQuality:
    """How much the parse of one document can be trusted, and why.

    Attributes:
        confidence: ``0.0``–``1.0``; the **weakest** of the sub-scores below. See the
            module docstring for why the minimum rather than a weighted average.
        ordering: The ordering sub-score — Kendall's tau between Docling's block order
            and the raw text layer's, clamped at zero, averaged over pages and weighted
            by anchor count. ``None`` when no page carried enough anchors to compare,
            which is the honest answer for a scanned document: there is no independent
            reading to cross-check against, so the check did not fail, it did not run.
        ordering_pages: How many pages the ordering score was computed over.
        ordering_anchors: How many anchor tokens it was computed from, in total.
        fragments: The fragment sub-score, ``1.0`` at or below
            :data:`FRAGMENT_FLOOR` and ``0.0`` at or above :data:`FRAGMENT_CEILING`.
        fragment_rate: The raw rate the sub-score was derived from.
        flat_headings: Whether the heading histogram is entirely level 1 on a document
            long enough for that to be a failure rather than a shape. The histogram
            itself is not copied here — it is already
            :attr:`~aegis.ingestion.blocks.ParsedDocument.heading_histogram`, and
            :attr:`reasons` names the levels for the ingest log.
        reasons: One line per signal, written for a person reading an ingest log.
    """

    confidence: float
    ordering: float | None
    ordering_pages: int
    ordering_anchors: int
    fragments: float
    fragment_rate: float
    flat_headings: bool
    reasons: tuple[str, ...]

    @property
    def is_low(self) -> bool:
        """Whether this parse is below :data:`LOW_CONFIDENCE` and must be flagged."""
        return self.confidence < LOW_CONFIDENCE


def _tokens(text: str) -> list[str]:
    """Reduce text to the comparable tokens two different extractors can agree on.

    Args:
        text: Either reader's text.

    Returns:
        Case-folded alphanumeric tokens of at least :data:`MIN_ANCHOR_LENGTH`
        characters, in order.
    """
    normalised = unicodedata.normalize("NFKC", text)
    normalised = _HYPHEN_BREAK.sub("", normalised)
    for soft in _SOFT_HYPHENS:
        normalised = normalised.replace(soft, "")
    return [
        token
        for token in _WORD.findall(normalised.casefold())
        if len(token) >= MIN_ANCHOR_LENGTH
    ]


def _count_inversions(sequence: list[int]) -> int:
    """Count the pairs of ``sequence`` that are out of order.

    A merge sort rather than the obvious double loop: the ordering check runs on every
    parse, and a 126-page document offers tens of thousands of anchors, where the
    quadratic version is minutes and this is milliseconds.

    Args:
        sequence: The values to count inversions in.

    Returns:
        How many pairs ``(i, j)`` with ``i < j`` have ``sequence[i] > sequence[j]``.
    """

    def sort(values: list[int]) -> tuple[list[int], int]:
        if len(values) < 2:
            return values, 0
        middle = len(values) // 2
        left, left_inversions = sort(values[:middle])
        right, right_inversions = sort(values[middle:])
        merged: list[int] = []
        inversions = left_inversions + right_inversions
        index = other = 0
        while index < len(left) and other < len(right):
            if left[index] <= right[other]:
                merged.append(left[index])
                index += 1
            else:
                merged.append(right[other])
                other += 1
                # Everything still on the left is greater than this right-hand value.
                inversions += len(left) - index
        merged.extend(left[index:])
        merged.extend(right[other:])
        return merged, inversions

    return sort(sequence)[1]


def _kendall_tau(parsed: list[str], reference: list[str]) -> tuple[float | None, int]:
    """Return the order agreement between two token sequences, over their anchors.

    Args:
        parsed: Tokens in the parser's reading order.
        reference: Tokens in the raw text layer's order.

    Returns:
        ``(tau, anchors)`` — tau in ``[-1.0, 1.0]``, or ``None`` when fewer than two
        anchors exist and there is no pair to be in order or out of it.
    """
    parsed_counts = Counter(parsed)
    reference_counts = Counter(reference)
    unique = {
        token
        for token, count in parsed_counts.items()
        if count == 1 and reference_counts.get(token) == 1
    }
    parsed_rank = {token: rank for rank, token in enumerate(parsed) if token in unique}
    reference_order = [token for token in reference if token in unique]
    anchors = len(reference_order)
    if anchors < 2:
        return None, anchors
    inversions = _count_inversions([parsed_rank[token] for token in reference_order])
    pairs = anchors * (anchors - 1) // 2
    return 1.0 - 2.0 * inversions / pairs, anchors


def ordering_agreement(
    document: ParsedDocument, page_text: Sequence[str]
) -> tuple[float | None, int, int]:
    """Cross-check Docling's block order against the raw text layer's, page by page.

    The primary D-parse signal. See the module docstring for why this is per page rather
    than per document, and why it is a rank correlation rather than a token overlap.

    Args:
        document: The parse to score.
        page_text: The raw text layer, one string per page in page order, as
            :func:`aegis.ingestion.probe.probe_page_text` returns it.

    Returns:
        ``(tau, pages, anchors)`` — the anchor-weighted mean tau, the number of pages it
        was computed over and the number of anchors behind it. ``tau`` is ``None`` when
        no page cleared :data:`MIN_ANCHORS_PER_PAGE`, which is what a scanned document
        looks like: no independent reading exists, so the check did not run.
    """
    by_page: dict[int, list[str]] = {}
    for block in document.blocks:
        by_page.setdefault(block.page_no, []).append(block.text)
    total_weight = 0
    weighted = 0.0
    pages = 0
    for page_no in sorted(by_page):
        index = page_no - 1
        if not 0 <= index < len(page_text):
            continue
        tau, anchors = _kendall_tau(
            _tokens("\n\n".join(by_page[page_no])), _tokens(page_text[index])
        )
        if tau is None or anchors < MIN_ANCHORS_PER_PAGE:
            continue
        weighted += tau * anchors
        total_weight += anchors
        pages += 1
    if not total_weight:
        return None, 0, 0
    return weighted / total_weight, pages, total_weight


def _ends_mid_clause(text: str) -> bool:
    """Whether a block's text stops without finishing its clause.

    Args:
        text: The block text.

    Returns:
        ``True`` when the last character, ignoring closing quotes and brackets, is not
        terminal punctuation.
    """
    stripped = text.rstrip().rstrip(_CLOSERS)
    return not stripped or stripped[-1] not in _TERMINAL


def fragment_rate(blocks: Iterable[ParsedBlock]) -> tuple[float, int]:
    """Return the share of substantial prose blocks that end mid-clause.

    Restricted to prose — :attr:`~aegis.ingestion.blocks.BlockKind.TEXT` and
    :attr:`~aegis.ingestion.blocks.BlockKind.LIST_ITEM` — of at least
    :data:`_MIN_FRAGMENT_WORDS` words. Headings, captions and table Markdown end without
    a full stop by nature and would drown the signal in blocks that are not fragments at
    all.

    Args:
        blocks: The parsed blocks.

    Returns:
        ``(rate, scored)`` — the rate and how many blocks it was measured over.
        ``(0.0, 0)`` when nothing qualifies, because "no prose" is not evidence of
        fragmentation.
    """
    prose = [
        block
        for block in blocks
        if block.kind in {BlockKind.TEXT, BlockKind.LIST_ITEM}
        and len(block.text.split()) >= _MIN_FRAGMENT_WORDS
    ]
    if not prose:
        return 0.0, 0
    fragments = sum(1 for block in prose if _ends_mid_clause(block.text))
    return fragments / len(prose), len(prose)


def headings_are_flat(histogram: dict[int, int], *, page_count: int) -> bool:
    """Whether the heading tree collapsed to a single level on a long document.

    An all-level-1 histogram is only evidence of failure on a document long enough and
    structured enough for a real hierarchy to have been expected.

    This is the flat case and **only** the flat case; see the module docstring for the
    measurement showing why the histogram cannot catch the half-configured one.

    Args:
        histogram: Heading count per level.
        page_count: How many pages the document has.

    Returns:
        ``True`` when every heading is at level 1, there are at least
        :data:`MIN_FLAT_HEADINGS` of them and the document runs to at least
        :data:`MIN_FLAT_PAGES` pages.
    """
    return (
        page_count >= MIN_FLAT_PAGES
        and set(histogram) == {1}
        and histogram[1] >= MIN_FLAT_HEADINGS
    )


def assess_parse(document: ParsedDocument, page_text: Sequence[str]) -> ParseQuality:
    """Score a parse against the three D-parse signals and say why.

    Args:
        document: The parse to score.
        page_text: The raw text layer, one string per page in page order. Pass ``()``
            when there is none to compare against — the ordering check then reports that
            it did not run rather than reporting a failure it did not observe.

    Returns:
        The score, its three components, and one reason line per signal.
    """
    ordering, pages, anchors = ordering_agreement(document, page_text)
    rate, scored = fragment_rate(document.blocks)
    histogram = document.heading_histogram
    flat = headings_are_flat(histogram, page_count=document.page_count)

    reasons: list[str] = []
    scores: list[float] = []

    if ordering is None:
        reasons.append(
            "reading order not cross-checked — no page carried enough text-layer "
            f"anchors (needs {MIN_ANCHORS_PER_PAGE} per page); this is expected for a "
            "scanned document and means the check did not run, not that it failed"
        )
    else:
        clamped = max(0.0, ordering)
        scores.append(clamped)
        reasons.append(
            f"reading order agrees with the raw text layer at tau={ordering:.3f} "
            f"over {anchors} anchor token(s) on {pages} page(s)"
            if clamped >= LOW_CONFIDENCE
            else (
                f"reading order DISAGREES with the raw text layer: tau={ordering:.3f} "
                f"over {anchors} anchor token(s) on {pages} page(s) — the two readings "
                "put this document's text in different orders, which is what a "
                "multi-column page read across the columns looks like"
            )
        )

    fragments = 1.0
    if scored:
        span = FRAGMENT_CEILING - FRAGMENT_FLOOR
        fragments = 1.0 - min(1.0, max(0.0, (rate - FRAGMENT_FLOOR) / span))
        scores.append(fragments)
        reasons.append(
            f"{rate:.0%} of {scored} prose block(s) end mid-clause"
            + ("" if fragments == 1.0 else " — paragraphs are being cut, not read")
        )
    else:
        reasons.append("no prose blocks long enough to score for fragmentation")

    if flat:
        scores.append(_FLAT_HEADING_SCORE)
        reasons.append(
            f"every one of {histogram[1]} heading(s) is at level 1 across "
            f"{document.page_count} pages — the heading hierarchy is not running"
        )
    else:
        reasons.append(f"heading levels {histogram}")

    if not scores:
        # Nothing was checkable: no text layer to compare against and no prose to score.
        # Reporting 1.0 here would claim confidence from the absence of evidence, which
        # is the exact move this whole module exists to refuse.
        scores.append(0.0)
        reasons.append(
            "no signal could be computed — this parse produced neither prose nor a "
            "text layer to check it against, so nothing about it is trustworthy"
        )

    return ParseQuality(
        confidence=round(min(scores), 4),
        ordering=ordering,
        ordering_pages=pages,
        ordering_anchors=anchors,
        fragments=fragments,
        fragment_rate=rate,
        flat_headings=flat,
        reasons=tuple(reasons),
    )
