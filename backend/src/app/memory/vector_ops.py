"""Backend shim: portable top-k cosine now lives in :mod:`aegis.memory.vector_ops`."""

from __future__ import annotations

from aegis.memory.vector_ops import topk_by_cosine

__all__ = ["topk_by_cosine"]
