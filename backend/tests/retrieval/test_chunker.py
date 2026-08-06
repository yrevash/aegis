"""Tests for the ingestion chunker + deduplication."""

from __future__ import annotations

import pytest

from app.retrieval.chunker import (
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
title: Refund guide
---

# Refunds

Refunds go back to the original payment method within a week. Verify identity first.

## Duplicate charges

Refund only the duplicate charge. Keep the original invoice open for the record.

# Escalation

Escalate to Tier-2 when the SLA is at risk of breaching.
"""


def test_chunk_structured_tracks_section_paths():
    pieces = chunk_structured(_DOC, chunk_size=50, overlap=5)
    sections = {p.section for p in pieces}
    # Heading nesting is captured as a path; frontmatter is stripped.
    assert "Refunds" in sections
    assert "Refunds > Duplicate charges" in sections
    assert "Escalation" in sections
    assert all(isinstance(p, ChunkPiece) for p in pieces)
    # Ordinals are contiguous and in reading order.
    assert [p.ordinal for p in pieces] == list(range(len(pieces)))


def test_chunk_structured_contextualizes_with_heading():
    pieces = chunk_structured(_DOC, chunk_size=50, overlap=5)
    dup = next(p for p in pieces if p.section == "Refunds > Duplicate charges")
    # Contextual retrieval: the section path is prepended to the embedded text.
    assert dup.contextualized().startswith("[Refunds > Duplicate charges]")
    assert "duplicate charge" in dup.contextualized().lower()


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
    a = _piece("billing refunds go to the original payment method", 0)
    b = _piece("login failures returning http 500 need the status page", 1)
    result = dedup_pieces([a, b])
    assert len(result.kept) == 2
    assert result.exact_duplicates == 0
    assert result.near_duplicates == 0


def test_dedup_pieces_content_id_is_stable_and_section_aware():
    same = _piece("identical body text here", 0)
    other = ChunkPiece(text="identical body text here", ordinal=1, section="A")
    # Same body, different section → different content id (context matters).
    assert same.content_id() != other.content_id()
    # Deterministic across calls.
    assert same.content_id() == _piece("identical body text here", 9).content_id()
