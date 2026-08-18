"""The verbatim-span primitive and citation verification (tasks 4.11/4.14, D10).

Everything the gold set and the citation check both stand on is here, because if this
normaliser is wrong then every retrieval number in the phase is wrong in the same
direction and nothing downstream would notice.
"""

from __future__ import annotations

import pytest

from aegis.retrieval.citations import (
    Citation,
    CitationStatus,
    citation_validity,
    matched_fraction,
    normalise_span,
    span_present,
    verify_citations,
)
from aegis.retrieval.models import Source
from aegis.retrieval.spotlight import build_spotlighted_context

SENTENCE = "The encoder is composed of a stack of N = 6 identical layers."


class _FakeSource:
    """The minimal shape verify_citations reads: an id and some text."""

    def __init__(self, id: str, text: str) -> None:
        self.id = id
        self.text = text


# ── the normalisation the whole phase rests on ───────────────────────────────

def test_casing_and_whitespace_do_not_change_a_span():
    assert normalise_span("The  ENCODER\nis\t composed") == normalise_span(
        "the encoder is composed"
    )


def test_punctuation_and_column_gutters_collapse():
    assert span_present(
        "a stack of N = 6 identical layers",
        "The encoder is composed   of a stack  of  N  =  6  identical  layers.",
    )


@pytest.mark.parametrize(
    ("in_document", "quoted"),
    [
        # Ligatures: the PDF font emits one glyph, the reader types two letters.
        ("the ﬁnal hidden vector", "the final hidden vector"),
        # A line-wrap hyphen is not part of the word.
        ("the trans-\nformer model", "the transformer model"),
        # PDFium emits U+FFFE where it cannot map the hyphen glyph — 332 times in
        # bert-two-column.pdf alone.
        ("all WordPiece to￾kens in each", "all WordPiece tokens in each"),
        # A soft hyphen is a hyphenation hint, never content.
        ("over­represented in poverty", "overrepresented in poverty"),
        # Decimal points survive as separators either way: "28 . 4" and "28.4" are one
        # number in two parsers' opinions, and the table arm depends on this.
        ("Transformer (big) 28 . 4 41 . 8", "Transformer (big) 28.4 41.8"),
    ],
)
def test_the_pdf_transport_artefacts_are_folded(in_document, quoted):
    assert span_present(quoted, in_document)


def test_a_different_word_is_still_a_different_span():
    """Normalising folds transport, not meaning — this is what stops it inflating."""
    assert not span_present("a stack of N = 8 identical layers", SENTENCE)
    assert not span_present("the decoder is composed of a stack", SENTENCE)


def test_a_span_matches_on_whole_words_only():
    assert not span_present("rate", "the corporate structure")
    assert span_present("rate", "the poverty rate rose")


def test_a_span_survives_the_spotlight_datamarking():
    """The model reads datamarked text; a quote copied out of it must still verify.

    Without this, turning on the injection defence would silently fail every citation.
    """
    context = build_spotlighted_context([SENTENCE])
    assert "▁" in context
    assert span_present(SENTENCE, context)


def test_an_empty_or_punctuation_only_span_is_never_present():
    assert not span_present("", SENTENCE)
    assert not span_present("   ...   ", SENTENCE)


# ── the failure diagnostic ───────────────────────────────────────────────────

def test_matched_fraction_separates_a_near_miss_from_an_invention():
    near = matched_fraction("The encoder is composed of a stack of six layers.", SENTENCE)
    invented = matched_fraction("Revenue grew by 40% in the fourth quarter.", SENTENCE)
    assert near > 0.5
    assert invented < 0.2
    assert matched_fraction(SENTENCE, SENTENCE) == 1.0


# ── citation verification ────────────────────────────────────────────────────

def test_a_citation_whose_span_is_in_its_chunk_verifies():
    sources = [_FakeSource("c1", f"Some preamble. {SENTENCE} And more after it.")]
    (check,) = verify_citations([Citation("c1", SENTENCE)], sources)

    assert check.status is CitationStatus.VERIFIED
    assert check.verified
    assert check.matched_fraction == 1.0


def test_a_citation_whose_span_is_not_in_its_chunk_fails_and_stays_visible():
    """The failing citation is returned, marked — not dropped.

    Dropping it would delete the loudest hallucination signal the system can emit while
    leaving the prose it justified in place. See the module docstring of
    ``aegis.retrieval.citations``.
    """
    sources = [_FakeSource("c1", SENTENCE)]
    citations = [
        Citation("c1", SENTENCE),
        Citation("c1", "The encoder is composed of a stack of N = 12 identical layers."),
    ]

    checks = verify_citations(citations, sources)

    assert [c.status for c in checks] == [
        CitationStatus.VERIFIED,
        CitationStatus.UNVERIFIED,
    ]
    assert len(checks) == len(citations), "a failed citation must not disappear"
    assert not checks[1].verified
    assert "not in c1" in checks[1].reason


def test_a_citation_naming_a_chunk_that_was_never_retrieved_is_its_own_status():
    """Quoting a real sentence from a source the answer was not given is a different bug.

    Collapsing it into "unverified" would hide a scoping or id-mapping defect behind a
    hallucination label.
    """
    checks = verify_citations(
        [Citation("c9", SENTENCE)], [_FakeSource("c1", SENTENCE)]
    )

    assert checks[0].status is CitationStatus.UNKNOWN_SOURCE
    assert checks[0].matched_fraction == 0.0
    assert "not one of the 1 sources" in checks[0].reason


def test_verification_reads_a_real_retrieval_source():
    """The production `Source` model satisfies what verify_citations needs."""
    source = Source(id="doc#3", text=SENTENCE, score=0.9, metadata={})
    (check,) = verify_citations([Citation("doc#3", "a stack of N = 6")], [source])

    assert check.verified


def test_a_quote_that_is_only_verbatim_in_another_source_does_not_verify():
    sources = [_FakeSource("c1", "Unrelated text."), _FakeSource("c2", SENTENCE)]
    (check,) = verify_citations([Citation("c1", SENTENCE)], sources)

    assert check.status is CitationStatus.UNVERIFIED


def test_citation_validity_is_none_when_nothing_was_cited():
    """An answer that cited nothing is not perfectly cited — it is unmeasured."""
    assert citation_validity([]) is None

    sources = [_FakeSource("c1", SENTENCE)]
    checks = verify_citations(
        [Citation("c1", SENTENCE), Citation("c1", "invented text")], sources
    )
    assert citation_validity(checks) == 0.5
