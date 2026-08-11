"""Backend shim: LLM-as-reranker now lives in ``aegis.retrieval.reranker``."""

from __future__ import annotations

from aegis.retrieval.reranker import rerank

__all__ = ["rerank"]
