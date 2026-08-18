"""Two parse-side defects found by task 4.3, fixed and pinned here.

Both were found by reading real chunks off a real fixture rather than by a unit test,
which is why they are pinned with unit tests: neither raises, and neither is visible in
anything but the text a reader eventually sees.
"""

from __future__ import annotations

from aegis.ingestion.blocks import BBox, BlockKind, ParsedBlock
from aegis.ingestion.convert import _drop_duplicated_captions, _heading_level

_BOX = BBox(left=0.0, top=0.0, right=10.0, bottom=10.0)


def _block(kind: BlockKind, text: str) -> ParsedBlock:
    return ParsedBlock(kind=kind, text=text, page_no=1, bbox=_BOX)


# ── the heading depth comes from the author's numbering ──────────────────────


def test_a_numbered_heading_states_its_own_depth():
    """The number is the author\'s statement of depth; counting it is exact."""
    assert _heading_level("3 Model Architecture", 1) == 1
    assert _heading_level("3.2 Attention", 1) == 2
    assert _heading_level("3.2.1 Scaled Dot-Product Attention", 1) == 3


def test_the_measured_collapse_no_longer_happens():
    """Docling gave these two the same level, so the deeper one popped its parent.

    That is the defect: the path read ``3 Model Architecture > 3.2.1 ...`` and the
    middle rung was *missing* rather than wrong, which nothing downstream could detect.
    """
    parent = _heading_level("3.2 Attention", 2)
    child = _heading_level("3.2.1 Scaled Dot-Product Attention", 2)
    assert child > parent, "the child must nest under its parent, not replace it"


def test_an_unnumbered_heading_still_trusts_the_model():
    """Numbering is better evidence where it exists; it does not exist everywhere."""
    assert _heading_level("Introduction", 2) == 2
    assert _heading_level("Introduction", 0) == 1
    assert _heading_level("Introduction", None) == 1


def test_a_runaway_numbering_run_is_capped():
    """A numbering-like run must not nest deeper than any real document does."""
    assert _heading_level("1.2.3.4.5.6.7.8.9 Notes", 1) == 6


def test_a_bare_number_is_not_read_as_numbering():
    """``2024 Annual Report`` is a year, not a section number."""
    assert _heading_level("2024 Annual Report", 1) == 1


# ── a caption the table already carries is dropped once ──────────────────────


def test_a_caption_the_table_repeats_is_dropped():
    """Docling emits the caption standalone *and* at the head of the table markdown."""
    blocks = [
        _block(BlockKind.CAPTION, "Table 1: Maximum path lengths per layer type."),
        _block(
            BlockKind.TABLE,
            "Table 1: Maximum path lengths per layer type.\n\n| a | b |\n|---|---|",
        ),
    ]
    kept = _drop_duplicated_captions(blocks)
    assert [b.kind for b in kept] == [BlockKind.TABLE]


def test_the_table_keeps_its_own_copy_so_it_stays_self_describing():
    """The table is the copy worth keeping — it is retrievable on its own terms."""
    table = _block(BlockKind.TABLE, "Table 1: Path lengths.\n\n| a |\n|---|")
    kept = _drop_duplicated_captions([_block(BlockKind.CAPTION, "Table 1: Path lengths."), table])
    assert kept[0].text == table.text


def test_a_caption_no_table_repeats_survives():
    """The negative control. Without it, a rule that deleted every caption would pass."""
    blocks = [
        _block(BlockKind.CAPTION, "Figure 2: The Transformer architecture."),
        _block(BlockKind.TABLE, "| unrelated | table |\n|---|---|"),
    ]
    assert len(_drop_duplicated_captions(blocks)) == 2


def test_only_an_adjacent_table_can_absorb_a_caption():
    """Two tables in one paper legitimately share a caption prefix.

    A document-wide match would delete a real block, so the match is positional.
    """
    blocks = [
        _block(BlockKind.CAPTION, "Table 2: Variations on the Transformer."),
        _block(BlockKind.TEXT, "Some prose sits between them."),
        _block(BlockKind.TABLE, "Table 2: Variations on the Transformer.\n\n| a |\n|---|"),
    ]
    assert len(_drop_duplicated_captions(blocks)) == 3
