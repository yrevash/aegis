"""Document chunking and deduplication for ingestion.

LightRAG does its own token chunking internally, but we chunk (and dedup) *before*
handing text to it so that (a) content validation and provenance run per-chunk, and
(b) exact/near-duplicate passages never reach extraction or the graph twice — cheaper
and a small poisoning-surface reduction.

Three entry points live here:

* :func:`chunk_text` — the original fixed-size sliding word window (kept for callers and
  as the last-resort splitter for an oversized paragraph).
* :func:`chunk_structured` — the **SOTA** path used by ingestion: *structure-aware,
  recursive* chunking. It respects Markdown headings (each chunk knows its section path),
  packs whole paragraphs/sentences up to a target size with word overlap, and prepends the
  section path to the chunk text (lightweight *contextual retrieval*) so the embedding and
  the graph extractor both see where the passage sits in the document. This mirrors the
  current recommendation — start from a recursive/structure-aware splitter at ~400–512
  tokens with 10–20 % overlap, add section context — rather than naive fixed windows
  (see ``docs/architecture/synthetic-data.md`` refs).
* :func:`chunk_sections` — the same packer, fed from a parsed PDF
  (:class:`~aegis.ingestion.blocks.ParsedDocument`) instead of from Markdown. The
  structure is already known there — Docling reports heading levels, page numbers and
  bounding boxes — so this path does not re-derive it from ``#`` markers; it groups the
  blocks into sections, hands the *same* :func:`_pack_units` the same kind of units, and
  threads each block's page and box onto the chunk that used it.

**The packer is deliberately not re-written for the parsed path** (phase 4 D11). Semantic,
proposition (DenseX) and LLM-driven chunking all *lose* to plain structural chunking on
in-corpus retrieval at 10²–10⁴× the cost — propositions by 15–27%, LumberChunker at 1,600×
the runtime for no gain. Structural packing is already on the winning side of that result,
so what phase 4 adds here is an adapter and a richer prefix, not a new algorithm.

Chunk size is measured in whitespace-delimited words as a portable, dependency-free
approximation of tokens (roughly ~0.75 tokens/word for English).

The prefix, and why its *shape* is fixed
----------------------------------------

:func:`chunk_structured` prepends the heading path alone (``[Returns > Refund window]``).
:func:`chunk_sections` prepends all four fields of D7 — ``[title · type · date · heading
path]`` — because the ECIR 2026 field ablation (``arXiv:2601.11863``) removed them one at a
time and found that dropping *company* and *year* degraded retrieval severely where dropping
*section titles* cost only a modest Context@K drop. We were shipping the junior partner of
that technique and omitting both senior ones, at zero model-call cost. Measured effect of
adding them: Context@5 **33.3% → 55.0%**.

A missing field therefore does **not** disappear from the prefix: it is rendered as a
placeholder (``untitled``, ``untyped``, ``undated``, ``unsectioned``) so the prefix keeps
four fields and three separators no matter what the document row happens to carry. This is
not cosmetic. The prefix is *inside the embedded text*, so it is part of what the vector
means; a corpus where some chunks read ``[title · type · date · section]`` and others read
``[title · section]`` places two different sentence shapes in one space and asks the
similarity to ignore the difference. A constant placeholder is the opposite problem — it
appears in every chunk that lacks the field, so it is near-zero-IDF for the keyword arm and
a constant offset for the dense one, which is exactly what "carries no information" should
look like.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from aegis.ingestion.blocks import BBox, BlockKind, ParsedBlock, ParsedDocument
from aegis.ingestion.tables import TableRef, table_caption, table_digest

_WHITESPACE = re.compile(r"\s+")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WORD_RE = re.compile(r"[a-z0-9]+")


def _content_id(text: str) -> str:
    """Return a short, stable content-addressed id for `text`."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _normalise(text: str) -> str:
    """Collapse whitespace and lowercase for duplicate detection."""
    return _WHITESPACE.sub(" ", text).strip().lower()


