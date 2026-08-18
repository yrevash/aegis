"""Running headers, footers and page numbers — the ones that must go, and the ones that must not."""

from __future__ import annotations

from aegis.ingestion import (
    BBox,
    BlockKind,
    ParsedBlock,
    ParsedPage,
    normalise_furniture_text,
    strip_running_furniture,
)

PAGE_HEIGHT = 792.0
PAGE_WIDTH = 612.0


def pages(count):
    return [
        ParsedPage(
            page_no=n,
            width=PAGE_WIDTH,
            height=PAGE_HEIGHT,
            char_count=2000,
            has_text_layer=True,
        )
        for n in range(1, count + 1)
    ]


def header(text, page):
    return ParsedBlock(
        kind=BlockKind.TEXT, text=text, page_no=page, bbox=BBox(72.0, 20.0, 540.0, 34.0)
    )


def footer(text, page):
    return ParsedBlock(
        kind=BlockKind.TEXT,
        text=text,
        page_no=page,
        bbox=BBox(72.0, PAGE_HEIGHT - 34.0, 540.0, PAGE_HEIGHT - 20.0),
    )


def body(text, page):
    return ParsedBlock(
        kind=BlockKind.TEXT, text=text, page_no=page, bbox=BBox(72.0, 200.0, 540.0, 400.0)
    )


def test_a_running_header_is_removed_from_every_page():
    blocks = []
    for page in range(1, 11):
        blocks += [header("2025 Instructions for Form 1040", page), body(f"content {page}", page)]

    kept, runs = strip_running_furniture(blocks, pages(10))

    assert [b.text for b in kept] == [f"content {page}" for page in range(1, 11)]
    assert len(runs) == 1
    assert runs[0].band == "header"
    assert runs[0].pages == tuple(range(1, 11))
    assert runs[0].sample == "2025 Instructions for Form 1040"


def test_page_numbers_are_one_pattern_because_digits_collapse():
    # "Page 1" .. "Page 10" are ten distinct strings and one running footer.
    blocks = []
    for page in range(1, 11):
        blocks += [body(f"content {page}", page), footer(f"Page {page}", page)]

    kept, runs = strip_running_furniture(blocks, pages(10))

    assert all(b.text.startswith("content") for b in kept)
    assert [r.pattern for r in runs] == ["page #"]
    assert runs[0].band == "footer"


def test_a_heading_at_the_top_of_its_page_survives():
    # Position alone is not evidence: this is a real section heading, and it happens once.
    blocks = [body(f"content {page}", page) for page in range(1, 11)]
    blocks.append(
        ParsedBlock(
            kind=BlockKind.HEADING,
            text="4. Findings",
            page_no=4,
            bbox=BBox(72.0, 20.0, 300.0, 34.0),
            level=1,
        )
    )

    kept, runs = strip_running_furniture(blocks, pages(10))

    assert "4. Findings" in [b.text for b in kept]
    assert runs == ()


def test_repeated_body_text_is_not_furniture():
    # Same text, same repetition, middle of the page: a boilerplate sentence is content.
    blocks = [body("Refer to the instructions.", page) for page in range(1, 11)]

    kept, runs = strip_running_furniture(blocks, pages(10))

    assert len(kept) == 10
    assert runs == ()


def test_a_pattern_on_too_few_pages_is_left_alone():
    # Three of forty pages is a recurring label, not a running head.
    blocks = [body(f"content {page}", page) for page in range(1, 41)]
    blocks += [header("Continued", page) for page in (3, 4, 5)]

    kept, runs = strip_running_furniture(blocks, pages(40))

    assert sum(1 for b in kept if b.text == "Continued") == 3
    assert runs == ()


def test_parser_labelled_furniture_goes_without_needing_repetition():
    # When the parser does classify it, that is authoritative — even on a two-page document.
    blocks = [
        ParsedBlock(
            kind=BlockKind.PAGE_FOOTER,
            text="Confidential — 1",
            page_no=1,
            bbox=BBox(72.0, 700.0, 540.0, 720.0),
        ),
        body("content 1", 1),
        body("content 2", 2),
    ]

    kept, runs = strip_running_furniture(blocks, pages(2))

    assert [b.text for b in kept] == ["content 1", "content 2"]
    assert [(r.band, r.pattern) for r in runs] == [("footer", "confidential — #")]


def test_short_documents_cannot_establish_a_run():
    blocks = [header("Quarterly Report", page) for page in (1, 2)]

    kept, runs = strip_running_furniture(blocks, pages(2))

    assert len(kept) == 2
    assert runs == ()


def test_normalisation_makes_two_pages_of_the_same_header_one_pattern():
    assert normalise_furniture_text("— Page 7 —") == normalise_furniture_text("Page 8")
    assert normalise_furniture_text("  U.S. Census Bureau\n") == "u.s. census bureau"
    assert normalise_furniture_text("...") == ""
