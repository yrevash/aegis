"""The parse quality gate on real parses of real PDFs — and the finding that redrew it.

**What the phase expected.** ``tests/fixtures/pdfs/README.md`` names
``bert-two-column.pdf`` as "the multi-column silent-failure case … the fixture the
D-parse quality gate must score *low*", against ``transformer-single-column.pdf`` as the
control. That is what D-parse was written against.

**What is actually true on ``docling==2.120.3``, measured here.** Docling reads BERT's
two columns in the right order. Its per-page reading order agrees with the raw text
layer at **tau 0.997** over 2443 anchor tokens on all 16 pages — statistically
indistinguishable from the single-column control's 0.9999. The known-bad fixture is not
bad on this version, and no honest signal will say it is. Asserting a low score here
would mean tuning a threshold until it fired on a correctly parsed document, which is
the same defect as a gate that never fires, pointed the other way.

**So the gate is proved to fire on the failure itself rather than on the fixture that
was expected to carry it.** ``_read_across_the_columns`` takes the *real* parse of the
*real* two-column paper and re-orders its blocks by position alone — top to bottom, left
to right, columns not detected — which is exactly the reading order a layout model
produces when it misses the column split (docling#2067). Every block, every box and every
word is genuine; only the order is the failure's.

That gives a control that is *stronger* than the one the README asked for, because the
same operation is applied to every document. Measured on ``docling==2.120.3``:

=================================  ================  ========================
fixture                            Docling's order   read across the columns
=================================  ================  ========================
``transformer-single-column.pdf``  1.000             1.000 — **unchanged**
``bert-two-column.pdf``            0.997             **0.565 — low**
``census-income-tables.pdf``       0.919             **0.724 — low**
``irs-1040-instructions.pdf``      0.912             **0.452 — low**
=================================  ================  ========================

Reading top-to-bottom-ignoring-columns *is* the reading order of a single-column page, so
the identical operation costs the control nothing and sinks all three multi-column
documents. A low score is therefore attributable to **layout**, which is the property the
README's control existed to establish.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from aegis.ingestion import ParsedDocument, parse_pdf
from aegis.ingestion.probe import probe_page_text
from aegis.ingestion.quality import LOW_CONFIDENCE, assess_parse

from .conftest import fixture_pdf, slow_fixtures

pytest.importorskip("docling", reason="the 'ingestion' extra is not installed")

#: A parse this gate is content with. Every real fixture clears it; see the module
#: docstring for the measured values.
_HIGH = 0.90


def _read_across_the_columns(document: ParsedDocument) -> ParsedDocument:
    """Re-order a parse's blocks the way a layout model that missed the columns would.

    Position only — page, then top, then left — so a two-column page comes back with its
    left and right columns interleaved row by row, and a single-column page comes back
    exactly as it went in. The text, the boxes and the page numbers are the parser's own.

    Args:
        document: A real parse.

    Returns:
        The same document with its blocks in the naive top-to-bottom order.
    """
    return replace(
        document,
        blocks=tuple(
            sorted(
                document.blocks,
                key=lambda block: (
                    block.page_no,
                    round(block.bbox.top, 1) if block.bbox else 0.0,
                    block.bbox.left if block.bbox else 0.0,
                ),
            )
        ),
    )


# ── the real parses ──────────────────────────────────────────────────────────


def test_parse_pdf_records_a_confidence_on_every_parse(parsed_transformer):
    """The value has to exist before anything can act on it."""
    quality = parsed_transformer.quality

    assert quality is not None, "a parse nobody scored is a parse nobody can tell is wrong"
    assert 0.0 <= quality.confidence <= 1.0
    assert quality.reasons, "a bare number tells nobody what to look at"
    assert not quality.is_low


def test_the_single_column_control_scores_high(parsed_transformer):
    quality = parsed_transformer.quality

    assert quality.confidence >= _HIGH, quality.reasons
    assert quality.ordering is not None and quality.ordering > 0.99
    assert quality.ordering_pages == parsed_transformer.page_count
    assert not quality.flat_headings


def test_docling_2_120_3_reads_the_two_column_paper_in_the_right_order(parsed_bert):
    """The measured correction to the phase's premise; see the module docstring.

    This is an assertion and not a note because it is the fact the rest of this file
    stands on: if a future Docling *does* scramble this document, the gate must be the
    thing that says so, and this test failing is that alarm.
    """
    quality = parsed_bert.quality

    assert quality.confidence >= _HIGH, quality.reasons
    assert quality.ordering is not None and quality.ordering > 0.99, (
        "bert-two-column.pdf was expected to parse badly and does not on 2.120.3; if "
        "that has changed, the fixture README and D-parse both need re-reading"
    )
    assert quality.ordering_anchors > 1000, "the agreement is measured over real evidence"


# ── the gate firing: the same document, read across its columns ──────────────


def test_reading_the_two_column_paper_across_its_columns_scores_low(parsed_bert):
    """The gate's whole reason to exist: this parse is scrambled and raises nothing."""
    scrambled = _read_across_the_columns(parsed_bert)

    quality = assess_parse(scrambled, probe_page_text(fixture_pdf("bert-two-column.pdf")))

    assert quality.is_low, quality.reasons
    assert quality.confidence < LOW_CONFIDENCE
    assert quality.ordering is not None and quality.ordering < 0.7
    assert any("DISAGREES" in reason for reason in quality.reasons)