def chunk_text(text: str, *, chunk_size: int = 400, overlap: int = 60) -> list[str]:
    """Split `text` into overlapping word-windows (fixed-size baseline).

    Args:
        text: The document text to chunk.
        chunk_size: Target window size in words (approximate tokens).
        overlap: Number of words shared between consecutive windows.

    Returns:
        A list of chunk strings (empty if `text` has no words).

    Raises:
        ValueError: If `chunk_size <= 0` or `overlap` is negative or `>= chunk_size`.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be in [0, chunk_size)")

    words = text.split()
    if not words:
        return []

    step = chunk_size - overlap
    chunks: list[str] = []
    for start in range(0, len(words), step):
        window = words[start : start + chunk_size]
        if window:
            chunks.append(" ".join(window))
        if start + chunk_size >= len(words):
            break
    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# Structure-aware recursive chunking (the SOTA ingestion path)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChunkPiece:
    """One structure-aware chunk plus the provenance captured while splitting.

    Attributes:
        text: The raw chunk body (no section prefix).
        ordinal: 0-based position of this chunk within its document.
        section: The heading path this chunk sits under (``"A > B"``), or ``""`` for
            preamble text before the first heading.
        word_start: Word offset of the chunk's first word within the document body
            (headings excluded, sections concatenated in reading order). Consecutive
            chunks overlap by design, so consecutive spans overlap by the overlap width;
            every span stays inside the document — ``word_start + word_count`` never
            exceeds the body's word count — because the shared words are counted once.
        word_count: Number of words in :attr:`text`.
    """

    text: str
    ordinal: int
    section: str = ""
    word_start: int = 0
    word_count: int = 0

    def contextualized(self) -> str:
        """Return the chunk prefixed with its section path (contextual retrieval).

        Prepending the heading path lets the embedding model and the graph extractor
        see where the passage lives, which measurably improves recall for
        section-scoped queries. When the chunk has no heading path the text is
        returned unchanged (so heading-free documents are byte-identical to their raw
        chunks — keeping deduplication and existing behaviour stable).
        """
        if not self.section:
            return self.text
        return f"[{self.section}]\n{self.text}"

    def content_id(self) -> str:
        """Return the stable content-addressed id for this chunk (section + body)."""
        return _content_id(_normalise(self.contextualized()))

    def indexed_id(self) -> str:
        """Return the id this chunk is published into the knowledge store under.

        :meth:`content_id` plus the chunk's **ordinal**, and the two differ for exactly
        one reason: a content address is a statement that two identical texts are one
        thing, which is right for de-duplication and wrong for a primary key.

        This is live on a shipped fixture rather than hypothetical. ``chunk_sections`` at
        the production defaults over ``census-income-tables.pdf`` yields **182 chunks
        under 162 distinct** :meth:`content_id` values — repeated table furniture
        ("Footnotes available at end of table.", the "(Populations in thousands…)"
        header) reprinted under one continued-table heading path. The ingestion ``chunk``
        stage does **not** call :func:`dedup_pieces` — it writes every piece as a row —
        so nothing upstream removes them, and the ``index`` stage then keys a global
        store by that id. The *text* lost is a duplicate; what is lost is the row identity
        behind it — ordinal, page number, bounding boxes — which is exactly what a
        citation resolves through.

        The ordinal is deliberately **not** folded into :meth:`content_id`:
        :func:`dedup_pieces` and the ingestion ledger key off that value, so salting it
        would silently disable exact-duplicate detection.

        Still a pure function of ``(ordinal, section, body)``, so a re-chunk of an
        unchanged document re-publishes over itself rather than duplicating — the
        property the ``index`` stage depends on.
        """
        return _content_id(f"{self.ordinal}\x00{_normalise(self.contextualized())}")


def _strip_frontmatter(text: str) -> str:
    """Drop a leading ``---``-delimited YAML frontmatter block, if present."""
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return text
    _, _, rest = stripped.partition("---")
    _, sep, body = rest.partition("---")
    return body if sep else text


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Split Markdown ``text`` into ``(heading_path, body)`` sections.

    Headings (``#``–``######``) open a new section; the heading *path* accumulates the
    nesting (``"Guide > Escalations"``). Text before the first heading is emitted under an
    empty path. Plain text with no headings yields a single ``("", text)`` section.
    """
    sections: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []
    current_path = ""
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            sections.append((current_path, body))

    for line in text.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            flush()
            buffer = []
            level = len(heading.group(1))
            title = heading.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            current_path = " > ".join(title for _, title in stack)
        else:
            buffer.append(line)
    flush()
    return sections


def _sentence_units(paragraph: str, chunk_size: int) -> list[str]:
    """Break an oversized paragraph into sentence (then word-window) units."""
    units: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(paragraph.strip()):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence.split()) <= chunk_size:
            units.append(sentence)
        else:  # a single mega-sentence: fall back to fixed word windows
            units.extend(chunk_text(sentence, chunk_size=chunk_size, overlap=0))
    return units


