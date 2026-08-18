"""The parsed-document vocabulary: provenance, and the histogram the D2 gate reads."""

from __future__ import annotations

import pytest

from aegis.ingestion import BBox, BlockKind, OcrDecision, ParsedBlock, ParsedDocument, ParsedPage


def block(text, *, kind=BlockKind.TEXT, page=1, level=None):
    return ParsedBlock(
        kind=kind, text=text, page_no=page, bbox=BBox(0, 0, 10, 10), level=level
    )


def document(blocks, pages=1):
    return ParsedDocument(
        source_name="x.pdf",
        pages=tuple(
            ParsedPage(page_no=n, width=612.0, height=792.0, char_count=500, has_text_layer=True)
            for n in range(1, pages + 1)
        ),
        blocks=tuple(blocks),
        ocr=OcrDecision(enabled=False, reason="test"),
    )


def test_bbox_rejects_a_bottom_left_origin_rectangle():
    # top > bottom is what a PDF-native box looks like if it reaches us unconverted.
    with pytest.raises(ValueError, match="inside-out"):
        BBox(left=10.0, top=770.0, right=480.0, bottom=741.0)


def test_bbox_merge_covers_both_rectangles():
    merged = BBox(10, 20, 30, 40).merge(BBox(5, 25, 25, 60))
    assert (merged.left, merged.top, merged.right, merged.bottom) == (5, 20, 30, 60)
    assert (merged.width, merged.height) == (25, 40)


def test_heading_histogram_reports_every_level_present():
    doc = document(
        [
            block("Title", kind=BlockKind.HEADING, level=1),
            block("Section", kind=BlockKind.HEADING, level=2),
            block("Sub", kind=BlockKind.HEADING, level=3),
            block("Sub two", kind=BlockKind.HEADING, level=3),
            block("body text"),
        ]
    )
    assert doc.heading_histogram == {1: 1, 2: 1, 3: 2}


def test_heading_histogram_shows_the_flat_failure_it_exists_to_catch():
    # Every heading at level 1 is what Docling produces with the hierarchy off (D2).
    doc = document([block(f"H{i}", kind=BlockKind.HEADING, level=1) for i in range(20)])
    assert doc.heading_histogram == {1: 20}


def test_table_count_and_text_read_only_what_has_content():
    doc = document(
        [
            block("| a | b |", kind=BlockKind.TABLE),
            block("prose"),
            block("", kind=BlockKind.OTHER),
        ]
    )
    assert doc.table_count == 1
    assert doc.text() == "| a | b |\n\nprose"
