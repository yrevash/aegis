"""Tests for the ingestion chunker + deduplication."""

from __future__ import annotations

import pytest

from aegis.retrieval.chunker import (
    ChunkPiece,
    chunk_structured,
    chunk_text,
    dedup_pieces,
)


def test_chunk_empty_text_returns_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_chunk_short_text_single_chunk():
    assert chunk_text("one two three", chunk_size=10, overlap=2) == ["one two three"]


def test_chunk_windows_with_overlap():
    words = " ".join(str(i) for i in range(10))
    chunks = chunk_text(words, chunk_size=4, overlap=1)
    # step = 3 → windows start at 0,3,6,9
    assert chunks[0] == "0 1 2 3"
    assert chunks[1] == "3 4 5 6"  # overlap of one word (the '3')
    assert chunks[-1].split()[-1] == "9"


def test_chunk_rejects_bad_params():
    with pytest.raises(ValueError):
        chunk_text("a b c", chunk_size=0)
    with pytest.raises(ValueError):
        chunk_text("a b c", chunk_size=3, overlap=3)


# ── structure-aware recursive chunking ──────────────────────────────────────

_DOC = """---
title: Closure guide
---

# Closures

Closures go back to the original approver for confirmation within a week. Verify identity first.

## Duplicate requests

Close only the duplicate request. Keep the original request open for the record.

# Escalation

Escalate to Tier-2 when the SLA is at risk of breaching.
"""


def test_chunk_structured_tracks_section_paths():
    pieces = chunk_structured(_DOC, chunk_size=50, overlap=5)
    sections = {p.section for p in pieces}
    # Heading nesting is captured as a path; frontmatter is stripped.
    assert "Closures" in sections
    assert "Closures > Duplicate requests" in sections
    assert "Escalation" in sections
    assert all(isinstance(p, ChunkPiece) for p in pieces)
    # Ordinals are contiguous and in reading order.
    assert [p.ordinal for p in pieces] == list(range(len(pieces)))


def test_chunk_structured_contextualizes_with_heading():
    pieces = chunk_structured(_DOC, chunk_size=50, overlap=5)
    dup = next(p for p in pieces if p.section == "Closures > Duplicate requests")
    # Contextual retrieval: the section path is prepended to the embedded text.
    assert dup.contextualized().startswith("[Closures > Duplicate requests]")
    assert "duplicate request" in dup.contextualized().lower()


def test_chunk_structured_respects_size_with_overlap():
    body = "# H\n\n" + " ".join(f"w{i}" for i in range(120))
    pieces = chunk_structured(body, chunk_size=40, overlap=10)
    assert len(pieces) > 1
    # Each window holds up to chunk_size words of new content plus up to `overlap`
    # words carried over from the previous window (the overlap bound).
    for p in pieces:
        assert p.word_count <= 40 + 10
    # Consecutive windows overlap (share the carry-over tail/head words).
    first_words = pieces[0].text.split()
    second_words = pieces[1].text.split()
    assert set(first_words[-10:]) & set(second_words[:10])


def test_chunk_structured_plain_text_has_no_prefix():
    # No headings → empty section → contextualized text is byte-identical to raw.
    pieces = chunk_structured("Just a plain sentence with no markdown headings at all.")
    assert len(pieces) == 1
    assert pieces[0].section == ""
    assert pieces[0].contextualized() == pieces[0].text


def test_chunk_structured_rejects_bad_params():
    with pytest.raises(ValueError):
        chunk_structured("a b c", chunk_size=0)
    with pytest.raises(ValueError):
        chunk_structured("a b c", chunk_size=3, overlap=3)


# ── robust deduplication (exact + near-duplicate) ───────────────────────────


def _piece(text: str, ordinal: int = 0) -> ChunkPiece:
    return ChunkPiece(text=text, ordinal=ordinal, word_count=len(text.split()))


def test_dedup_pieces_drops_exact_duplicates():
    pieces = [_piece("the quick brown fox", 0), _piece("the  quick brown fox", 1)]
    result = dedup_pieces(pieces)
    assert len(result.kept) == 1
    assert result.exact_duplicates == 1
    assert result.near_duplicates == 0


def test_dedup_pieces_drops_near_duplicates():
    a = "the quick brown fox jumps over the lazy dog every single morning"
    b = "the quick brown fox jumps over the lazy dog every single evening"
    result = dedup_pieces([_piece(a, 0), _piece(b, 1)], near_threshold=0.8)
    assert len(result.kept) == 1
    assert result.near_duplicates == 1


def test_dedup_pieces_keeps_distinct_content():
    a = _piece("billing closures go to the original approver queue", 0)
    b = _piece("login failures returning http 500 need the status page", 1)
    result = dedup_pieces([a, b])
    assert len(result.kept) == 2
    assert result.exact_duplicates == 0
    assert result.near_duplicates == 0