@dataclass(frozen=True, slots=True)
class _PackedWindow:
    """One packed window, and which of the caller's units went into it.

    Attributes:
        text: The window text.
        carried: How many of the window's leading words were re-used from the previous
            window's tail. Those words are *not* new text, so the caller must not advance
            a document word offset by them — counting them twice is what made the
            reported spans drift past the end of the document.
        unit_indices: Positions, in the caller's ``units`` list, of the units whose text
            this window holds — excluding the carried tail, which came from the previous
            window's units. :func:`chunk_structured` ignores this; :func:`chunk_sections`
            is how a chunk gets back to the blocks it was packed from, and therefore to
            their page numbers and bounding boxes.
    """

    text: str
    carried: int
    unit_indices: tuple[int, ...]


def _pack_units(units: list[str], chunk_size: int, overlap: int) -> list[_PackedWindow]:
    """Greedily pack text units into windows ≤ ``chunk_size`` words, seeding overlap.

    Whole units (paragraphs/sentences) are kept intact so a chunk never straddles a
    sentence boundary mid-word. When a window fills, the next one is seeded with the
    trailing ``overlap`` words of the previous window to preserve cross-chunk context.

    Args:
        units: The text units to pack, in reading order.
        chunk_size: Target window size in words.
        overlap: Words carried from a full window into the next one.

    Returns:
        One :class:`_PackedWindow` per window, in order.
    """
    windows: list[_PackedWindow] = []
    current: list[str] = []
    current_words = 0
    carried = 0
    indices: list[int] = []
    for index, unit in enumerate(units):
        unit_words = len(unit.split())
        if current and current_words + unit_words > chunk_size:
            window = " ".join(current)
            windows.append(_PackedWindow(window, carried, tuple(indices)))
            indices = []
            if overlap > 0:
                tail = " ".join(window.split()[-overlap:])
                current = [tail]
                current_words = len(tail.split())
                carried = current_words
            else:
                current = []
                current_words = 0
                carried = 0
        current.append(unit)
        current_words += unit_words
        indices.append(index)
    if current:
        windows.append(_PackedWindow(" ".join(current), carried, tuple(indices)))
    return windows


