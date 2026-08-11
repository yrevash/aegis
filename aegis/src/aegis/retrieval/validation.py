"""Content validation before writing to the knowledge graph (poisoning defense).

`docs/security.md` calls for validating content *before* it enters the store so that
adversarial "documents" cannot plant instructions or junk that later gets retrieved
(RAG/vector poisoning, memory poisoning). This is a cheap, deterministic, pure-code
gate — no model call — that runs on every chunk during ingestion. Spotlighting
(`spotlight.py`) is the second line of defence at *retrieval* time; this is the first
line at *write* time.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

#: Phrases characteristic of prompt-injection payloads embedded in documents.
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions", re.IGNORECASE),
    re.compile(r"disregard\s+(the\s+)?(system|previous|above)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+", re.IGNORECASE),
    re.compile(r"</?(system|assistant|user)>", re.IGNORECASE),
    re.compile(r"\bnew\s+instructions?\s*:", re.IGNORECASE),
    re.compile(r"\bexfiltrat", re.IGNORECASE),
    re.compile(r"reveal\s+(your\s+)?(system\s+prompt|api\s+key|secret)", re.IGNORECASE),
)

#: Minimum useful chunk length (characters) after stripping.
_MIN_CHARS = 8

#: Maximum chunk length (characters) — guards against oversized poisoning blobs.
_MAX_CHARS = 20_000

#: Reject chunks whose share of non-printable characters exceeds this fraction.
_MAX_NONPRINTABLE_RATIO = 0.15


class ValidationResult(BaseModel):
    """Verdict for a single candidate chunk."""

    ok: bool = Field(description="Whether the chunk is safe to write to the store.")
    reason: str = Field(default="", description="Why it was rejected (empty when ok).")


def _nonprintable_ratio(text: str) -> float:
    """Return the fraction of characters that are neither printable nor common whitespace."""
    if not text:
        return 0.0
    bad = sum(1 for ch in text if not (ch.isprintable() or ch in "\n\r\t"))
    return bad / len(text)


def validate_content(text: str) -> ValidationResult:
    """Validate one chunk of candidate content before it enters the graph/vector store.

    Args:
        text: The chunk text to validate.

    Returns:
        A `ValidationResult`; `ok=False` carries a human-readable `reason` that the
        ingest report surfaces.
    """
    stripped = text.strip()
    if len(stripped) < _MIN_CHARS:
        return ValidationResult(ok=False, reason="content too short to be meaningful")
    if len(stripped) > _MAX_CHARS:
        return ValidationResult(ok=False, reason="content exceeds maximum chunk size")
    if _nonprintable_ratio(text) > _MAX_NONPRINTABLE_RATIO:
        return ValidationResult(ok=False, reason="excessive non-printable characters")
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            return ValidationResult(
                ok=False, reason=f"suspected embedded injection ({pattern.pattern!r})"
            )
    return ValidationResult(ok=True)
