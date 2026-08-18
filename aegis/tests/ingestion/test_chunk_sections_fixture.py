"""`chunk_sections` over a real Docling parse — the provenance half of tasks 4.3/4.4.

The unit tests in ``aegis/tests/retrieval/test_chunk_sections.py`` build blocks by hand,
which proves the adapter's rules. They cannot prove it reads a *real* tree: that the
heading paths Docling produces group into sections, that the boxes it reports survive
packing, and that a chunk's page is the page its words are printed on. That needs a PDF,
so it is here — beside the seam, behind the same ``docling`` import guard.

``transformer-single-column.pdf`` is the single-column control (15 pages, ~7 s). The two
big fixtures are gated behind ``AEGIS_DOCLING_SLOW_FIXTURES``; nothing here needs them.
"""

from __future__ import annotations

from datetime import date

import pytest

from aegis.ingestion import BlockKind, ParsedDocument
from aegis.retrieval.chunker import DocumentContext, chunk_prefix, chunk_sections

pytest.importorskip("docling", reason="the 'ingestion' extra is not installed")

#: Blocks that contribute no body text, mirroring the chunker's own rule.
_NON_BODY = {BlockKind.HEADING, BlockKind.PAGE_HEADER, BlockKind.PAGE_FOOTER}


@pytest.fixture(scope="module")
def transformer(parsed_transformer) -> ParsedDocument:
    """One real 15-page single-column parse, reused across this module."""
    return parsed_transformer


@pytest.fixture(scope="module")
def chunks(transformer):
    """The document's chunks, with every prefix field supplied by the caller."""
    return chunk_sections(
        transformer,
        context=DocumentContext(
            title=DocumentContext.from_parsed(transformer).title,
            doc_type="research paper",
            doc_date=date(2017, 6, 12),
        ),
        chunk_size=400,
        overlap=60,
    )


@pytest.fixture(scope="module")
def body_blocks(transformer):
    """Blocks whose text identifies exactly one block, for matching text back to a box.

    The filter is not defensive tidiness. Docling's Markdown export of a table **opens
    with that table's caption**, so on this fixture the caption block on page 9 ("Table 3:
    Variations on the Transformer architecture…", box at the top of the page) has text
    that is also a prefix of the table block below it — and "which block is this text
    from" has two right answers, in two different chunks. Matching on such a block would
    assert one block's box against the other block's chunk. Blocks whose text appears
    inside another block's text are therefore skipped — 122 of this document's 126 body
    blocks survive that filter, which is what makes the assertion below worth making.
    """
    candidates = [
        block
        for block in transformer.blocks
        if block.kind not in _NON_BODY and len(block.text.split()) > 6
    ]
    texts = [block.text for block in transformer.blocks]
    return [
        block
        for block in candidates
        if sum(1 for text in texts if block.text in text) == 1
    ]


def test_a_real_parse_produces_chunks_at_all(chunks):
    assert len(chunks) > 10
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_every_chunk_carries_the_page_and_box_of_its_own_blocks(transformer, chunks, body_blocks):
    """Each chunk's spans cover the pages of the blocks whose text it holds.

    Checked against the blocks themselves rather than against a stored expectation: a
    block whose whole text sits inside a chunk *is* one of that chunk's blocks, so its
    page must appear in the chunk's spans and its box must sit inside that page's span
    box. This is the property citation rendering (4.12) and the verbatim span check
    (4.14) both read.
    """
    assert body_blocks

    matched = 0
    for chunk in chunks:
        assert chunk.spans, f"chunk {chunk.ordinal} lost its provenance"
        boxes = {span.page_no: span.bbox for span in chunk.spans}
        for span in chunk.spans:
            assert 1 <= span.page_no <= transformer.page_count
        for block in body_blocks:
            if block.text not in chunk.text:
                continue
            matched += 1
            assert block.page_no in boxes, (
                f"chunk {chunk.ordinal} holds a block printed on page {block.page_no} "
                f"but cites pages {sorted(boxes)}"
            )
            if block.bbox is not None:
                page_box = boxes[block.page_no]
                assert page_box is not None
                assert page_box.left <= block.bbox.left + 0.01
                assert page_box.top <= block.bbox.top + 0.01
                assert page_box.right >= block.bbox.right - 0.01
                assert page_box.bottom >= block.bbox.bottom - 0.01

    # The loop above asserts nothing if it never matches. On this fixture the 122 body
    # blocks land in chunks 127 times (a few appear twice, in an overlap tail as well as
    # in their own chunk); the floor is set well below that so a Docling-side change of
    # reading order fails the box assertions rather than this one.
    assert matched > 100


def test_the_first_page_of_a_chunk_is_the_page_its_first_words_are_on(transformer, chunks):
    first_block = next(
        block for block in transformer.blocks if block.kind not in _NON_BODY and block.text.strip()
    )
    opener = next(chunk for chunk in chunks if first_block.text[:40] in chunk.text)

    assert opener.page_no == first_block.page_no
    assert opener.bbox is not None


def test_every_chunk_is_prefixed_with_all_four_fields(transformer, chunks):
    title = DocumentContext.from_parsed(transformer).title
    for chunk in chunks:
        assert chunk.prefix.count(" · ") == 3
        assert chunk.prefix.startswith(f"[{title[:20]}")
        assert " · research paper · 2017-06-12 · " in chunk.prefix
        assert chunk.contextualized() == f"{chunk.prefix}\n{chunk.text}"


def test_the_derived_title_is_the_papers_own_title(transformer):
    assert DocumentContext.from_parsed(transformer).title == "Attention Is All You Need"


def test_a_document_with_no_type_or_date_still_gets_a_four_field_prefix(transformer):
    # What the pipeline produces before an upload route supplies the documents row.
    bare = chunk_sections(transformer, chunk_size=400, overlap=60)

    assert bare
    for chunk in bare:
        assert chunk.prefix.count(" · ") == 3
        assert " · untyped · undated · " in chunk.prefix
    # …and the shape is identical to the fully-populated one, which is the whole point.
    assert len({c.prefix.count(" · ") for c in bare}) == 1


def test_no_chunk_spans_two_sections_of_the_real_document(chunks, body_blocks):
    sections = {chunk.section for chunk in chunks}
    assert len(sections) > 3, f"only {sections} — the heading tree did not survive"

    for chunk in chunks:
        for block in body_blocks:
            if block.text in chunk.text:
                assert " > ".join(block.heading_path) == chunk.section, (
                    f"chunk {chunk.ordinal} is labelled {chunk.section!r} but holds text "
                    f"from {' > '.join(block.heading_path)!r}"
                )


def test_the_chunks_cover_the_documents_body_text(transformer, chunks):
    packed = set()
    for chunk in chunks:
        packed.update(chunk.text.split())
    for block in transformer.blocks:
        if block.kind in _NON_BODY:
            continue
        missing = set(block.text.split()) - packed
        assert not missing, f"words dropped from page {block.page_no}: {sorted(missing)[:5]}"


def test_the_prefix_is_the_same_string_the_helper_builds(chunks):
    context = DocumentContext(
        title="Attention Is All You Need",
        doc_type="research paper",
        doc_date=date(2017, 6, 12),
    )
    for chunk in chunks:
        assert chunk.prefix == chunk_prefix(context, chunk.section)