def chunk_structured(
    text: str, *, chunk_size: int = 400, overlap: int = 60
) -> list[ChunkPiece]:
    """Structure-aware recursive chunking with section provenance (SOTA ingest path).

    The document is split by Markdown headings, then each section's body is packed
    paragraph-by-paragraph (falling back to sentences, then word windows) into chunks
    targeting ``chunk_size`` words, with ``overlap`` words carried over between
    consecutive chunks (so a window holds up to ``chunk_size + overlap`` words). Whole
    sentences/paragraphs are kept intact; every chunk records its heading path.

    Args:
        text: The document text (Markdown or plain).
        chunk_size: Target chunk size in words (~tokens). ~400 is a good default.
        overlap: Words of overlap carried between consecutive chunks (10–20 %).

    Returns:
        The document's chunks as :class:`ChunkPiece` records, in reading order.

    Raises:
        ValueError: If ``chunk_size <= 0`` or ``overlap`` is out of ``[0, chunk_size)``.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be in [0, chunk_size)")

    body = _strip_frontmatter(text)
    pieces: list[ChunkPiece] = []
    ordinal = 0
    running_words = 0
    for section, section_body in _split_sections(body):
        paragraphs = [p.strip() for p in _PARAGRAPH_SPLIT.split(section_body) if p.strip()]
        units: list[str] = []
        for paragraph in paragraphs:
            if len(paragraph.split()) <= chunk_size:
                units.append(paragraph)
            else:
                units.extend(_sentence_units(paragraph, chunk_size))
        for window in _pack_units(units, chunk_size, overlap):
            word_count = len(window.text.split())
            # ``carried`` leading words are a repeat of the previous window's tail, so
            # this window actually starts that many words BEFORE the previous one ended.
            # Advancing by the full word_count would count every overlap twice and push
            # the reported spans past the end of the document — offsets that cannot
            # locate the chunk they claim to cite.
            word_start = max(0, running_words - window.carried)
            pieces.append(
                ChunkPiece(
                    text=window.text,
                    ordinal=ordinal,
                    section=section,
                    word_start=word_start,
                    word_count=word_count,
                )
            )
            ordinal += 1
            running_words = word_start + word_count
    return pieces


# ─────────────────────────────────────────────────────────────────────────────
# The parsed-document path: pre-structured sections in, enriched chunks out
# ─────────────────────────────────────────────────────────────────────────────

#: Separator between heading levels in a section path, as :func:`chunk_structured` writes it.
_SECTION_SEPARATOR = " > "
#: Separator between the four fields of the D7 prefix.
_PREFIX_SEPARATOR = " · "

#: What each prefix field reads when the document does not carry it. A field is never
#: dropped — see the module docstring for why the prefix's shape is fixed.
_UNKNOWN_TITLE = "untitled"
_UNKNOWN_TYPE = "untyped"
_UNKNOWN_DATE = "undated"
_UNKNOWN_SECTION = "unsectioned"

#: Characters a document's own metadata may not contribute to the prefix, because they are
#: the prefix's own punctuation: a title containing "·" or "]" would otherwise forge a field
#: boundary that a citation renderer (4.12) then reads back as structure.
_PREFIX_PUNCTUATION = str.maketrans({"·": "-", "[": "(", "]": ")"})

#: Field widths, in words and characters. A title is a title; a mis-detected heading that
#: is really a paragraph would otherwise cost every chunk in the document hundreds of
#: tokens of prefix — the trade-off D7 names, spent at four short fields and no more.
_TITLE_LIMITS = (24, 180)
_TYPE_LIMITS = (8, 64)
_DATE_LIMITS = (4, 32)
_SECTION_LIMITS = (32, 220)

#: Blocks that contribute no body text to a chunk. Headings *are* the section path (they
#: reach the chunk through the prefix, exactly as ``#`` lines do in
#: :func:`chunk_structured`), and page furniture is whatever survived
#: :mod:`aegis.ingestion.furniture` — repeating it in every chunk is what that module
#: exists to prevent.
_NON_BODY_KINDS = frozenset({BlockKind.HEADING, BlockKind.PAGE_HEADER, BlockKind.PAGE_FOOTER})


@dataclass(frozen=True, slots=True)
class PageSpan:
    """One page a chunk's text came from, and where on it.

    Attributes:
        page_no: 1-based page number, as printed in the citation.
        bbox: The union of the boxes of this chunk's blocks on that page — top-left
            origin, as :class:`~aegis.ingestion.blocks.BBox` defines it. ``None`` only
            when every contributing block on the page reached us with no provenance,
            which is recorded rather than guessed at.
    """

    page_no: int
    bbox: BBox | None


@dataclass(frozen=True)
class DocumentContext:
    """The document-level fields the D7 prefix carries, beside the heading path.

    All three are optional and each is rendered as a placeholder when absent, so a
    document with no title and no date still produces a four-field prefix. See the module
    docstring for why that matters more than it looks.

    Attributes:
        title: What the document is called. :meth:`from_parsed` derives it from the
            parse when the caller has nothing better; a caller that *does* — the upload
            route knows the file name the tenant chose, and the ``documents`` row stores
            it — should pass it.
        doc_type: The document's class in the tenant's own vocabulary — "policy",
            "10-K", "lab report". This is the field the ECIR ablation found expensive to
            lose, and it cannot be inferred from the bytes: a MIME type is
            ``application/pdf`` for every document in the corpus and so discriminates
            nothing. Left empty it degrades to ``untyped`` rather than to a confident
            constant.
        doc_date: The date the document is *about* or was issued on — not the upload
            timestamp, which says only when someone got round to sending it.
    """

    title: str = ""
    doc_type: str = ""
    doc_date: date | None = None

    @classmethod
    def from_parsed(
        cls,
        document: ParsedDocument,
        *,
        doc_type: str = "",
        doc_date: date | None = None,
    ) -> DocumentContext:
        """Derive what the parse itself can tell us, and take the rest from the caller.

        The title is the document's first heading — which is what Docling's ``title``
        label maps to, and on every fixture in ``tests/fixtures/pdfs`` is the real title
        printed on page 1. A document that came back with no heading at all falls back to
        its file name without the extension, which is a weak title but a true one; there
        is nothing else in the bytes that claims to name the document, and inventing one
        with a model call is precisely the cost D7 exists to avoid.

        Args:
            document: The parsed document.
            doc_type: The document's class, if the caller knows it.
            doc_date: The document's own date, if the caller knows it.

        Returns:
            The context to build prefixes from.
        """
        title = next(
            (
                block.text
                for block in document.blocks
                if block.kind is BlockKind.HEADING and block.text.strip()
            ),
            "",
        )
        if not title:
            title = document.source_name.rsplit(".", 1)[0]
        return cls(title=title, doc_type=doc_type, doc_date=doc_date)


def _render_date(value: date | None) -> str:
    """Render a document date as ISO ``YYYY-MM-DD`` (empty string for ``None``).

    A :class:`~datetime.datetime` is narrowed to its date first: the hour a filing was
    uploaded is not part of what the document is, and a bare ``isoformat()`` would put
    ``T00:00:00`` into every embedded chunk of the corpus.
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = value.date()
    return value.isoformat()


