"""Backend shim: ingestion chunking/dedup now lives in ``aegis.retrieval.chunker``."""

from __future__ import annotations

from aegis.retrieval.chunker import (
    ChunkPiece,
    DedupResult,
    chunk_structured,
    chunk_text,
    dedup_pieces,
)

__all__ = [
    "ChunkPiece",
    "DedupResult",
    "chunk_structured",
    "chunk_text",
    "dedup_pieces",
]
