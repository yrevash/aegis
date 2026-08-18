"""The D-parse quality gate, on inputs that let it fail.

Nothing here needs a PDF stack. The gate reads a
:class:`~aegis.ingestion.blocks.ParsedDocument` and a list of per-page strings, so every
failure it exists to detect can be built by hand and pointed at it — which is the only
way to prove that each of the three signals *independently* moves the score. A gate whose
signals are only ever exercised together is a gate where two of them could be dead.

The fixture-backed half — real Docling output on the two small papers, and the same
two-column document re-ordered into the failure — is in
``test_parse_confidence.py``.
"""

from __future__ import annotations

from aegis.ingestion.blocks import BBox, BlockKind, ParsedBlock, ParsedDocument, ParsedPage
from aegis.ingestion.probe import OcrDecision
from aegis.ingestion.quality import (
    FRAGMENT_CEILING,
    FRAGMENT_FLOOR,
    LOW_CONFIDENCE,
    MIN_ANCHORS_PER_PAGE,
    _count_inversions,
    _kendall_tau,
    _tokens,
    assess_parse,
    fragment_rate,
    headings_are_flat,
    ordering_agreement,
)

_BOX = BBox(left=0.0, top=0.0, right=10.0, bottom=10.0)
_OCR = OcrDecision(enabled=False, reason="test")

#: Two paragraphs of distinct long words, so every token is its own anchor.
_LEFT = "alpha bravo charlie delta echo foxtrot golf hotel india juliett kilo lima."
_RIGHT = "mike november oscar papa quebec romeo sierra tango uniform victor whiskey xray."


def _document(
    *,
    blocks: list[ParsedBlock],
    pages: int = 1,
) -> ParsedDocument:
    """A parsed document over ``pages`` pages, carrying ``blocks``."""
    return ParsedDocument(
        source_name="hand-built.pdf",
        pages=tuple(
            ParsedPage(
                page_no=number,
                width=612.0,
                height=792.0,
                char_count=2000,
                has_text_layer=True,
            )
            for number in range(1, pages + 1)
        ),
        blocks=tuple(blocks),
        ocr=_OCR,
    )


def _text(text: str, *, page: int = 1, kind: BlockKind = BlockKind.TEXT) -> ParsedBlock:
    """One block on one page."""
    return ParsedBlock(kind=kind, text=text, page_no=page, bbox=_BOX)


def _heading(text: str, *, level: int, page: int = 1) -> ParsedBlock:
    """One heading at a stated level."""
    return ParsedBlock(
        kind=BlockKind.HEADING, text=text, page_no=page, bbox=_BOX, level=level
    )


# ── tokenising: two extractors have to agree on what a word is ───────────────


def test_a_word_broken_across_a_line_is_one_token_in_both_readings():
    """PDFium substitutes U+FFFE for the break and drops the newline; Docling keeps the
    hyphen. Left alone, "representation" is one token in one reading and two in the other,
    so the single most distinctive word on the page is not an anchor in either."""
    assert _tokens("representa￾tion model") == ["representation", "model"]
    assert _tokens("representa-\ntion model") == ["representation", "model"]
    assert _tokens("representa­tion model") == ["representation", "model"]


def test_short_tokens_are_not_anchors():
    """A three-letter word repeats; a repeated token is a guess about which occurrence
    matched, not an anchor."""
    assert _tokens("the cat sat on a bright mat") == ["bright"]


def test_tokens_are_case_folded_and_stripped_of_punctuation():
    assert _tokens("Attention, Is All You Need!") == ["attention", "need"]


# ── inversions and tau: the ordering arithmetic itself ───────────────────────


def test_a_sorted_sequence_has_no_inversions():
    assert _count_inversions([0, 1, 2, 3, 4]) == 0


def test_a_reversed_sequence_is_every_pair_inverted():
    assert _count_inversions([4, 3, 2, 1, 0]) == 10  # 5 * 4 / 2


