"""The title a document gets listed under, on the layouts that used to defeat it.

``_derive_title`` took the first heading. On a born-digital paper that is the printed
title; **on a reprint it is the running header**, and a reprint is not an exotic shape —
it is what every CFR part, every gazette and every bound statutory volume looks like.

Measured on this repository's own corpus, on ``docling==2.120.3``:

* ``ftc-mail-internet-telephone-order-merchandise-rule-16cfr435.pdf`` and
  ``ftc-informal-dispute-settlement-procedures-16cfr703.pdf`` both derived
  ``"Federal Trade Commission"`` — two unrelated regulations, one title, two identical
  rows on any screen listing ``documents.title``;
* ``reg-z-billing-error-resolution-12cfr1026-13.pdf`` derived ``"§1026.13"``, its recto
  running head, rather than ``"§1026.13 Billing error resolution."``.

Retrieval never suffered — the chunk prefix carries the section path — so nothing failed
loudly, which is exactly why this needs a test rather than a fixed row.

The fixtures here are built by hand from the real page distributions those three
documents produce, so each case can be pointed at the function without a PDF stack. The
page numbers are the whole of the evidence and are not decoration: a running header is a
heading printed page after page, and a title is a heading printed once.
"""

from __future__ import annotations

from aegis.ingestion.blocks import BBox, BlockKind, ParsedBlock, ParsedDocument, ParsedPage
from aegis.ingestion.probe import OcrDecision

from app.ingestion.stages import _derive_title, _running_headers

_BOX = BBox(left=0.0, top=0.0, right=10.0, bottom=10.0)
_OCR = OcrDecision(enabled=False, reason="test")


def _heading(text: str, *, page: int, level: int = 1) -> ParsedBlock:
    """One heading on one page."""
    return ParsedBlock(
        kind=BlockKind.HEADING, text=text, page_no=page, bbox=_BOX, level=level
    )


def _document(blocks: list[ParsedBlock], *, pages: int) -> ParsedDocument:
    """A parsed document of ``pages`` pages carrying ``blocks``."""
    return ParsedDocument(
        source_name="reprint.pdf",
        pages=tuple(
            ParsedPage(
                page_no=n, width=612.0, height=792.0, char_count=2000, has_text_layer=True
            )
            for n in range(1, pages + 1)
        ),
        blocks=tuple(blocks),
        ocr=_OCR,
    )


def _cfr_part_703() -> ParsedDocument:
    """16 CFR 703 as Docling reads it: a verso running head above the part title.

    The page distribution is the measured one — "Federal Trade Commission" on three of
    six pages, the part title on one.
    """
    return _document(
        [
            _heading("Federal Trade Commission", page=1, level=2),
            _heading("PART 703-INFORMAL DISPUTE SETTLEMENT PROCEDURES", page=1),
            _heading("§703.1 Definitions.", page=1, level=2),
            _heading("Federal Trade Commission", page=3, level=2),
            _heading("§703.4 Qualification of members.", page=3, level=2),
            _heading("Federal Trade Commission", page=5, level=2),
        ],
        pages=6,
    )


def test_the_running_header_is_not_the_title() -> None:
    """The defect itself: the part title, not the agency printed above it."""
    assert _derive_title(_cfr_part_703(), "16cfr703.pdf") == (
        "PART 703-INFORMAL DISPUTE SETTLEMENT PROCEDURES"
    )


def test_two_reprints_from_one_agency_no_longer_collide() -> None:
    """The consequence that was visible on a screen, asserted as a screen would see it.

    A test that only checked one document would pass on a heuristic that returned the
    agency name for both, so the *distinctness* is what is asserted.
    """
    part_435 = _document(
        [
            _heading("Federal Trade Commission", page=1, level=2),
            _heading("PART 435-MAIL, INTERNET, OR TELEPHONE ORDER MERCHANDISE", page=1),
            _heading("Federal Trade Commission", page=3, level=2),
            _heading("16 CFR Ch. I (1-1-23 Edition)", page=4),
            _heading("Federal Trade Commission", page=5, level=2),
        ],
        pages=5,
    )

    titles = {
        _derive_title(part_435, "16cfr435.pdf"),
        _derive_title(_cfr_part_703(), "16cfr703.pdf"),
    }

    assert len(titles) == 2, f"two unrelated regulations still share one title: {titles}"
    assert "Federal Trade Commission" not in titles