def test_the_single_column_control_is_untouched_by_the_same_reordering(
    parsed_transformer,
):
    """The control, and the reason the low score above is about **layout**.

    Top-to-bottom-ignoring-columns *is* the reading order of a single-column page, so the
    identical operation costs this document nothing. Without this half, a low score on
    the scrambled two-column paper would only prove that re-ordering blocks lowers the
    ordering score — which is true of any document and says nothing about columns.
    """
    scrambled = _read_across_the_columns(parsed_transformer)

    quality = assess_parse(
        scrambled, probe_page_text(fixture_pdf("transformer-single-column.pdf"))
    )

    assert not quality.is_low, quality.reasons
    assert quality.confidence >= _HIGH


def test_the_fragment_arm_is_measured_on_both_papers(parsed_bert, parsed_transformer):
    """The second signal, on real prose rather than on hand-built blocks.

    Both are correct parses, so both must sit under the floor that draws no penalty. The
    two-column paper's rate is the higher of the two (0.094 against 0.029); the floor
    itself is calibrated on ``census-income-tables.pdf``, whose correct parse measures
    0.221 — see the slow test below.
    """
    assert parsed_transformer.quality.fragments == 1.0
    assert parsed_bert.quality.fragments == 1.0
    assert parsed_bert.quality.fragment_rate > parsed_transformer.quality.fragment_rate


# ── the two large fixtures ───────────────────────────────────────────────────


@pytest.fixture(scope="module")
def parsed_census() -> ParsedDocument:
    """The 67-page statistical report, parsed once for the two tests below (~214 s)."""
    return parse_pdf(fixture_pdf("census-income-tables.pdf"))


@slow_fixtures
def test_the_67_page_statistical_report_scores_high(parsed_census):
    """67 pages of two-column statistical tables — the correct parse with the *highest*
    fragment rate we have (0.221), and therefore the one that decides whether the
    fragment floor is drawn too tight."""
    quality = parsed_census.quality

    assert not quality.is_low, quality.reasons
    assert quality.confidence >= _HIGH, quality.reasons
    assert quality.fragments == 1.0, (
        "a correct parse of a real document must not be penalised for prose that "
        "legitimately ends without a full stop"
    )
    assert not quality.flat_headings


@slow_fixtures
def test_the_126_page_government_form_scores_high():
    """The other end of the corpus: dense forms, merged cells, three columns of
    instructions."""
    document = parse_pdf(fixture_pdf("irs-1040-instructions-tables.pdf"))

    quality = document.quality
    assert not quality.is_low, quality.reasons
    assert quality.confidence >= _HIGH, quality.reasons


@slow_fixtures
def test_reading_the_statistical_report_across_its_columns_scores_low(parsed_census):
    """The gate fires on the large fixture too, and by the same construction."""
    scrambled = _read_across_the_columns(parsed_census)

    quality = assess_parse(
        scrambled, probe_page_text(fixture_pdf("census-income-tables.pdf"))
    )

    assert quality.is_low, quality.reasons