def test_one_transposition_is_one_inversion():
    assert _count_inversions([0, 2, 1, 3]) == 1


def test_identical_orders_agree_completely():
    tokens = _tokens(_LEFT)
    tau, anchors = _kendall_tau(tokens, tokens)

    assert tau == 1.0
    assert anchors == len(tokens)


def test_a_reversed_order_disagrees_completely():
    tokens = _tokens(_LEFT)
    tau, _anchors = _kendall_tau(list(reversed(tokens)), tokens)

    assert tau == -1.0


def test_only_tokens_unique_in_both_readings_become_anchors():
    """A token appearing twice on either side cannot be matched to one position."""
    _tau, anchors = _kendall_tau(
        ["alpha", "bravo", "bravo", "charlie"], ["alpha", "bravo", "charlie"]
    )

    assert anchors == 2, "'bravo' repeats in one reading and must not anchor anything"


def test_two_readings_that_share_nothing_have_no_agreement_to_report():
    tau, anchors = _kendall_tau(_tokens(_LEFT), _tokens(_RIGHT))

    assert tau is None
    assert anchors == 0


# ── the ordering signal, on documents ────────────────────────────────────────


def test_the_ordering_signal_fires_when_a_page_is_read_out_of_order():
    """The primary D-parse signal, in the smallest form that shows it working."""
    words = _tokens(f"{_LEFT} {_RIGHT}")
    page_text = [" ".join(words)]
    right_way = _document(blocks=[_text(word) for word in words])
    wrong_way = _document(blocks=[_text(word) for word in reversed(words)])

    assert ordering_agreement(right_way, page_text)[0] == 1.0
    assert ordering_agreement(wrong_way, page_text)[0] == -1.0


def test_reading_a_two_column_page_across_the_columns_sinks_the_score():
    """The failure the gate is named after, at block granularity: the right column is
    read before the left. Only the cross-block pairs invert — within a paragraph the
    words are still in order — so tau lands just under zero rather than at -1, and that
    is still comfortably below the threshold."""
    page_text = [f"{_LEFT}\n{_RIGHT}"]
    interleaved = _document(blocks=[_text(_RIGHT), _text(_LEFT)])

    tau, _pages, _anchors = ordering_agreement(interleaved, page_text)

    assert tau is not None and tau < 0.0
    assert assess_parse(interleaved, page_text).is_low


def test_ordering_is_scored_per_page_because_a_document_wide_tau_dilutes_it():
    """Measured, and the reason the aggregate is per page: reading order is scrambled
    *within* a page and never across pages, so a document-wide tau is dominated by the
    cross-page pairs that cannot invert. On ``bert-two-column.pdf`` re-ordered into the
    column-interleave failure that is 0.967 against a per-page 0.570 — the difference
    between a gate and a decoration."""
    pages = 6
    blocks: list[ParsedBlock] = []
    page_text: list[str] = []
    for page in range(1, pages + 1):
        words = [f"word{page}number{index}" for index in range(24)]
        # Every page is read backwards: total local disorder, no cross-page disorder.
        blocks.extend(_text(word, page=page) for word in reversed(words))
        page_text.append(" ".join(words))
    scrambled = _document(blocks=blocks, pages=pages)

    per_page, scored, _anchors = ordering_agreement(scrambled, page_text)

    assert scored == pages
    assert per_page == -1.0, "every page is fully inverted; the aggregate must say so"
    # And the document-wide comparison the per-page aggregate replaces: the same blocks
    # scored as one sequence look almost fine, because 5/6 of the pairs are cross-page.
    document_wide, _anchors = _kendall_tau(
        _tokens("\n\n".join(block.text for block in blocks)),
        _tokens("\n".join(page_text)),
    )
    assert document_wide > 0.6, (
        "if this ever drops, the per-page aggregate has stopped being the thing that "
        "makes the signal visible and this test is no longer explaining anything"
    )


