"""Document chunking and deduplication for ingestion.

LightRAG does its own token chunking internally, but we chunk (and dedup) *before*
handing text to it so that (a) content validation and provenance run per-chunk, and
(b) exact/near-duplicate passages never reach extraction or the graph twice — cheaper
and a small poisoning-surface reduction.

Two chunkers live here:

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

Chunk size is measured in whitespace-delimited words as a portable, dependency-free
approximation of tokens (roughly ~0.75 tokens/word for English).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

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
    nesting (``"Guide > Refunds"``). Text before the first heading is emitted under an
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


def _pack_units(units: list[str], chunk_size: int, overlap: int) -> list[tuple[str, int]]:
    """Greedily pack text units into windows ≤ ``chunk_size`` words, seeding overlap.

    Whole units (paragraphs/sentences) are kept intact so a chunk never straddles a
    sentence boundary mid-word. When a window fills, the next one is seeded with the
    trailing ``overlap`` words of the previous window to preserve cross-chunk context.

    Returns:
        One ``(window, carried)`` pair per window, where ``carried`` is how many of the
        window's leading words were re-used from the previous window. Those words are
        *not* new text, so the caller must not advance a document word offset by them —
        counting them twice is what made the reported spans drift past the end of the
        document.
    """
    windows: list[tuple[str, int]] = []
    current: list[str] = []
    current_words = 0
    carried = 0
    for unit in units:
        unit_words = len(unit.split())
        if current and current_words + unit_words > chunk_size:
            window = " ".join(current)
            windows.append((window, carried))
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
    if current:
        windows.append((" ".join(current), carried))
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
        for window, carried in _pack_units(units, chunk_size, overlap):
            word_count = len(window.split())
            # ``carried`` leading words are a repeat of the previous window's tail, so
            # this window actually starts that many words BEFORE the previous one ended.
            # Advancing by the full word_count would count every overlap twice and push
            # the reported spans past the end of the document — offsets that cannot
            # locate the chunk they claim to cite.
            word_start = max(0, running_words - carried)
            pieces.append(
                ChunkPiece(
                    text=window,
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
    pieces: list[ChunkPiece], *, near_threshold: float = _NEAR_DUP_THRESHOLD
) -> DedupResult:
    """Drop exact and near-duplicate chunks, preserving reading order (first wins).

    Duplication is judged on a chunk's **contextualized** text — body *plus* section
    path — exactly like :meth:`ChunkPiece.content_id` and the ingestion idempotency
    ledger downstream. That agreement is the point: judging the bare body here while the
    ledger hashes body+section makes two sections that happen to share a sentence
    ("Contact support." under *Refunds* and under *Returns*) collide, and the second
    section is silently left with no indexed content while the run reports a benign
    duplicate. Near-duplicate detection is scoped to chunks under the same section path
    for the same reason: two sections repeating boilerplate are distinct answers to
    distinct questions, not one passage seen twice.

    Within a section, exact duplicates are removed by a normalised content hash and
    near-duplicates by word-shingle Jaccard overlap ``>= near_threshold`` against an
    already-kept chunk — guarding against paraphrased or boilerplate-heavy passages (a
    common synthetic-data artefact) reaching extraction twice.

    Args:
        pieces: Candidate chunks for one document (or batch), in order.
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