def test_word_start_locates_the_chunk_inside_the_document():
    # REGRESSION: the running offset advanced by the FULL window size, counting every
    # overlap twice — a 1000-word doc reported spans 0, 400, 860, … which run past the
    # end of the document and cannot locate the text they are shipped as lineage for.
    words = [f"w{i}" for i in range(1000)]
    body = " ".join(words)

    pieces = chunk_structured(body, chunk_size=400, overlap=60)

    assert len(pieces) > 1  # the document really was split
    for piece in pieces:
        span = words[piece.word_start : piece.word_start + piece.word_count]
        # The reported span is inside the document AND is exactly this chunk's words.
        assert piece.word_start + piece.word_count <= len(words)
        assert span == piece.text.split()
    assert [p.word_start for p in pieces] == sorted(p.word_start for p in pieces)


def test_word_start_stays_within_the_document_across_sections():
    body = "\n".join(
        f"# Section {s}\n\n" + " ".join(f"s{s}w{i}" for i in range(300)) for s in range(3)
    )
    total_words = sum(
        len(line.split()) for line in body.splitlines() if not line.startswith("#")
    )

    pieces = chunk_structured(body, chunk_size=120, overlap=20)

    for piece in pieces:
        assert piece.word_start >= 0
        assert piece.word_start + piece.word_count <= total_words


def test_dedup_pieces_keeps_identical_bodies_under_different_sections():
    # REGRESSION: in-batch dedup hashed the bare body while the ingestion ledger hashes
    # body+section, so "Contact support." under two different headings collided and the
    # second section was silently left with no indexed content.
    closures = ChunkPiece(text="Contact support.", ordinal=0, section="Closures", word_count=2)
    returns = ChunkPiece(text="Contact support.", ordinal=1, section="Returns", word_count=2)

    result = dedup_pieces([closures, returns])

    assert [p.section for p in result.kept] == ["Closures", "Returns"]
    assert result.exact_duplicates == 0
    assert result.near_duplicates == 0


def test_dedup_pieces_keeps_long_shared_boilerplate_under_different_sections():
    # The same trap via the near-duplicate arm: two sections repeating a long passage
    # are distinct answers to distinct questions, not one passage seen twice.
    body = "escalate the request to a senior agent before the stated deadline lapses"
    a = ChunkPiece(text=body, ordinal=0, section="Closures", word_count=len(body.split()))
    b = ChunkPiece(text=body, ordinal=1, section="Returns", word_count=len(body.split()))

    result = dedup_pieces([a, b])

    assert len(result.kept) == 2
    # …while a genuine repeat WITHIN one section is still dropped.
    same_section = dedup_pieces([a, a.__class__(**{**a.__dict__, "ordinal": 2})])
    assert len(same_section.kept) == 1
    assert same_section.exact_duplicates == 1


def test_dedup_pieces_content_id_is_stable_and_section_aware():
    same = _piece("identical body text here", 0)
    other = ChunkPiece(text="identical body text here", ordinal=1, section="A")
    # Same body, different section → different content id (context matters).
    assert same.content_id() != other.content_id()
    # Deterministic across calls.
    assert same.content_id() == _piece("identical body text here", 9).content_id()


def test_indexed_id_separates_two_chunks_whose_text_and_section_are_identical():
    """The store key carries per-chunk identity; the dedup key deliberately does not.

    ``content_id`` is a content *address*: two pieces with the same section and the same
    body are one passage, which is what :func:`dedup_pieces` and the ingestion ledger need
    and why the ordinal must stay out of it. But the ``index`` stage keys a global store by
    that id, and the ingestion ``chunk`` stage does not de-duplicate — so byte-identical
    repeated boilerplate under one heading path (a continued table's "Footnotes available
    at end of table.") writes two rows that claim one key, and the second row's ordinal,
    page and boxes — everything a citation resolves through — are overwritten by the first
    row's. Measured on ``census-income-tables.pdf``: 182 chunks, 162 distinct
    ``content_id``, 20 rows lost (see ``test_chunk_sections_fixture``).
    """
    first = _piece("footnotes available at end of table.", 77)
    second = _piece("footnotes available at end of table.", 89)

    # The dedup key is unchanged, and must stay unchanged: it is what makes the two one
    # passage for de-duplication purposes.
    assert first.content_id() == second.content_id()
    # The store key is not.
    assert first.indexed_id() != second.indexed_id()
    # Still a pure function of (ordinal, section, body) — a re-chunk of the same document
    # is deterministic, so the same text at the same ordinal re-publishes over itself
    # rather than duplicating, which is what the ``index`` stage relies on.
    assert first.indexed_id() == _piece("footnotes available at end of table.", 77).indexed_id()
    # Section still matters, exactly as it does for the content address.
    assert (
        ChunkPiece(text="identical body", ordinal=3, section="A").indexed_id()
        != ChunkPiece(text="identical body", ordinal=3, section="B").indexed_id()
    )