def test_a_page_with_too_few_anchors_is_not_scored_at_all():
    """Two anchors give a tau of ±1 with no evidence behind it."""
    document = _document(blocks=[_text("alpha bravo")])

    tau, pages, anchors = ordering_agreement(document, ["alpha bravo"])

    assert (tau, pages, anchors) == (None, 0, 0)
    assert len(_tokens("alpha bravo")) < MIN_ANCHORS_PER_PAGE


def test_a_document_with_no_text_layer_reports_that_the_check_did_not_run():
    """A scanned document has no independent reading. That is not a failed check."""
    document = _document(blocks=[_text(_LEFT)])

    assert ordering_agreement(document, ())[0] is None


# ── the fragment signal ──────────────────────────────────────────────────────


def _prose(text: str) -> list[ParsedBlock]:
    """Ten-plus words of prose, which is the shortest block the signal scores."""
    return [_text(f"one two three four five six seven eight nine ten {text}")]


def test_a_paragraph_ending_in_a_full_stop_is_not_a_fragment():
    rate, scored = fragment_rate(_prose("eleven."))

    assert (rate, scored) == (0.0, 1)


def test_a_paragraph_stopping_mid_clause_is_a_fragment():
    rate, scored = fragment_rate(_prose("eleven and then"))

    assert (rate, scored) == (1.0, 1)


def test_a_closing_quote_after_the_full_stop_still_closes_the_clause():
    assert fragment_rate(_prose('eleven."'))[0] == 0.0
    assert fragment_rate(_prose("eleven.)"))[0] == 0.0


def test_headings_and_tables_are_not_scored_for_fragmentation():
    """They end without a full stop by nature, and counting them would drown the signal
    in blocks that are not fragments at all."""
    blocks = [
        _heading("3 Model Architecture", level=1),
        _text("| a | b |\n|---|---|", kind=BlockKind.TABLE),
        _text("Figure 2: the encoder", kind=BlockKind.CAPTION),
    ]

    assert fragment_rate(blocks) == (0.0, 0)


def test_a_short_block_has_no_clause_to_end_in_the_middle_of():
    assert fragment_rate([_text("Total revenue")]) == (0.0, 0)


def test_the_fragment_signal_alone_can_sink_a_parse():
    """Independently exercised, on purpose: perfect ordering, perfect headings, and
    every paragraph cut. A change that broke the fragment arm would pass every ordering
    test in this file and fail here."""
    cut = [
        _text(f"paragraph number {index} runs on and on and then stops abruptly and")
        for index in range(10)
    ]
    document = _document(blocks=cut)

    quality = assess_parse(document, ())

    assert quality.fragment_rate == 1.0
    assert quality.fragments == 0.0
    assert quality.confidence == 0.0
    assert quality.is_low


def test_a_rate_at_the_floor_costs_nothing_and_the_ceiling_costs_everything():
    """The two calibration points, asserted so a change to either constant is visible."""
    span = FRAGMENT_CEILING - FRAGMENT_FLOOR

    assert span > 0
    assert FRAGMENT_FLOOR > 0.221, (
        "the floor must sit above the highest fragment rate measured on a parse known "
        "to be correct (0.221, census-income-tables.pdf), or the gate penalises correct "
        "documents for a shape real prose actually has"
    )


# ── the heading signal: the FLAT case, and only the flat case ────────────────


def test_a_long_document_with_every_heading_at_level_one_is_flat():
    """``{1: 33}`` on Docling's defaults, measured on ``census-income-tables.pdf``."""
    assert headings_are_flat({1: 33}, page_count=67)


def test_a_short_document_may_legitimately_be_flat():
    assert not headings_are_flat({1: 33}, page_count=3)


def test_a_document_with_few_headings_may_legitimately_be_flat():
    assert not headings_are_flat({1: 3}, page_count=67)


def test_a_multi_level_tree_is_not_flat():
    assert not headings_are_flat({1: 8, 2: 12, 3: 13}, page_count=16)


