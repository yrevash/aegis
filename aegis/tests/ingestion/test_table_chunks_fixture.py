"""Real tables out of a real parse, and what a chunk carries about them (D8 / task 4.10).

``aegis/tests/retrieval/test_chunk_sections.py`` proves the rules against blocks built by
hand. It cannot prove the rules hold over a table TableFormer actually produced — one with
merged header cells, a caption that wraps across three printed lines, and a body that runs
9,600 characters. That needs a PDF, so it is here, beside the seam and behind the same
``docling`` import guard.

Both small fixtures are used, because they disagree in a useful way: the single-column
control has four tables and the two-column paper has eight, and D3b's ``ACCURATE``
TableFormer is what is being trusted for the shapes asserted below. The 67- and 126-page
fixtures are minutes of parsing each and are gated behind ``AEGIS_DOCLING_SLOW_FIXTURES``;
their table shapes are recorded in the phase notes rather than re-derived on every run.
"""

from __future__ import annotations

import pytest

from aegis.ingestion import BlockKind, ParsedDocument
from aegis.ingestion.tables import DEFAULT_TABLE_SUMMARY_POLICY, table_digest
from aegis.retrieval.chunker import chunk_sections

pytest.importorskip("docling", reason="the 'ingestion' extra is not installed")


@pytest.fixture(scope="module")
def transformer_chunks(parsed_transformer):
    """The 15-page control paper, chunked the way the ingest stage chunks it."""
    return chunk_sections(parsed_transformer, chunk_size=400, overlap=60)


@pytest.fixture(scope="module")
def bert_chunks(parsed_bert):
    """The 16-page two-column paper, chunked the same way."""
    return chunk_sections(parsed_bert, chunk_size=400, overlap=60)


def _table_blocks(document: ParsedDocument):
    """Every table block the parse produced, in reading order."""
    return [block for block in document.blocks if block.kind is BlockKind.TABLE]


def test_every_table_in_the_paper_becomes_exactly_one_table_chunk(
    parsed_transformer, transformer_chunks
):
    """Four tables in, four table chunks out — none merged into prose, none split apart."""
    blocks = _table_blocks(parsed_transformer)
    assert len(blocks) == 4, "the fixture's table count moved; the parser or the PDF changed"

    tables = [chunk for chunk in transformer_chunks if chunk.table is not None]
    assert len(tables) == 4
    assert [chunk.text for chunk in tables] == [block.text for block in blocks]


def test_the_two_column_paper_yields_its_eight_tables_as_chunks(parsed_bert, bert_chunks):
    blocks = _table_blocks(parsed_bert)
    assert len(blocks) == 8

    tables = [chunk for chunk in bert_chunks if chunk.table is not None]
    assert len(tables) == 8
    assert [chunk.table.digest for chunk in tables] == [
        table_digest(block.text) for block in blocks
    ]


def test_a_real_table_chunk_carries_the_shape_tableformer_reported(
    parsed_transformer, transformer_chunks
):
    """The shape is TableFormer's, threaded through, not a count of pipe characters."""
    blocks = _table_blocks(parsed_transformer)
    tables = [chunk for chunk in transformer_chunks if chunk.table is not None]

    for chunk, block in zip(tables, blocks, strict=True):
        assert block.table_shape is not None, "the parse reported a table with no shape"
        assert (chunk.table.rows, chunk.table.cols) == block.table_shape


def test_the_bleu_table_carries_its_own_caption_and_its_own_numbers(transformer_chunks):
    """The paper's Table 2 — the one a question about BLEU scores has to find.

    Asserted on the caption *and* on a cell, because those are the two halves of D8: the
    caption is what the summary is written from, and the number is what the answer is
    read from. A chunk holding one without the other is not a usable table.
    """
    bleu = next(
        chunk
        for chunk in transformer_chunks
        if chunk.table is not None and chunk.table.caption.startswith("Table 2:")
    )

    assert bleu.table.caption.startswith(
        "Table 2: The Transformer achieves better BLEU scores"
    )
    assert (bleu.table.rows, bleu.table.cols) == (12, 5)
    assert "41.8" in bleu.text, "the grid's own numbers must still be in the chunk"
    assert bleu.page_no == 8
    assert bleu.bbox is not None


def test_a_table_chunk_never_holds_the_prose_printed_beside_it(
    parsed_transformer, transformer_chunks
):
    """Its text is the table block's text and nothing else, on both fixtures' hardest case.

    The 21x13 variations table on page 9 is 9,652 characters — well past the 400-word
    window — so before task 4.10 it was already alone. Table 1 on page 6 is 143 words and
    was not: it packed with whatever paragraph preceded it.
    """
    blocks = {block.text for block in _table_blocks(parsed_transformer)}

    for chunk in transformer_chunks:
        if chunk.table is None:
            continue
        assert chunk.text in blocks


def test_no_prose_chunk_swallowed_a_table(parsed_transformer, transformer_chunks):
    """The complement of the test above: a grid may not appear inside a prose chunk."""
    for chunk in transformer_chunks:
        if chunk.table is not None:
            continue
        assert "|---" not in chunk.text, (
            f"chunk {chunk.ordinal} holds a Markdown grid but is not labelled a table"
        )


def test_every_real_table_in_both_papers_clears_the_summary_threshold(
    parsed_transformer, parsed_bert
):
    """The threshold must not be paying for itself by refusing real tables.

    Twelve real tables across the two papers, the smallest 8x3. All twelve clear the
    default (3 rows, 3 columns, 12 cells), so the saving comes from layout artefacts and
    two-column key/value blocks rather than from the tables D8 exists for.
    """
    shapes = [
        block.table_shape
        for document in (parsed_transformer, parsed_bert)
        for block in _table_blocks(document)
    ]
    assert len(shapes) == 12

    chunks = [
        chunk
        for document in (parsed_transformer, parsed_bert)
        for chunk in chunk_sections(document)
        if chunk.table is not None
    ]
    assert all(DEFAULT_TABLE_SUMMARY_POLICY.wants_summary(chunk.table) for chunk in chunks)


def test_the_chunks_still_cover_every_word_of_the_document(
    parsed_transformer, transformer_chunks
):
    """Isolating tables must not have dropped anything on the way past."""
    packed: set[str] = set()
    for chunk in transformer_chunks:
        packed.update(chunk.text.split())

    skip = {BlockKind.HEADING, BlockKind.PAGE_HEADER, BlockKind.PAGE_FOOTER}
    for block in parsed_transformer.blocks:
        if block.kind in skip:
            continue
        missing = set(block.text.split()) - packed
        assert not missing, f"words dropped from page {block.page_no}: {sorted(missing)[:5]}"