def _prefix_field(value: str, *, fallback: str, limits: tuple[int, int]) -> str:
    """Normalise one prefix field, or return ``fallback`` when it is empty.

    Args:
        value: The raw field value.
        fallback: What an empty field reads as, so the prefix keeps its shape.
        limits: ``(max_words, max_chars)`` for this field.

    Returns:
        The field as it appears in the prefix.
    """
    text = _WHITESPACE.sub(" ", value or "").strip().translate(_PREFIX_PUNCTUATION)
    if not text:
        return fallback
    max_words, max_chars = limits
    words = text.split()
    if len(words) > max_words:
        text = " ".join(words[:max_words]) + "…"
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


def chunk_prefix(context: DocumentContext, section: str) -> str:
    """Build the D7 chunk prefix: ``[title · type · date · heading path]``.

    Always four fields and three separators. A field the document does not carry reads
    as its placeholder rather than as empty space or as a missing slot — the prefix is
    embedded *with* the chunk, so two documents whose prefixes have different shapes are
    not comparable in the vector space, and a ragged ``[ ·  · 2024 · Intro]`` is worse
    than either because it also perturbs tokenisation.

    Args:
        context: The document-level fields.
        section: The chunk's heading path (``"A > B"``), or ``""`` for text that sits
            before the first heading.

    Returns:
        The prefix, brackets included, with no trailing newline.
    """
    fields = (
        _prefix_field(context.title, fallback=_UNKNOWN_TITLE, limits=_TITLE_LIMITS),
        _prefix_field(context.doc_type, fallback=_UNKNOWN_TYPE, limits=_TYPE_LIMITS),
        _prefix_field(
            _render_date(context.doc_date), fallback=_UNKNOWN_DATE, limits=_DATE_LIMITS
        ),
        _prefix_field(section, fallback=_UNKNOWN_SECTION, limits=_SECTION_LIMITS),
    )
    return "[" + _PREFIX_SEPARATOR.join(fields) + "]"


@dataclass(frozen=True)
class SectionChunk(ChunkPiece):
    """A chunk packed from parsed blocks: the same piece, plus where it came from.

    It *is* a :class:`ChunkPiece` — same body, ordinal, section path and word span, so
    :func:`dedup_pieces` and the ingestion ledger treat it identically — with two
    additions that only exist once a real page has been parsed.

    Attributes:
        spans: One :class:`PageSpan` per page this chunk's text came from, ascending, each
            carrying the union of the boxes of the chunk's blocks on that page. A chunk
            that straddles a page break has two, and both are true; keeping only the first
            would put half of a citation's highlight nowhere. This is what makes a
            citation checkable (D9) and what the verbatim span check (4.14) and the
            citation renderer (4.12) read.
        prefix: The D7 prefix as it will be embedded, built once at chunk time because
            the document context it needs is not reachable from the chunk later.
        table: Set when this chunk **is** a table (D8, task 4.10) — its shape, its
            caption and the content digest its natural-language summary is cached under.
            ``None`` for prose. A table chunk holds exactly one table and nothing else,
            so this is a property of the whole chunk rather than of part of it.
    """

    spans: tuple[PageSpan, ...] = ()
    prefix: str = ""
    table: TableRef | None = None

    @property
    def page_no(self) -> int | None:
        """The first page this chunk's text appears on, or ``None`` if it has no spans."""
        return self.spans[0].page_no if self.spans else None

    @property
    def bbox(self) -> BBox | None:
        """The box on :attr:`page_no`, or ``None`` when that page carried no provenance."""
        return self.spans[0].bbox if self.spans else None

    def contextualized(self) -> str:
        """Return the enriched prefix followed by the chunk body — what gets embedded.

        Overrides :meth:`ChunkPiece.contextualized`, which prepends the heading path
        alone. The prefix is *always* prepended here, including for a chunk with no
        heading path, because dropping it for those chunks is the shape change the module
        docstring rules out.
        """
        if not self.prefix:
            return self.text
        return f"{self.prefix}\n{self.text}"