def test_the_histogram_cannot_catch_the_half_configured_case_and_does_not_claim_to():
    """The measured correction this whole task was redesigned around: on
    ``docling==2.120.3``, enabling ``heading_hierarchy_options.enabled`` *alone* yields
    ``{1: 13, 2: 12, 3: 8}`` — a plausible three-level tree with eleven headings at the
    wrong depth. It is indistinguishable from a correct one by shape, so the gate does
    not pretend otherwise; the defence is setting both switches, which ``convert.py``
    asserts."""
    half_configured = {1: 13, 2: 12, 3: 8}
    correct = {1: 8, 2: 12, 3: 13}

    assert not headings_are_flat(half_configured, page_count=67)
    assert not headings_are_flat(correct, page_count=16)


def test_the_heading_signal_alone_can_sink_a_parse():
    """Perfect ordering, no fragments, and a heading tree that never turned on."""
    blocks: list[ParsedBlock] = []
    for page in range(1, 11):
        blocks.append(_heading(f"Section {page}", level=1, page=page))
        blocks.append(_text(f"one two three four five six seven eight nine {page}.", page=page))
    document = _document(blocks=blocks, pages=10)

    quality = assess_parse(document, ())

    assert quality.flat_headings
    assert quality.confidence < LOW_CONFIDENCE
    assert quality.is_low


# ── the score itself ─────────────────────────────────────────────────────────


def test_the_score_is_the_weakest_signal_not_the_average():
    """Averaging would let a perfect ordering score hide a heading tree that is entirely
    flat, which is the arithmetic that turns a gate into a decoration."""
    blocks: list[ParsedBlock] = []
    page_text: list[str] = []
    for page in range(1, 11):
        prose = f"alpha{page} bravo{page} charlie{page} delta{page} echo{page} " + (
            f"foxtrot{page} golf{page} hotel{page} india{page} juliett{page}."
        )
        blocks.append(_heading(f"Section {page}", level=1, page=page))
        blocks.append(_text(prose, page=page))
        page_text.append(f"Section {page}\n{prose}")
    document = _document(blocks=blocks, pages=10)

    quality = assess_parse(document, page_text)

    assert quality.ordering == 1.0, "the ordering arm is perfect on this document"
    assert quality.fragments == 1.0, "and so is the fragment arm"
    assert quality.confidence < LOW_CONFIDENCE, (
        "two perfect signals must not average away the third, which says the heading "
        "hierarchy is not running at all"
    )


def test_a_parse_with_nothing_checkable_is_not_credited_with_confidence():
    """No prose and no text layer. Reporting 1.0 would claim confidence from an absence
    of evidence, which is the exact move this module exists to refuse."""
    quality = assess_parse(_document(blocks=[]), ())

    assert quality.confidence == 0.0
    assert quality.is_low
    assert any("no signal could be computed" in reason for reason in quality.reasons)


def test_the_reasons_name_every_signal_so_a_person_can_act_on_the_score():
    """4.12 renders these. A bare number tells nobody what to look at."""
    document = _document(blocks=[_text(_LEFT), _text(_RIGHT)])

    quality = assess_parse(document, [f"{_LEFT}\n{_RIGHT}"])

    joined = " | ".join(quality.reasons).lower()
    assert "reading order" in joined
    assert "mid-clause" in joined
    assert "heading levels" in joined


def test_a_disagreement_says_so_in_words_not_only_in_a_number():
    document = _document(blocks=[_text(_RIGHT), _text(_LEFT)])

    quality = assess_parse(document, [f"{_LEFT}\n{_RIGHT}"])

    assert quality.is_low
    assert any("DISAGREES" in reason for reason in quality.reasons)


def test_an_unavailable_ordering_check_is_reported_as_not_run_not_as_failed():
    document = _document(blocks=_prose("eleven."))

    quality = assess_parse(document, ())

    assert quality.ordering is None
    assert quality.confidence == 1.0, "the fragment arm is clean and nothing else ran"
    assert any("did not run" in reason for reason in quality.reasons)
