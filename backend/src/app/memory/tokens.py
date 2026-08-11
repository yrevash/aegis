"""Backend shim: token counting now lives in :mod:`aegis.memory.tokens`."""

from __future__ import annotations

from aegis.memory.tokens import count_tokens

__all__ = ["count_tokens"]