def _section_runs(
    blocks: Sequence[ParsedBlock],
) -> list[tuple[str, list[ParsedBlock]]]:
    """Group blocks into consecutive runs that share one heading path.

    A run is broken by **every** heading block, not merely by a change of path: two
    sibling sections that happen to carry the same title ("Notes" under two different
    chapters at the same depth) are two sections, and a chunk that spanned both would
    answer a question about the first with text from the second.

    Args:
        blocks: The document's blocks, in reading order.

    Returns:
        ``(heading_path, blocks)`` per run, in reading order. The path is ``""`` for
        blocks that sit before the document's first heading.
    """
    runs: list[tuple[str, list[ParsedBlock]]] = []
    current: list[ParsedBlock] = []
    current_path = ""
    for block in blocks:
        if block.kind is BlockKind.HEADING:
            if current:
                runs.append((current_path, current))
                current = []
            continue
        if block.kind in _NON_BODY_KINDS or not block.text.strip():
            continue
        path = _SECTION_SEPARATOR.join(block.heading_path)
        if current and path != current_path:
            runs.append((current_path, current))
            current = []
        current_path = path
        current.append(block)
    if current:
        runs.append((current_path, current))
    return runs


def _block_units(
    blocks: Sequence[ParsedBlock], chunk_size: int
) -> list[tuple[str, ParsedBlock]]:
    """Turn one section's blocks into packable units, each remembering its block.

    A block is a unit. An oversized *prose* block is split by sentence (then by word
    window) exactly as an oversized paragraph is in :func:`chunk_structured`, and every
    piece keeps the page and box of the block it came out of. A **table** block is never
    split: its text is a Markdown grid, and sentence-splitting a grid produces rows that
    have lost their header. An oversized table therefore becomes one oversized chunk,
    which is the honest outcome: a table is answered from its numbers, and half a table
    cannot be. What task 4.10 adds is a natural-language summary written *in front of*
    that grid at ingest — see :mod:`aegis.ingestion.tables` — never in place of it.

    Args:
        blocks: One section's blocks, in reading order.
        chunk_size: Target chunk size in words.

    Returns:
        ``(text, block)`` per unit, in reading order.
    """
    units: list[tuple[str, ParsedBlock]] = []
    for block in blocks:
        text = block.text.strip()
        if not text:
            continue
        if block.kind is BlockKind.TABLE or len(text.split()) <= chunk_size:
            units.append((text, block))
            continue
        for piece in _sentence_units(text, chunk_size):
            units.append((piece, block))
    return units


def _packing_groups(
    units: list[tuple[str, ParsedBlock]],
) -> list[tuple[int, list[tuple[str, ParsedBlock]]]]:
    """Split one section's units into runs that may be packed together.

    **A table is packed with nothing else** (D8, task 4.10). Left in the general run it
    would be greedily merged with whatever prose happened to precede it — so the chunk
    that holds "Table 2: BLEU scores…" would also hold the last paragraph of §6.1, its
    citation would name two pages, and it could not be labelled a table because it is
    only partly one. Worse in the other direction: the grid's own tail becomes the
    ``overlap`` seed of the *next* prose chunk, which then opens with three rows of
    orphaned numbers that belong to a table it does not contain.

    Isolating it costs a small number of extra chunks on a table-dense document and buys
    two things nothing downstream can reconstruct: a chunk that *is* a table (so the
    shape, the caption and the summary are properties of the whole row), and a page span
    that is the table's page rather than the union of the table's and its neighbour's.

    Args:
        units: One section's units, in reading order.

    Returns:
        ``(offset, units)`` per group, in reading order, where ``offset`` is the group's
        first position in ``units`` — the caller needs it to map a window's unit indices
        back to the blocks they came from.
    """
    groups: list[tuple[int, list[tuple[str, ParsedBlock]]]] = []
    current: list[tuple[str, ParsedBlock]] = []
    start = 0
    for index, unit in enumerate(units):
        if unit[1].kind is BlockKind.TABLE:
            if current:
                groups.append((start, current))
                current = []
            groups.append((index, [unit]))
            start = index + 1
            continue
        if not current:
            start = index
        current.append(unit)
    if current:
        groups.append((start, current))
    return groups


def _table_ref(block: ParsedBlock) -> TableRef:
    """Build the chunk's :class:`~aegis.ingestion.tables.TableRef` from a table block.

    The shape comes from TableFormer, through
    :attr:`~aegis.ingestion.blocks.ParsedBlock.table_shape`. A block that reached here
    without one is recorded as ``(0, 0)`` rather than guessed at from the pipe count:
    a shape inferred from the Markdown would be confidently wrong on exactly the merged
    and nested headers the shape is wanted for, and ``(0, 0)`` fails the summary
    threshold, which is the safe direction to fail in.

    Args:
        block: The table block.

    Returns:
        Its shape, caption and content digest.
    """
    rows, cols = block.table_shape or (0, 0)
    return TableRef(
        rows=rows,
        cols=cols,
        caption=table_caption(block.text),
        digest=table_digest(block.text),
    )


