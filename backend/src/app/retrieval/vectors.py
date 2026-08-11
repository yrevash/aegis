"""Backend shim: vector helpers now live in ``aegis.retrieval.vectors``."""

from __future__ import annotations

from aegis.retrieval.vectors import cosine_similarity

__all__ = ["cosine_similarity"]
