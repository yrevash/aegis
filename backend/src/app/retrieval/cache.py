"""Backend shim: the two-tier semantic cache now lives in ``aegis.retrieval.cache``."""

from __future__ import annotations

from aegis.retrieval.cache import RedisLike, SemanticCache

__all__ = ["RedisLike", "SemanticCache"]