def _carried_indices(
    units: list[tuple[str, ParsedBlock]], previous: tuple[int, ...], carried: int
) -> list[int]:
    """Return the previous window's units that supplied this window's carried tail.

    The overlap words at the head of a window are a repeat of the previous window's last
    ``carried`` words, and they are real text sitting in this chunk. If the previous
    window ended on page 3 and this one is otherwise page 4, a chunk whose first sentence
    is on page 3 must say page 3 — a citation that omits the page its own opening words
    are printed on is the kind of provenance hole that is only found by a reader.

    Args:
        units: The section's units.
        previous: The units of the previous *chunk*, in reading order — its own carried
            units included, not just the ones it introduced. A window whose new content
            is shorter than the overlap width carries words from two chunks back, and
            walking only the units it introduced would stop short of them.
        carried: How many words were carried.

    Returns:
        The contributing unit positions, in reading order.
    """
    if carried <= 0:
        return []
    taken: list[int] = []
    words = 0
    for index in reversed(previous):
        taken.append(index)
        words += len(units[index][0].split())
        if words >= carried:
            break
    return list(reversed(taken))


def _spans_of(blocks: Sequence[ParsedBlock]) -> tuple[PageSpan, ...]:
    """Collapse contributing blocks into one span per page, boxes merged.

    Args:
        blocks: The blocks whose text is in the chunk, in any order.

    Returns:
        One :class:`PageSpan` per page touched, ascending by page number.
    """
    boxes: dict[int, BBox | None] = {}
    for block in blocks:
        if block.page_no not in boxes:
            boxes[block.page_no] = block.bbox
        elif block.bbox is not None:
            known = boxes[block.page_no]
            boxes[block.page_no] = block.bbox if known is None else known.merge(block.bbox)
    return tuple(PageSpan(page_no=page, bbox=boxes[page]) for page in sorted(boxes))


def chunk_sections(
    document: ParsedDocument,
    *,
    context: DocumentContext | None = None,
    chunk_size: int = 400,
    overlap: int = 60,
) -> list[SectionChunk]:
    """Pack a parsed document's sections into enriched, provenance-carrying chunks.

    This is the ingestion path for PDFs (tasks 4.3 and 4.4). The structure has already
    been recovered by the parser — heading levels, reading order, page numbers and boxes
    — so nothing here re-derives it: the blocks are grouped into runs that share a heading
    path, each run is handed to the *same* :func:`_pack_units` that
    :func:`chunk_structured` uses, and each resulting window is given back the page and
    box of every block whose text it holds.

    Three properties are worth stating because downstream work depends on them:

    * **A chunk never spans two sections.** Packing restarts at every heading, so a
      window cannot mix text from two sections, however short they are.
    * **Provenance is not lost to the overlap.** A window's leading ``overlap`` words are
      a repeat of the previous window's tail; the blocks that supplied them are counted
      into this chunk's spans too, so :attr:`SectionChunk.spans` covers every page whose
      text is actually in the chunk.
    * **A table is its own chunk** (D8, task 4.10), carrying its shape, its caption and
      its content digest on :attr:`SectionChunk.table`. It is packed with no prose before
      it and seeds no overlap into what follows — see :func:`_packing_groups`.

    Args:
        document: The parsed document (:func:`aegis.ingestion.parse_pdf`'s output).
        context: The document-level prefix fields. Defaults to
            :meth:`DocumentContext.from_parsed`, which derives a title from the parse and
            leaves type and date to degrade to their placeholders.
        chunk_size: Target chunk size in words (~tokens).
        overlap: Words carried between consecutive chunks within a section.

    Returns:
        The document's chunks in reading order, ordinals contiguous from 0.

    Raises:
        ValueError: If ``chunk_size <= 0`` or ``overlap`` is out of ``[0, chunk_size)``.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be in [0, chunk_size)")

    fields = context if context is not None else DocumentContext.from_parsed(document)
    chunks: list[SectionChunk] = []
    ordinal = 0
    running_words = 0
    for section, blocks in _section_runs(document.blocks):
        units = _block_units(blocks, chunk_size)
        if not units:
            continue
        prefix = chunk_prefix(fields, section)
        previous: tuple[int, ...] = ()
        for offset, group in _packing_groups(units):
            table = (
                _table_ref(group[0][1])
                if len(group) == 1 and group[0][1].kind is BlockKind.TABLE
                else None
            )
            for window in _pack_units([text for text, _ in group], chunk_size, overlap):
                word_count = len(window.text.split())
                # Same correction as chunk_structured: the carried words are not new
                # text, so the offset walks back over them instead of counting them
                # twice.
                word_start = max(0, running_words - window.carried)
                contributing = _carried_indices(units, previous, window.carried)
                contributing.extend(offset + index for index in window.unit_indices)
                chunks.append(
                    SectionChunk(
                        text=window.text,
                        ordinal=ordinal,
                        section=section,
                        word_start=word_start,
                        word_count=word_count,
                        spans=_spans_of([units[index][1] for index in contributing]),
                        prefix=prefix,
                        table=table,
                    )
                )
                ordinal += 1
                running_words = word_start + word_count
                previous = tuple(contributing)
    return chunks

# ─────────────────────────────────────────────────────────────────────────────
# Robust deduplication (exact + near-duplicate)
# ─────────────────────────────────────────────────────────────────────────────

#: Jaccard similarity at/above which two chunks count as near-duplicates.
_NEAR_DUP_THRESHOLD = 0.9
#: Shingle width (in words) for near-duplicate detection.
_SHINGLE_WIDTH = 3


@dataclass
class DedupResult:
    """Outcome of :func:`dedup_pieces`: what was kept vs dropped, and why."""

    kept: list[ChunkPiece] = field(default_factory=list)
    exact_duplicates: int = 0
    near_duplicates: int = 0


def _shingles(text: str, width: int = _SHINGLE_WIDTH) -> frozenset[str]:
    """Return the set of word-``width`` shingles of ``text`` (for Jaccard overlap)."""
    tokens = _WORD_RE.findall(text.lower())
    if len(tokens) < width:
        return frozenset({" ".join(tokens)}) if tokens else frozenset()
    return frozenset(
        " ".join(tokens[i : i + width]) for i in range(len(tokens) - width + 1)
    )


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Return the Jaccard similarity of two shingle sets (0.0 when either is empty)."""
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    if not intersection:
        return 0.0
    return intersection / len(a | b)