def test_a_section_number_alone_is_not_the_section_title() -> None:
    """12 CFR 1026.13: every heading is level 1, so heading *depth* could not have told
    the running heads from the title. Repetition across pages can, and does."""
    reg_z = _document(
        [
            _heading("§1026.13", page=1),
            _heading("12 CFR Ch. X (1-1-23 Edition)", page=1),
            _heading("§1026.13 Billing error resolution.", page=1),
            _heading("Bur. of Consumer Financial Protection", page=2),
            _heading("§1026.13", page=3),
            _heading("12 CFR Ch. X (1-1-23 Edition)", page=3),
            _heading("Bur. of Consumer Financial Protection", page=4),
        ],
        pages=4,
    )

    assert _derive_title(reg_z, "12cfr1026-13.pdf") == "§1026.13 Billing error resolution."


def test_a_title_reprinted_on_its_first_content_page_is_still_the_title() -> None:
    """The regression this fix must not cause, and the reason the test is a *share* of
    pages rather than "appears more than once".

    ``census-income-tables.pdf`` prints "Poverty in the United States: 2022" on three of
    its 67 pages — a cover, a half-title and a section head. Any rule that rejected a
    heading for repeating at all would have thrown that title away and listed the
    document under "Current Population Reports".
    """
    report = _document(
        [
            _heading("Poverty in the United States: 2022", page=1),
            _heading("Current Population Reports", page=1, level=2),
            _heading("Poverty in the United States: 2022", page=3),
            _heading("Poverty in the United States: 2022", page=9),
        ],
        pages=67,
    )

    assert _running_headers(report) == frozenset()
    assert _derive_title(report, "census.pdf") == "Poverty in the United States: 2022"


def test_a_paper_whose_first_heading_is_its_title_is_untouched() -> None:
    """The case that already worked. It has to keep working, or the fix traded one
    wrong title for another."""
    paper = _document(
        [
            _heading("Attention Is All You Need", page=1),
            _heading("Abstract", page=1, level=3),
            _heading("1 Introduction", page=2),
        ],
        pages=15,
    )

    assert _derive_title(paper, "transformer.pdf") == "Attention Is All You Need"


def test_a_short_document_is_not_judged_on_repetition() -> None:
    """On two pages every heading trivially covers half of them, so the running-header
    test would reject the real title and every alternative to it."""
    memo = _document(
        [
            _heading("Quarterly Incident Review", page=1),
            _heading("Quarterly Incident Review", page=2),
        ],
        pages=2,
    )

    assert _running_headers(memo) == frozenset()
    assert _derive_title(memo, "memo.pdf") == "Quarterly Incident Review"


def test_a_document_that_is_all_running_header_keeps_its_own_words() -> None:
    """The fallback is the first heading, not the file name: a repeated heading is still
    the document's own words, and is strictly no worse than what this returned before."""
    every_page = _document(
        [_heading("Federal Register", page=n) for n in range(1, 5)],
        pages=4,
    )

    assert _derive_title(every_page, "fr-notice.pdf") == "Federal Register"


def test_a_document_with_no_headings_falls_back_to_the_tenant_s_file_name() -> None:
    """Unchanged behaviour, asserted so the rewrite cannot have dropped it. The stem of
    the row's file name — never ``source_name``, which is a SHA-256 in the store."""
    assert _derive_title(_document([], pages=3), "vendor-terms.pdf") == "vendor-terms"
