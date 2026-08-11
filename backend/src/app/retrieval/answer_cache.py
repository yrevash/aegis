"""Backend shim: the answer-level semantic cache now lives in ``aegis.retrieval.answer_cache``."""

from __future__ import annotations

from aegis.retrieval.answer_cache import AnswerCache, AnswerCacheHit

__all__ = ["AnswerCache", "AnswerCacheHit"]
