"""Tables as first-class objects, and the one model call that makes one findable (D8).

A table reaches this module as what :mod:`aegis.ingestion.convert` exports: a Markdown
grid, opening with the table's own caption. That grid is *correct* and it is nearly
unsearchable. ``| 27.3 | 28.4 | 41.8 |`` is three tokens of arithmetic and no semantics,
so the vector it embeds to sits nowhere near "which model scored best on English-to-German
translation", and the lexical arm can only match a number the asker already knew. D8's
answer is one model call per table producing a sentence or two about what the table
*shows*, embedded **alongside** the grid rather than instead of it.

Two things in this module are cost control, and they are the reason it is not simply a
prompt inlined in the stage handler.

**The digest.** :func:`table_digest` hashes the table's own content, so the same table
costs one call no matter how many times it is seen: twice in one document, in two
documents of the same corpus, or on every re-ingest and every re-index of the same bytes.
``documents`` already deduplicates on ``content_sha256``; this is that idea one level
down, and it is what makes the 4.13 re-index path free rather than a second full bill.

**The threshold.** :class:`TableSummaryPolicy` refuses to spend a call on a table whose
Markdown a reader — and an embedding model — can already follow. The default is *three
rows, three columns and twelve cells*, which is the smallest grid that is genuinely
two-dimensional: two columns is a key-and-value list and reads as prose already, two rows
is a label and a value, and a 3×3 is nine cells a chunk carries perfectly well beside its
caption. Measured against the phase's fixtures, every one of the twelve real tables in the
two papers clears it — the smallest is 8×3 — so the threshold buys its saving from layout
artefacts and inline two-column blocks rather than from real tables. It is configurable
because the right cut-off is a property of a corpus, not of this code.

What is deliberately *not* here: any rule that a table below the threshold is dropped, or
that the summary replaces the grid. The numbers are the answer to most questions a table
is asked; the summary is how the question finds them.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aegis.core.models import ModelRole

if TYPE_CHECKING:
    # Type-checking only, and deliberately. ``aegis.retrieval`` imports the chunker,
    # which imports this package — so a runtime import of the retrieval protocols here
    # would close a cycle. ``from __future__ import annotations`` keeps the annotation a
    # string, which is all a signature needs.
    from aegis.retrieval.protocols import CompleteFn

__all__ = [
    "DEFAULT_TABLE_SUMMARY_POLICY",
    "TableRef",
    "TableSummaryPolicy",
    "summarise_table",
    "summary_messages",
    "table_caption",
    "table_digest",
    "truncate_grid",
]

#: Runs of horizontal whitespace, collapsed before hashing. Docling pads a Markdown cell
#: out to its column's width, so the *same* grid rendered beside a wider neighbouring
#: column is a different byte string — and a cache keyed on those bytes would miss on a
#: table it has already paid for. Newlines are preserved: a row boundary is content.
_HORIZONTAL_SPACE = re.compile(r"[ \t]+")

#: Any whitespace at all, for normalising a summary the model wrote across several lines.
_ANY_SPACE = re.compile(r"\s+")

#: A Markdown grid row. The caption Docling emits ahead of the grid does not match, which
#: is what :func:`table_caption` separates on.
_GRID_ROW = re.compile(r"^\s*\|")


def table_digest(text: str) -> str:
    """Return the content-addressed cache key for a table.

    Whitespace *within* a line is collapsed first and trailing whitespace dropped, so two
    renderings of the same table that differ only in column padding hash the same. Line
    structure is kept, because a row break is part of what the table says. The caption is
    part of the hashed text: two identical grids under "Table 1: dev set" and "Table 2:
    test set" are two different tables and deserve two different sentences.

    Args:
        text: The table as Markdown, caption included.

    Returns:
        The SHA-256 of the normalised text, hex-encoded (64 characters).
    """
    lines = [_HORIZONTAL_SPACE.sub(" ", line).strip() for line in text.splitlines()]
    normalised = "\n".join(line for line in lines if line)
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def table_caption(text: str) -> str:
    """Return the caption a table's Markdown opens with, or ``""`` when it has none.

    Docling's Markdown export puts the caption ahead of the grid — which is also why
    :func:`aegis.ingestion.convert._drop_duplicated_captions` deletes the standalone
    caption block beside it — so the caption is every line before the first row that
    starts with a pipe.

    Args:
        text: The table as Markdown.

    Returns:
        The caption, whitespace-normalised, or the empty string.
    """
    head: list[str] = []
    for line in text.splitlines():
        if _GRID_ROW.match(line):
            break
        if line.strip():
            head.append(line.strip())
    return _ANY_SPACE.sub(" ", " ".join(head)).strip()


@dataclass(frozen=True, slots=True)
class TableRef:
    """What makes a table chunk a *table* rather than a slab of Markdown.

    Carried on the chunk (:class:`aegis.retrieval.chunker.SectionChunk`) and recorded on
    the row, so a consumer can tell a table from prose without parsing pipes back out of
    the text — which is the difference between "return the table on page 9" being a query
    the platform can answer and a string search it can only approximate.

    Attributes:
        rows: Rows in the parsed grid, header rows included, as TableFormer reported them.
        cols: Columns in the parsed grid.
        caption: The table's own caption, or ``""`` when it was printed without one.
        digest: :func:`table_digest` of the table's Markdown — the summary cache key.
    """

    rows: int
    cols: int
    caption: str
    digest: str

    @property
    def cells(self) -> int:
        """Rows × columns — the size the summary threshold is measured in."""
        return self.rows * self.cols


@dataclass(frozen=True, slots=True)
class TableSummaryPolicy:
    """When a table is worth a model call, and how large a prompt it may become.

    Attributes:
        enabled: Master switch. ``False`` summarises nothing and calls nothing — the
            grids are still chunked, still indexed and still citable, which is the
            honest degradation: retrieval on tables gets worse, and nothing lies.
        min_rows: Fewest rows a table may have and still be summarised. Two rows is a
            label and a value.
        min_cols: Fewest columns. Two columns is a key-and-value list, which already
            reads as prose to an embedding model.
        min_cells: Fewest cells, which is what rules out the 3×3 that clears both
            dimensions and is still nine cells long.
        max_grid_chars: How much of the grid the prompt may carry. A 21×13 table is
            ~9,600 characters on the phase's own fixtures, and the cost of a summary is
            almost entirely its input tokens. Rows past the limit are dropped and *said*
            to be dropped — see :func:`truncate_grid`.
        max_summary_words: The summary is one or two sentences by instruction; this is
            the guard for when the model ignores that, because a runaway summary would
            displace the grid inside the chunk it is supposed to be helping.
        role: Which model role pays for it. ``CHEAP`` — describing a grid that is in front
            of you is the archetypal cheap-model job, and 80 tables on the reasoning model
            is exactly the bill the phase's risk section names.
    """

    enabled: bool = True
    min_rows: int = 3
    min_cols: int = 3
    min_cells: int = 12
    max_grid_chars: int = 6_000
    max_summary_words: int = 90
    role: ModelRole = ModelRole.CHEAP

    def wants_summary(self, table: TableRef | None) -> bool:
        """Return whether ``table`` clears the threshold.

        Args:
            table: The table, or ``None`` for a chunk that is not one.

        Returns:
            ``True`` when a model call is warranted.
        """
        if table is None or not self.enabled:
            return False
        return (
            table.rows >= self.min_rows
            and table.cols >= self.min_cols
            and table.cells >= self.min_cells
        )

    def reason_to_skip(self, table: TableRef) -> str:
        """Return a short, readable reason this table was not summarised.

        Recorded on the chunk rather than left to be inferred: "no summary" and "a
        summary that failed" look identical on a row, and only one of them is a bug.

        Args:
            table: The table that was skipped.

        Returns:
            A one-line explanation.
        """
        if not self.enabled:
            return "table summaries are disabled"
        return (
            f"{table.rows}x{table.cols} ({table.cells} cells) is below the summary "
            f"threshold of {self.min_rows}x{self.min_cols} and {self.min_cells} cells"
        )


#: The shipped defaults. A deployment overrides them through its own configuration —
#: ``app.config.Settings.table_summary_*`` in this platform's host.
DEFAULT_TABLE_SUMMARY_POLICY = TableSummaryPolicy()


def truncate_grid(text: str, *, max_chars: int) -> str:
    """Return the grid trimmed to ``max_chars``, whole rows only, saying what it dropped.

    Trimming is by line rather than by character because half a Markdown row is not a
    table, and the header rows are kept by construction: they come first. The trailing
    note exists so the model is not silently told that a 200-row table has 40 rows — it
    would then describe a range that is not the table's range, in a sentence a reader has
    no way to distrust.

    Args:
        text: The table as Markdown.
        max_chars: The prompt's budget for it.

    Returns:
        The whole grid when it fits, otherwise its first whole rows plus a stated
        omission.
    """
    if len(text) <= max_chars:
        return text
    kept: list[str] = []
    used = 0
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if used + len(line) + 1 > max_chars:
            return "\n".join(kept) + f"\n… ({len(lines) - index} further rows omitted)"
        kept.append(line)
        used += len(line) + 1
    return "\n".join(kept)


#: What the summariser is for, stated as the retrieval job it actually is. The two
#: prohibitions are the ones that matter on a grid of numbers: a model asked to describe a
#: table will otherwise explain what the numbers *mean* in the wider literature (context
#: the table does not contain, which then embeds as if the document had said it), or
#: restate every cell (which is the grid again, at the cost of a model call).
_SUMMARY_SYSTEM = (
    "You describe a table taken from a document, in one or two sentences, so that a "
    "search engine can find it from a question about what it shows.\n"
    "Say what the table reports, what its rows are and what its columns are, and the "
    "single comparison a reader would most likely draw from it.\n"
    "Use only what the table and its caption state. Do not explain the subject matter, "
    "do not restate every cell, and never write a number that is not printed in the "
    "table. Reply with the sentences alone: no preamble, no heading, no markdown."
)


def summary_messages(
    grid: str,
    *,
    table: TableRef,
    policy: TableSummaryPolicy = DEFAULT_TABLE_SUMMARY_POLICY,
    title: str = "",
    section: str = "",
) -> list[dict[str, object]]:
    """Build the chat messages for one table summary.

    The document title and heading path are included because a caption is frequently
    elliptical — "Table 4: Dev and Test accuracies" says nothing about *what* was
    measured, and the section it sits under does.

    Args:
        grid: The table as Markdown.
        table: Its shape, caption and digest.
        policy: The size and role limits.
        title: The document's title, when known.
        section: The heading path the table sits under, when known.

    Returns:
        Messages ready for a :class:`~aegis.retrieval.protocols.CompleteFn`.
    """
    context = [f"Document: {title}" if title else "", f"Section: {section}" if section else ""]
    user = "\n".join(
        [line for line in context if line]
        + [
            f"Shape: {table.rows} rows x {table.cols} columns",
            f"Caption: {table.caption or '(none printed)'}",
            "",
            truncate_grid(grid, max_chars=policy.max_grid_chars),
        ]
    )
    return [
        {"role": "system", "content": _SUMMARY_SYSTEM},
        {"role": "user", "content": user},
    ]


def _clean_summary(content: str, *, max_words: int) -> str:
    """Normalise the model's reply into the sentence(s) that get embedded.

    Args:
        content: The raw completion text.
        max_words: The word ceiling from the policy.

    Returns:
        The summary, whitespace-collapsed and truncated on a word boundary; ``""`` when
        the model returned nothing usable.
    """
    text = _ANY_SPACE.sub(" ", content or "").strip()
    if not text:
        return ""
    words = text.split()
    if len(words) > max_words:
        return " ".join(words[:max_words]) + "…"
    return text


async def summarise_table(
    grid: str,
    *,
    table: TableRef,
    complete: CompleteFn,
    policy: TableSummaryPolicy = DEFAULT_TABLE_SUMMARY_POLICY,
    title: str = "",
    section: str = "",
) -> str:
    """Produce the natural-language summary of one table — **one model call**.

    Deliberately unconditional: the decision not to call is
    :meth:`TableSummaryPolicy.wants_summary`'s, and the decision not to call *again* is
    the cache's. A function that quietly re-checked both would make "how many calls did
    this ingest make" unanswerable from the call site.

    Args:
        grid: The table as Markdown, caption included.
        table: Its shape, caption and digest.
        complete: The injected chat-completion callable.
        policy: The role and size limits.
        title: The document's title, when known.
        section: The heading path the table sits under, when known.

    Returns:
        One or two sentences describing the table, or ``""`` when the model returned
        nothing usable — which the caller records as an absence rather than papering over
        with the grid's own text.
    """
    result = await complete(
        policy.role,
        summary_messages(grid, table=table, policy=policy, title=title, section=section),
        temperature=0.0,
    )
    return _clean_summary(result.content, max_words=policy.max_summary_words)
