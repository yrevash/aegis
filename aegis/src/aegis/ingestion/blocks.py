"""The vocabulary everything downstream reads — and the reason Docling stops at the seam.

A parser is the easiest dependency in a RAG pipeline to marry by accident. Its document
object is convenient, it is already in hand, and passing it one layer further costs
nothing on the day. Two months later the chunker imports ``docling_core``, the citation
renderer knows what a ``ProvenanceItem`` is, and swapping the parser — the thing
``docs/dev_new_docs_v2/phase-04-ingestion.md`` D1 explicitly reserves the right to do —
means rewriting everything it touched. So the types in this module are deliberately
poorer than Docling's: they carry what the pipeline needs and nothing else, and
:mod:`aegis.ingestion.convert` is the only module in the platform allowed to import
Docling at all.

Page and bbox are not optional, and not later
---------------------------------------------

Every block that came from a page carries :attr:`ParsedBlock.page_no` and
:attr:`ParsedBlock.bbox` (D9). That is what makes a citation checkable — "page 7, this
paragraph" rather than "somewhere in this document" — and the reason it is here on the
first commit rather than a later task is arithmetic: threading a field through a write
path that already exists is strictly more work than building the path with the field in
it.

Coordinates are normalised at this boundary
-------------------------------------------

Docling reports PDF-native coordinates, whose origin is the **bottom** left, so ``top``
is a larger number than ``bottom`` and every consumer that draws a rectangle has to know
that. :class:`BBox` is defined top-left-origin instead — the convention of every viewer,
canvas and CSS overlay that will ever draw it — and the conversion happens once, in the
seam. ``__post_init__`` refuses a rectangle that is inside-out, because a silently
flipped box does not fail: it draws in the wrong place, or nowhere, and looks like a
front-end bug for a day.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

from aegis.ingestion.probe import OcrDecision

__all__ = [
    "BBox",
    "BlockKind",
    "FurnitureRun",
    "ParsedBlock",
    "ParsedDocument",
    "ParsedPage",
]


class BlockKind(StrEnum):
    """What a block *is*, in the only distinctions the pipeline acts on.

    Deliberately coarser than Docling's label set. A kind earns a place here only if
    something downstream branches on it: headings build the section tree, tables are
    packed and summarised differently from prose (D8), list items are joined rather than
    split, and page furniture is what :mod:`aegis.ingestion.furniture` removes. Every
    other label collapses into :attr:`TEXT` or :attr:`OTHER` rather than becoming a
    distinction nobody reads.
    """

    HEADING = "heading"
    TEXT = "text"
    LIST_ITEM = "list_item"
    TABLE = "table"
    CAPTION = "caption"
    FOOTNOTE = "footnote"
    FORMULA = "formula"
    CODE = "code"
    PAGE_HEADER = "page_header"
    PAGE_FOOTER = "page_footer"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class BBox:
    """A rectangle on a page, in PDF points, with the origin at the **top** left.

    Attributes:
        left: Distance from the left page edge to the left side of the box.
        top: Distance from the *top* page edge to the top side of the box.
        right: Distance from the left page edge to the right side of the box.
        bottom: Distance from the top page edge to the bottom side of the box.
    """

    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self) -> None:
        """Refuse an inside-out rectangle.

        Raises:
            ValueError: If ``right < left`` or ``bottom < top`` — which is what a
                bottom-left-origin box looks like when it reaches here unconverted.
        """
        if self.right < self.left or self.bottom < self.top:
            raise ValueError(
                f"inside-out bbox (left={self.left}, top={self.top}, right={self.right}, "
                f"bottom={self.bottom}); coordinates here are top-left origin, so a box "
                f"whose top exceeds its bottom is an unconverted PDF-native rectangle"
            )

    @property
    def width(self) -> float:
        """The box width in points."""
        return self.right - self.left

    @property
    def height(self) -> float:
        """The box height in points."""
        return self.bottom - self.top

    def merge(self, other: BBox) -> BBox:
        """Return the smallest box containing both.

        Docling can report several provenance rectangles for one logical block — a
        paragraph broken across a column, most often. Keeping only the first would put
        the citation highlight on part of the text; the union is the honest answer.

        Args:
            other: The box to absorb.

        Returns:
            The union rectangle.
        """
        return BBox(
            left=min(self.left, other.left),
            top=min(self.top, other.top),
            right=max(self.right, other.right),
            bottom=max(self.bottom, other.bottom),
        )


@dataclass(frozen=True, slots=True)
class ParsedPage:
    """One page of the source document, with what the text-layer probe found on it.

    Attributes:
        page_no: 1-based page number, as printed in every citation.
        width: Page width in PDF points.
        height: Page height in PDF points.
        char_count: Characters the raw text layer yields for this page — 0 for a scanned
            page. See :mod:`aegis.ingestion.probe`.
        has_text_layer: Whether :attr:`char_count` cleared the probe's threshold.
    """

    page_no: int
    width: float
    height: float
    char_count: int
    has_text_layer: bool


@dataclass(frozen=True, slots=True)
class ParsedBlock:
    """One extracted region of the document, with the page and box it came from.

    Attributes:
        kind: What the block is; see :class:`BlockKind`.
        text: The block's text. For :attr:`BlockKind.TABLE` this is the table rendered
            as Markdown, which is what gets embedded.
        page_no: 1-based page the block sits on.
        bbox: Where on that page, top-left origin. ``None`` only for a block Docling
            reported with no provenance at all, which is recorded rather than guessed.
        level: Heading depth for :attr:`BlockKind.HEADING`, else ``None``. Level 1 is
            the top of the tree.
        heading_path: The enclosing headings, outermost first, excluding the block
            itself. This is the structural context D7's chunk prefix is built from.
        table_shape: ``(rows, columns)`` for a table block, else ``None``.
    """

    kind: BlockKind
    text: str
    page_no: int
    bbox: BBox | None = None
    level: int | None = None
    heading_path: tuple[str, ...] = ()
    table_shape: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class FurnitureRun:
    """A repeating header/footer that was removed, recorded so the removal is visible.

    Attributes:
        pattern: The normalised form that repeated — digits collapsed, so the page
            numbers of "Page 7" and "Page 8" share one pattern.
        sample: One real occurrence, verbatim, so a human can recognise it.
        pages: The pages it was removed from.
        band: ``"header"`` or ``"footer"``.
    """

    pattern: str
    sample: str
    pages: tuple[int, ...]
    band: str


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """A parsed PDF, in types that owe nothing to the parser that produced it.

    Attributes:
        source_name: The file name the bytes arrived under.
        pages: Every page, in order.
        blocks: Every block, in reading order.
        ocr: The per-document OCR decision (D3) and the evidence for it.
        removed_furniture: Running headers/footers stripped by
            :mod:`aegis.ingestion.furniture`.
        parse_seconds: Wall clock spent inside the parser, for the ingest log.
        parser: Name and version of the parser that produced this, recorded because a
            re-parse under a different version is a different result and the ``chunks``
            it produced are not interchangeable.
    """

    source_name: str
    pages: tuple[ParsedPage, ...]
    blocks: tuple[ParsedBlock, ...]
    ocr: OcrDecision
    removed_furniture: tuple[FurnitureRun, ...] = ()
    parse_seconds: float = 0.0
    parser: str = ""

    @property
    def page_count(self) -> int:
        """How many pages were parsed."""
        return len(self.pages)

    @property
    def heading_histogram(self) -> dict[int, int]:
        """Heading count per level — the D2 signal, and the D-parse gate's first input.

        A real document comes back multi-level. ``{1: N}`` means the heading hierarchy
        never turned on, and ``{1: 16, 2: 4}`` on a deeply structured document is D2's
        silent partial failure: it looks plausible and it is wrong. Neither raises
        anywhere, which is exactly why the number is computed and recorded rather than
        trusted.

        Returns:
            Level to count, ascending by level.
        """
        counts = Counter(
            block.level
            for block in self.blocks
            if block.kind is BlockKind.HEADING and block.level is not None
        )
        return {level: counts[level] for level in sorted(counts)}

    @property
    def table_count(self) -> int:
        """How many table blocks were extracted."""
        return sum(1 for block in self.blocks if block.kind is BlockKind.TABLE)

    def text(self) -> str:
        """Return the document's text in reading order, one block per paragraph.

        Returns:
            Every block's text joined by blank lines.
        """
        return "\n\n".join(block.text for block in self.blocks if block.text)
