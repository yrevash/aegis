"""The table-summary primitives: the cache key, the threshold, and the one model call.

No PDF and no database here — this is the arithmetic underneath task 4.10, and all of it
is about **not** spending money:

* :func:`~aegis.ingestion.tables.table_digest` decides what counts as the *same* table, so
  it decides how often the cache upstairs can hit. Two renderings that differ only in
  Docling's column padding must hash the same, and two tables that differ only in their
  caption must not.
* :meth:`~aegis.ingestion.tables.TableSummaryPolicy.wants_summary` decides which tables
  are worth a call at all.
* :func:`~aegis.ingestion.tables.truncate_grid` decides how large the prompt may get,
  which is where nearly all of the cost of a table-dense document actually lives: the
  126-page IRS fixture's tables are 3.2 MB of Markdown, and the 6,000-character cap sends
  134 KB of it.

The completer is a spy in every test that has one. A real gateway call in a unit test is
a bill and a network dependency, and it cannot answer the only question these tests ask,
which is *how many calls happened*.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from aegis.core.models import ModelRole
from aegis.ingestion.tables import (
    DEFAULT_TABLE_SUMMARY_POLICY,
    TableRef,
    TableSummaryPolicy,
    summarise_table,
    summary_messages,
    table_caption,
    table_digest,
    truncate_grid,
)

#: A small, real-shaped table: a caption, a header row, a rule, and three data rows.
_GRID = """Table 4: SWAG Dev and Test accuracies.

