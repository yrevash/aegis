"""Token counting for the working-memory budget — offline-safe.

Uses ``tiktoken`` when it is importable (accurate), else a ``len // 4`` heuristic so the
budget math still works with zero dependencies offline. The budgeter only needs a
consistent, monotone estimate — exactness is not required for eviction correctness.
"""

from __future__ import annotations

from functools import lru_cache

_CHARS_PER_TOKEN = 4


@lru_cache(maxsize=4)
def _encoder(model: str | None):  # noqa: ANN202 - tiktoken.Encoding | None
    """Return a cached tiktoken encoder, or ``None`` if tiktoken is unavailable."""
    try:
        import tiktoken
    except ImportError:
        return None
    try:
        return tiktoken.encoding_for_model(model) if model else tiktoken.get_encoding("cl100k_base")
    except (KeyError, ValueError):
        return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, model: str | None = None) -> int:
    """Estimate the token count of ``text`` (tiktoken if present, else ``len // 4``).

    Args:
        text: The text to measure.
        model: Optional model id to pick the tiktoken encoding.

    Returns:
        A non-negative token estimate.
    """
    if not text:
        return 0
    enc = _encoder(model)
    if enc is not None:
        return len(enc.encode(text))
    return max(1, len(text) // _CHARS_PER_TOKEN)


__all__ = ["count_tokens"]
