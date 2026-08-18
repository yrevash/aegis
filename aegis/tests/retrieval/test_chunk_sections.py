"""The parsed-document chunking path: provenance, section boundaries and the D7 prefix.

Synthetic blocks rather than a parse, so these run on a machine with no PDF stack. The
same assertions against a real Docling parse live in
``aegis/tests/ingestion/test_chunk_sections_fixture.py``; both exist because a fixture
proves the adapter reads a real tree and unit tests prove the degenerate cases a real
document happens not to contain.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from aegis.ingestion import BBox, BlockKind, OcrDecision, ParsedBlock, ParsedDocument, ParsedPage
from aegis.ingestion.tables import table_digest
from aegis.retrieval.chunker import (
    ChunkPiece,
    DocumentContext,
    SectionChunk,
    chunk_prefix,
    chunk_sections,
    chunk_structured,
    dedup_pieces,
)

PAGE_HEIGHT = 792.0
PAGE_WIDTH = 612.0


def _pages(count: int) -> tuple[ParsedPage, ...]:
    return tuple(
        ParsedPage(
            page_no=n,
            width=PAGE_WIDTH,
            height=PAGE_HEIGHT,
            char_count=2000,
            has_text_layer=True,
        )
        for n in range(1, count + 1)
    )


def _heading(text: str, page: int, *, level: int = 1, path: tuple[str, ...] = ()) -> ParsedBlock:
    return ParsedBlock(
        kind=BlockKind.HEADING,
        text=text,
        page_no=page,
        bbox=BBox(72.0, 100.0, 540.0, 120.0),
        level=level,
        heading_path=path,
    )


def _text(
    text: str,
    page: int,
    *,
    path: tuple[str, ...] = (),
    top: float = 200.0,
    kind: BlockKind = BlockKind.TEXT,
) -> ParsedBlock:
    return ParsedBlock(
        kind=kind,
        text=text,
        page_no=page,
        bbox=BBox(72.0, top, 540.0, top + 40.0),
        heading_path=path,
    )


def _document(blocks: list[ParsedBlock], *, name: str = "policy.pdf") -> ParsedDocument:
    pages = _pages(max((b.page_no for b in blocks), default=1))
    return ParsedDocument(
        source_name=name,
        pages=pages,
        blocks=tuple(blocks),
        ocr=OcrDecision(enabled=False, reason="synthetic fixture"),
        parser="test",
    )


# ── the D7 prefix ────────────────────────────────────────────────────────────


def test_prefix_carries_all_four_fields_when_all_four_are_present():
    prefix = chunk_prefix(
        DocumentContext(title="Returns policy", doc_type="policy", doc_date=date(2024, 3, 1)),
        "Returns > Refund window",
    )

    assert prefix == "[Returns policy · policy · 2024-03-01 · Returns > Refund window]"


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        (
            DocumentContext(doc_type="policy", doc_date=date(2024, 3, 1)),
            "[untitled · policy · 2024-03-01 · Returns]",
        ),
        (
            DocumentContext(title="Returns policy", doc_type="policy"),
            "[Returns policy · policy · undated · Returns]",
        ),
        (
            DocumentContext(title="Returns policy", doc_date=date(2024, 3, 1)),
            "[Returns policy · untyped · 2024-03-01 · Returns]",
        ),
        (DocumentContext(), "[untitled · untyped · undated · Returns]"),
    ],
)
def test_a_missing_field_degrades_to_a_placeholder_not_to_a_gap(context, expected):
    # The failure this guards is `[ ·  · 2024-03-01 · Returns]` and its twin
    # `[2024-03-01 · Returns]`: both are read by the embedding model, and neither is
    # comparable with a chunk from a document that carried every field.
    assert chunk_prefix(context, "Returns") == expected


def test_the_prefix_shape_is_the_same_whatever_is_missing():
    shapes = {
        chunk_prefix(context, "Returns").count(" · ")
        for context in (
            DocumentContext(title="T", doc_type="policy", doc_date=date(2024, 3, 1)),
            DocumentContext(title="T"),
            DocumentContext(doc_date=date(2024, 3, 1)),
            DocumentContext(),
        )
    }

    assert shapes == {3}


def test_text_before_the_first_heading_still_gets_a_four_field_prefix():
    assert chunk_prefix(DocumentContext(title="T", doc_type="memo"), "") == (
        "[T · memo · undated · unsectioned]"
    )


def test_a_datetime_is_narrowed_to_its_date():
    # An upload timestamp would otherwise put "T09:14:05" into every embedded chunk.
    prefix = chunk_prefix(
        DocumentContext(title="T", doc_date=datetime(2024, 3, 1, 9, 14, 5)), "S"
    )

    assert "2024-03-01" in prefix
    assert "T09" not in prefix


def test_a_title_cannot_forge_a_field_boundary():
    prefix = chunk_prefix(DocumentContext(title="Q3 · results] and more"), "S")

    # Exactly four fields survive: the title's own punctuation is neutralised rather than
    # read back as structure by a citation renderer.
    assert prefix.count(" · ") == 3
    assert prefix == "[Q3 - results) and more · untyped · undated · S]"


def test_a_runaway_title_is_capped_rather_than_charged_to_every_chunk():
    prefix = chunk_prefix(DocumentContext(title=" ".join(f"w{i}" for i in range(200))), "S")

    assert prefix.count(" · ") == 3
    assert len(prefix) < 220
    assert "…" in prefix


def test_a_multiline_title_is_collapsed_to_one_line():
    prefix = chunk_prefix(DocumentContext(title="BERT:\nPre-training  of\tDeep"), "S")

    assert prefix.startswith("[BERT: Pre-training of Deep · ")
    assert "\n" not in prefix


# ── chunking a parsed document ───────────────────────────────────────────────


def test_a_chunk_carries_the_page_and_bbox_of_the_blocks_it_came_from():
    blocks = [
        _heading("Returns", 3),
        _text("Refunds are issued within ten working days.", 3, path=("Returns",), top=140.0),
        _text("Proof of purchase is required.", 3, path=("Returns",), top=220.0),
    ]

    chunks = chunk_sections(_document(blocks), chunk_size=400, overlap=60)

    assert len(chunks) == 1
    chunk = chunks[0]
    assert [span.page_no for span in chunk.spans] == [3]
    assert chunk.page_no == 3
    # The union of both paragraphs' boxes — not the first one's, which would highlight
    # half of what the citation quotes.
    assert chunk.bbox == BBox(72.0, 140.0, 540.0, 260.0)


def test_a_chunk_that_straddles_a_page_break_reports_both_pages():
    blocks = [
        _heading("Returns", 4),
        _text("first paragraph on page four", 4, path=("Returns",), top=600.0),
        _text("continuation on page five", 5, path=("Returns",), top=90.0),
    ]

    chunks = chunk_sections(_document(blocks), chunk_size=400, overlap=60)

    assert [span.page_no for span in chunks[0].spans] == [4, 5]


def test_the_overlap_tail_keeps_the_page_it_was_printed_on():
    # The last unit of chunk 0 sits on page 1; chunk 1 opens with its trailing words, so
    # a chunk whose first sentence is printed on page 1 must cite page 1 as well as 2.
    blocks = [
        _heading("Returns", 1),
        _text(" ".join(f"a{i}" for i in range(30)), 1, path=("Returns",)),
        _text(" ".join(f"b{i}" for i in range(30)), 1, path=("Returns",), top=300.0),
        _text(" ".join(f"c{i}" for i in range(30)), 2, path=("Returns",)),
    ]

    chunks = chunk_sections(_document(blocks), chunk_size=40, overlap=10)

    # The last chunk is the page-2 paragraph seeded with the tail of the page-1 one.
    assert chunks[-1].text.split()[:10] == [f"b{i}" for i in range(20, 30)]
    assert [span.page_no for span in chunks[-1].spans] == [1, 2]
    # …and the chunk before it, whose carried tail is also page 1, stays on page 1 only.
    assert [span.page_no for span in chunks[-2].spans] == [1]


def test_a_tail_that_reaches_back_two_chunks_keeps_both_pages():
    # A window whose new content is shorter than the overlap width carries words from
    # two chunks back: with 12-word blocks, size 20 and overlap 15, the third chunk opens
    # with 3 words from page 1 and 12 from page 2. Walking only the units the previous
    # chunk *introduced* would stop at page 2 and lose the page its first three words are
    # printed on.
    blocks = [_heading("S", 1)] + [
        _text(" ".join(f"p{page}w{i}" for i in range(12)), page, path=("S",))
        for page in (1, 2, 3)
    ]

    chunks = chunk_sections(_document(blocks), chunk_size=20, overlap=15)

    assert chunks[-1].text.split()[0] == "p1w9"
    assert [span.page_no for span in chunks[-1].spans] == [1, 2, 3]


def test_a_block_with_no_provenance_is_recorded_as_none_not_guessed():
    blocks = [
        _heading("Returns", 2),
        ParsedBlock(
            kind=BlockKind.TEXT,
            text="a paragraph Docling gave us no rectangle for",
            page_no=2,
            bbox=None,
            heading_path=("Returns",),
        ),
    ]

    chunks = chunk_sections(_document(blocks))

    assert chunks[0].spans == (chunks[0].spans[0],)
    assert chunks[0].page_no == 2
    assert chunks[0].bbox is None


def test_a_chunk_never_spans_two_top_level_sections():
    blocks = [
        _heading("Returns", 1),
        _text("refunds within ten days", 1, path=("Returns",)),
        _heading("Escalation", 1),
        _text("escalate to tier two", 1, path=("Escalation",)),
    ]

    # A chunk_size far larger than the whole document: only the section boundary can
    # stop these two paragraphs being packed together.
    chunks = chunk_sections(_document(blocks), chunk_size=400, overlap=60)

    assert [c.section for c in chunks] == ["Returns", "Escalation"]
    assert "escalate" not in chunks[0].text
    assert "refunds" not in chunks[1].text


def test_two_sibling_sections_sharing_a_title_are_not_merged():
    # Same heading_path string, different sections. Grouping by path alone would pack
    # them into one chunk and answer a question about the first with the second's text.
    blocks = [
        _heading("Notes", 1),
        _text("first notes body", 1, path=("Notes",)),
        _heading("Notes", 1),
        _text("second notes body", 1, path=("Notes",)),
    ]

    chunks = chunk_sections(_document(blocks), chunk_size=400, overlap=60)

    assert len(chunks) == 2
    assert chunks[0].text == "first notes body"
    assert chunks[1].text == "second notes body"


def test_the_nested_heading_path_reaches_the_prefix():
    blocks = [
        _heading("Returns", 1),
        _heading("Refund window", 1, level=2, path=("Returns",)),
        _text("Fourteen days from delivery.", 1, path=("Returns", "Refund window")),
    ]

    chunks = chunk_sections(
        _document(blocks), context=DocumentContext(title="Policy", doc_type="policy")
    )

    assert chunks[0].section == "Returns > Refund window"
    assert chunks[0].contextualized() == (
        "[Policy · policy · undated · Returns > Refund window]\nFourteen days from delivery."
    )


def test_headings_and_furniture_contribute_no_body_text():
    blocks = [
        ParsedBlock(kind=BlockKind.PAGE_HEADER, text="Form 1040 (2024)", page_no=1),
        _heading("Returns", 1),
        _text("body text", 1, path=("Returns",)),
        ParsedBlock(kind=BlockKind.PAGE_FOOTER, text="page 1", page_no=1),
    ]

    chunks = chunk_sections(_document(blocks))

    assert [c.text for c in chunks] == ["body text"]


def test_a_table_is_never_split_into_sentences():
    grid = "\n".join(
        ["| year | filers |", "| --- | --- |"]
        + [f"| 20{n:02d} | {n} thousand returns filed |" for n in range(40)]
    )
    blocks = [
        _heading("Tables", 1),
        ParsedBlock(
            kind=BlockKind.TABLE,
            text=grid,
            page_no=1,
            bbox=BBox(72.0, 100.0, 540.0, 700.0),
            heading_path=("Tables",),
            table_shape=(41, 2),
        ),
    ]

    chunks = chunk_sections(_document(blocks), chunk_size=40, overlap=10)

    assert len(chunks) == 1
    assert chunks[0].text == grid


def test_ordinals_are_contiguous_and_word_spans_locate_the_chunk():
    blocks = [_heading("S", 1)]
    for n in range(6):
        blocks.append(_text(" ".join(f"w{n}_{i}" for i in range(60)), 1, path=("S",)))
    body = " ".join(b.text for b in blocks if b.kind is not BlockKind.HEADING).split()

    chunks = chunk_sections(_document(blocks), chunk_size=100, overlap=20)

    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    for chunk in chunks:
        assert chunk.word_start + chunk.word_count <= len(body)
        assert body[chunk.word_start : chunk.word_start + chunk.word_count] == chunk.text.split()


def test_an_empty_document_produces_no_chunks():
    assert chunk_sections(_document([])) == []


def test_chunk_sections_rejects_bad_params():
    document = _document([_text("a b c", 1)])
    with pytest.raises(ValueError):
        chunk_sections(document, chunk_size=0)
    with pytest.raises(ValueError):
        chunk_sections(document, chunk_size=3, overlap=3)


# ── the derived context ──────────────────────────────────────────────────────


def test_the_title_defaults_to_the_documents_first_heading():
    blocks = [_heading("Attention Is All You Need", 1), _text("we propose", 1, path=("A",))]

    context = DocumentContext.from_parsed(_document(blocks))

    assert context.title == "Attention Is All You Need"
    assert context.doc_type == ""
    assert context.doc_date is None


def test_a_document_with_no_heading_falls_back_to_its_file_name():
    document = _document([_text("just a paragraph", 1)], name="q3-board-pack.pdf")

    assert DocumentContext.from_parsed(document).title == "q3-board-pack"


def test_the_derived_context_still_produces_a_full_prefix():
    blocks = [_heading("Attention Is All You Need", 1), _text("we propose", 1, path=("A",))]

    chunks = chunk_sections(_document(blocks))

    assert chunks[0].contextualized().startswith(
        "[Attention Is All You Need · untyped · undated · A]"
    )


# ── the seam with the rest of the chunker ────────────────────────────────────


def test_a_section_chunk_is_a_chunk_piece_and_dedups_like_one():
    blocks = [
        _heading("Returns", 1),
        _text("Contact support.", 1, path=("Returns",)),
        _heading("Escalation", 1),
        _text("Contact support.", 1, path=("Escalation",)),
        _heading("Closures", 1),
        _text("Contact support.", 1, path=("Returns",)),
    ]

    chunks = chunk_sections(_document(blocks))
    assert all(isinstance(chunk, (ChunkPiece, SectionChunk)) for chunk in chunks)

    result = dedup_pieces(chunks)

    # Identical bodies under different sections survive; the repeat under the same
    # section path does not — exactly as for a ChunkPiece, because the id hashes the
    # contextualized text and the prefix differs only by section here.
    assert [c.section for c in result.kept] == ["Returns", "Escalation"]
    assert result.exact_duplicates == 1


def test_the_prefix_is_part_of_the_content_id():
    blocks = [_heading("Returns", 1), _text("Refunds take ten days.", 1, path=("Returns",))]
    document = _document(blocks)

    dated = chunk_sections(document, context=DocumentContext(title="P", doc_date=date(2024, 1, 1)))
    undated = chunk_sections(document, context=DocumentContext(title="P"))

    assert dated[0].text == undated[0].text
    assert dated[0].content_id() != undated[0].content_id()


def test_the_markdown_path_is_untouched_by_the_parsed_path():
    """The existing chunker's behaviour is unchanged for inputs that are not blocks.

    ``_pack_units`` grew a third field so the parsed path can find its blocks again;
    this asserts the packing itself did not move — same chunks, same sections, same word
    spans, same heading-path-only prefix, and still no prefix at all for text with no
    headings.

    The expected values are not derived from the current implementation: they were read
    off the chunker as it stood at 373262c, before this task touched it, and pasted here.
    A test that recomputes them from the code under test proves nothing about a change.
    """
    document = (
        "# Closures\n\n"
        + " ".join(f"c{i}" for i in range(120))
        + "\n\n## Duplicate requests\n\n"
        + " ".join(f"d{i}" for i in range(90))
        + "\n\n# Escalation\n\n"
        + " ".join(f"e{i}" for i in range(50))
    )

    pieces = chunk_structured(document, chunk_size=50, overlap=10)

    assert [p.ordinal for p in pieces] == list(range(len(pieces)))
    assert [p.section for p in pieces] == [
        "Closures",
        "Closures",
        "Closures",
        "Closures > Duplicate requests",
        "Closures > Duplicate requests",
        "Escalation",
    ]
    assert [p.word_start for p in pieces] == [0, 40, 90, 120, 160, 210]
    assert [p.word_count for p in pieces] == [50, 60, 30, 50, 50, 50]
    assert [p.text.split()[:2] for p in pieces] == [
        ["c0", "c1"],
        ["c40", "c41"],
        ["c90", "c91"],
        ["d0", "d1"],
        ["d40", "d41"],
        ["e0", "e1"],
    ]
    assert pieces[0].contextualized() == f"[Closures]\n{pieces[0].text}"
    assert type(pieces[0]) is ChunkPiece

    plain = chunk_structured("Just a plain sentence with no markdown headings at all.")
    assert plain[0].contextualized() == plain[0].text


# ── D8 / task 4.10: a table is a first-class chunk ───────────────────────────


_TABLE_MARKDOWN = """Table 2: BLEU scores on newstest2014.