def dedup_pieces(
    pieces: Sequence[ChunkPiece], *, near_threshold: float = _NEAR_DUP_THRESHOLD
) -> DedupResult:
    """Drop exact and near-duplicate chunks, preserving reading order (first wins).

    Duplication is judged on a chunk's **contextualized** text — body *plus* section
    path — exactly like :meth:`ChunkPiece.content_id` and the ingestion idempotency
    ledger downstream. That agreement is the point: judging the bare body here while the
    ledger hashes body+section makes two sections that happen to share a sentence
    ("Contact support." under *Escalations* and under *Reassignments*) collide, and the second
    section is silently left with no indexed content while the run reports a benign
    duplicate. Near-duplicate detection is scoped to chunks under the same section path
    for the same reason: two sections repeating boilerplate are distinct answers to
    distinct questions, not one passage seen twice.

    Within a section, exact duplicates are removed by a normalised content hash and
    near-duplicates by word-shingle Jaccard overlap ``>= near_threshold`` against an
    already-kept chunk — guarding against paraphrased or boilerplate-heavy passages (a
    common synthetic-data artefact) reaching extraction twice.

    Args:
        pieces: Candidate chunks for one document (or batch), in order. A
            ``Sequence`` rather than a ``list`` so a list of the
            :class:`SectionChunk` subclass is accepted — under list invariance it
            would not be, and the parsed-document path produces exactly that.
        near_threshold: Jaccard cut-off for treating two chunks as near-duplicates.

    Returns:
        A :class:`DedupResult` with the kept chunks and honest drop counts.
    """
    result = DedupResult()
    seen_hashes: set[str] = set()
    kept_shingles: dict[str, list[frozenset[str]]] = {}
    for piece in pieces:
        if not _normalise(piece.text):
            continue
        content_hash = piece.content_id()
        if content_hash in seen_hashes:
            result.exact_duplicates += 1
            continue
        shingles = _shingles(piece.text)
        section_shingles = kept_shingles.setdefault(piece.section, [])
        if any(_jaccard(shingles, prior) >= near_threshold for prior in section_shingles):
            result.near_duplicates += 1
            continue
        seen_hashes.add(content_hash)
        section_shingles.append(shingles)
        result.kept.append(piece)
    return result