| System     | Dev   | Test   |
|------------|-------|--------|
| ESIM+GloVe | 51.9  | 52.7   |
| OpenAI GPT | -     | 78.0   |
| BERT LARGE | 86.6  | 86.3   |"""


def _ref(text: str = _GRID, *, rows: int = 5, cols: int = 3) -> TableRef:
    """Build the reference a chunk would carry for ``text``."""
    return TableRef(
        rows=rows, cols=cols, caption=table_caption(text), digest=table_digest(text)
    )


@dataclass(frozen=True)
class _Completion:
    """The one field the retrieval completion protocol requires."""

    content: str


class _Spy:
    """A completer that counts, and answers with something recognisable."""

    def __init__(self, reply: str = "It reports SWAG accuracies for five systems.") -> None:
        self.reply = reply
        self.calls: list[list[dict[str, object]]] = []
        self.roles: list[object] = []

    async def __call__(
        self,
        role: object,
        messages: list[dict[str, object]],
        *,
        temperature: float = 0.0,
        response_format: dict[str, object] | None = None,
    ) -> _Completion:
        self.roles.append(role)
        self.calls.append(messages)
        return _Completion(self.reply)


# ─────────────────────────────────────────────────────────────────────────────
# The cache key
# ─────────────────────────────────────────────────────────────────────────────


def test_the_digest_ignores_the_column_padding_docling_happens_to_emit():
    """Padding is a rendering artefact; a cache that keyed on it would miss constantly.

    Docling pads every Markdown cell out to its column's width, so the *same* table
    exported beside a wider neighbour is a different byte string. A digest that changed
    with it would buy a second model call for a table the platform has already described.
    """
    padded = _GRID
    tight = "\n".join(
        " ".join(line.split()) if line.strip() else line for line in _GRID.splitlines()
    )

    assert padded != tight
    assert table_digest(padded) == table_digest(tight)


def test_the_digest_changes_when_a_single_cell_changes():
    """A different number is a different table, and must not reuse a stale sentence."""
    changed = _GRID.replace("86.6", "88.1")

    assert table_digest(changed) != table_digest(_GRID)


def test_two_identical_grids_under_different_captions_are_different_tables():
    """The caption is what the table is *about*; two of them deserve two sentences."""
    dev = _GRID
    test = _GRID.replace("Table 4: SWAG Dev and Test accuracies.", "Table 9: CoNLL NER.")

    assert table_digest(dev) != table_digest(test)


def test_the_digest_is_a_full_sha256_hex_string():
    digest = table_digest(_GRID)

    assert len(digest) == 64
    assert set(digest) <= set("0123456789abcdef")


def test_the_caption_is_everything_before_the_first_grid_row():
    assert table_caption(_GRID) == "Table 4: SWAG Dev and Test accuracies."


def test_a_table_printed_with_no_caption_reports_no_caption():
    """An empty caption is recorded as empty, never invented from the first row."""
    assert table_caption("| a | b |\n|---|---|\n| 1 | 2 |") == ""


def test_a_caption_broken_across_lines_is_rejoined():
    text = "Table 1: Maximum path lengths,\nper-layer complexity.\n\n| a |\n|---|"

    assert table_caption(text) == "Table 1: Maximum path lengths, per-layer complexity."


# ─────────────────────────────────────────────────────────────────────────────
# The threshold
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("rows", "cols"),
    [
        (2, 3),  # the case D8's cost note names: a label and three values
        (3, 2),  # a key-and-value list, which already reads as prose
        (3, 3),  # two-dimensional but only nine cells — the chunk carries it fine
        (1, 40),  # a single row, however wide
    ],
)
def test_a_small_table_is_not_worth_a_model_call(rows, cols):
    """Below the threshold the answer is not "summarise cheaply", it is "do not call"."""
    assert not DEFAULT_TABLE_SUMMARY_POLICY.wants_summary(
        TableRef(rows=rows, cols=cols, caption="", digest="d")
    )


@pytest.mark.parametrize(
    ("rows", "cols"),
    [
        (4, 3),  # the smallest grid the default admits
        (3, 4),
        (8, 3),  # the smallest real table in the phase's two paper fixtures
        (54, 23),  # the largest in the 126-page IRS fixture
    ],
)
def test_a_genuinely_two_dimensional_table_is_summarised(rows, cols):
    assert DEFAULT_TABLE_SUMMARY_POLICY.wants_summary(
        TableRef(rows=rows, cols=cols, caption="", digest="d")
    )


def test_the_threshold_is_configurable_in_both_directions():
    """The right cut-off is a property of a corpus, so it is settings and not a constant."""
    table = TableRef(rows=3, cols=3, caption="", digest="d")

    assert not DEFAULT_TABLE_SUMMARY_POLICY.wants_summary(table)
    assert TableSummaryPolicy(min_cells=9).wants_summary(table)
    assert not TableSummaryPolicy(min_rows=4, min_cols=4, min_cells=16).wants_summary(
        TableRef(rows=8, cols=3, caption="", digest="d")
    )


def test_disabling_summaries_turns_every_table_down_and_says_why():
    policy = TableSummaryPolicy(enabled=False)
    table = TableRef(rows=54, cols=23, caption="", digest="d")

    assert not policy.wants_summary(table)
    assert policy.reason_to_skip(table) == "table summaries are disabled"


def test_a_table_with_no_shape_at_all_fails_the_threshold_rather_than_passing_it():
    """``(0, 0)`` is what a block with no TableFormer shape becomes; it must not call."""
    assert not DEFAULT_TABLE_SUMMARY_POLICY.wants_summary(
        TableRef(rows=0, cols=0, caption="", digest="d")
    )


def test_the_skip_reason_names_the_shape_and_the_threshold():
    reason = DEFAULT_TABLE_SUMMARY_POLICY.reason_to_skip(
        TableRef(rows=2, cols=3, caption="", digest="d")
    )

    assert "2x3" in reason
    assert "6 cells" in reason
    assert "3x3" in reason


# ─────────────────────────────────────────────────────────────────────────────
# The prompt's size, which is where a table-dense document's bill actually is
# ─────────────────────────────────────────────────────────────────────────────


def test_a_grid_that_fits_is_sent_whole():
    assert truncate_grid(_GRID, max_chars=10_000) == _GRID


def test_an_oversized_grid_keeps_whole_rows_and_says_what_it_dropped():
    """Half a Markdown row is not a table, and a silent truncation is a lie by omission.

    The model is told rows were omitted because otherwise it describes the range it was
    shown as the table's range — a sentence a reader has no way to distrust.
    """
    big = "Table 9: rates.\n\n| year | rate |\n|---|---|\n" + "\n".join(
        f"| 20{n:02d} | {n}.5 |" for n in range(400)
    )

    trimmed = truncate_grid(big, max_chars=600)

    assert len(trimmed) < len(big)
    assert trimmed.startswith("Table 9: rates.")
    assert "| year | rate |" in trimmed, "the header must survive; rows without it are noise"
    assert "further rows omitted)" in trimmed
    for line in trimmed.splitlines():
        assert not line.startswith("| 20") or line.endswith("|")


# ─────────────────────────────────────────────────────────────────────────────
# The call itself
# ─────────────────────────────────────────────────────────────────────────────


async def test_summarising_a_table_is_exactly_one_call_on_the_cheap_role():
    """One table, one call — and on the role that a table-dense document can afford."""
    spy = _Spy()

    summary = await summarise_table(_GRID, table=_ref(), complete=spy)

    assert len(spy.calls) == 1
    assert spy.roles == [ModelRole.CHEAP]
    assert summary == "It reports SWAG accuracies for five systems."


async def test_the_prompt_carries_the_caption_the_shape_and_the_grid():
    """A caption like "Dev and Test accuracies" says nothing without the rest."""
    spy = _Spy()

    await summarise_table(
        _GRID, table=_ref(), complete=spy, title="BERT", section="Experiments > SWAG"
    )

    prompt = str(spy.calls[0][-1]["content"])
    assert "BERT" in prompt
    assert "Experiments > SWAG" in prompt
    assert "5 rows x 3 columns" in prompt
    assert "Table 4: SWAG Dev and Test accuracies." in prompt
    assert "| ESIM+GloVe | 51.9  | 52.7   |" in prompt


def test_the_system_prompt_forbids_inventing_numbers():
    """The one failure mode of this feature that would poison a citation."""
    system = str(summary_messages(_GRID, table=_ref())[0]["content"])

    assert "never write a number that is not printed" in system


async def test_a_runaway_summary_is_truncated_rather_than_left_to_displace_the_grid():
    spy = _Spy(reply=" ".join(f"word{n}" for n in range(500)))

    summary = await summarise_table(
        _GRID, table=_ref(), complete=spy, policy=TableSummaryPolicy(max_summary_words=20)
    )

    assert len(summary.split()) == 20
    assert summary.endswith("word19…"), "the cut must be visible, not silent"


async def test_a_model_that_answers_with_nothing_yields_no_summary_rather_than_filler():
    """An empty reply is an absence. Nothing here manufactures a sentence from the grid."""
    spy = _Spy(reply="   \n  ")

    assert await summarise_table(_GRID, table=_ref(), complete=spy) == ""


async def test_a_multi_line_reply_is_collapsed_into_the_text_that_gets_embedded():
    spy = _Spy(reply="It reports SWAG accuracies.\n\n   Human performance is highest.")

    summary = await summarise_table(_GRID, table=_ref(), complete=spy)

    assert summary == "It reports SWAG accuracies. Human performance is highest."