| Model       | EN-DE   | EN-FR   |
|-------------|---------|---------|
| ByteNet     | 23.75   |         |
| GNMT + RL   | 24.6    | 39.92   |
| Transformer | 28.4    | 41.8    |"""


def _table(
    page: int = 1,
    *,
    text: str = _TABLE_MARKDOWN,
    shape: tuple[int, int] | None = (5, 3),
    path: tuple[str, ...] = ("Results",),
) -> ParsedBlock:
    return ParsedBlock(
        kind=BlockKind.TABLE,
        text=text,
        page_no=page,
        bbox=BBox(72.0, 300.0, 540.0, 500.0),
        heading_path=path,
        table_shape=shape,
    )


def test_a_table_becomes_its_own_chunk_carrying_its_shape_and_caption():
    """The whole of D8's "first-class object": shape and caption, on the chunk.

    Without this a consumer can only recover "is this a table" by counting pipes in the
    text, and can only recover the shape by trusting that count — on exactly the merged
    and nested headers TableFormer is running on ACCURATE (D3b) to get right.
    """
    blocks = [
        _heading("Results", 1),
        _text("The Transformer outperforms every previously reported model.", 1, path=("Results",)),
        _table(1),
        _text("Training took twelve hours on eight GPUs.", 1, path=("Results",)),
    ]

    chunks = chunk_sections(_document(blocks))

    tables = [chunk for chunk in chunks if chunk.table is not None]
    assert len(tables) == 1
    table = tables[0]
    assert table.table.rows == 5
    assert table.table.cols == 3
    assert table.table.caption == "Table 2: BLEU scores on newstest2014."
    assert table.table.digest == table_digest(_TABLE_MARKDOWN)


def test_a_table_is_never_packed_together_with_the_prose_around_it():
    """A chunk that is half a table cannot be labelled one, and cites two places at once.

    All three blocks here fit inside one 400-word window, so before task 4.10 they were
    one chunk: the paragraph, the grid and the next paragraph, sharing a citation.
    """
    before = "The Transformer outperforms every previously reported model."
    after = "Training took twelve hours on eight GPUs."
    blocks = [
        _heading("Results", 1),
        _text(before, 1, path=("Results",)),
        _table(1),
        _text(after, 1, path=("Results",)),
    ]

    chunks = chunk_sections(_document(blocks))

    assert [chunk.text for chunk in chunks] == [before, _TABLE_MARKDOWN, after]
    assert [chunk.table is not None for chunk in chunks] == [False, True, False]
    assert [chunk.ordinal for chunk in chunks] == [0, 1, 2]


def test_a_table_seeds_no_overlap_into_the_chunk_that_follows_it():
    """Otherwise the next prose chunk opens with rows of numbers it does not contain."""
    tail = " ".join(f"w{n}" for n in range(200))
    blocks = [
        _heading("Results", 1),
        _table(1),
        _text(tail, 1, path=("Results",)),
    ]

    chunks = chunk_sections(_document(blocks), chunk_size=100, overlap=30)

    assert chunks[0].text == _TABLE_MARKDOWN
    assert chunks[1].text.startswith("w0 w1")
    assert "|" not in chunks[1].text


def test_the_grid_itself_is_what_the_table_chunk_holds():
    """The summary is added downstream, in front of this. It never replaces it."""
    chunks = chunk_sections(_document([_heading("Results", 1), _table(1)]))

    assert chunks[0].text == _TABLE_MARKDOWN
    assert "| Transformer | 28.4    | 41.8    |" in chunks[0].text


def test_two_consecutive_tables_are_two_chunks_and_two_digests():
    other = _TABLE_MARKDOWN.replace("Table 2", "Table 3").replace("28.4", "26.4")
    blocks = [
        _heading("Results", 1),
        _table(1),
        _table(2, text=other),
    ]

    chunks = chunk_sections(_document(blocks))

    assert len(chunks) == 2
    assert chunks[0].table.digest != chunks[1].table.digest
    assert [chunk.page_no for chunk in chunks] == [1, 2]


def test_the_same_table_in_two_places_shares_one_digest():
    """The cache key is the table, not the position — this is what makes it hit."""
    blocks = [
        _heading("Results", 1),
        _table(1),
        _heading("Appendix", 2),
        _table(2, path=("Appendix",)),
    ]

    chunks = chunk_sections(_document(blocks))

    assert len(chunks) == 2
    assert chunks[0].section != chunks[1].section
    assert chunks[0].table.digest == chunks[1].table.digest


def test_a_table_block_with_no_reported_shape_records_zero_rather_than_a_guess():
    """A shape counted back out of the Markdown would be wrong exactly where it matters."""
    chunks = chunk_sections(_document([_heading("Results", 1), _table(1, shape=None)]))

    assert chunks[0].table.rows == 0
    assert chunks[0].table.cols == 0


def test_a_prose_chunk_carries_no_table_reference_at_all():
    blocks = [_heading("Intro", 1), _text("Plain prose, no grid in sight.", 1, path=("Intro",))]

    chunks = chunk_sections(_document(blocks))

    assert chunks[0].table is None


def test_a_table_chunk_keeps_its_own_page_and_box():
    """A table isolated into its own chunk must not lose the provenance that cites it."""
    blocks = [
        _heading("Results", 1),
        _text("Prose on page one.", 1, path=("Results",)),
        _table(2),
    ]

    chunks = chunk_sections(_document(blocks))

    table = next(chunk for chunk in chunks if chunk.table is not None)
    assert [span.page_no for span in table.spans] == [2]
    assert table.bbox == BBox(72.0, 300.0, 540.0, 500.0)
