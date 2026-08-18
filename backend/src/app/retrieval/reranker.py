"""Backend shim: reranking now lives in ``aegis.retrieval``.

Two rerankers, in this order: the **local ONNX cross-encoder**
(``aegis.retrieval.local_reranker``) is what the query path uses, and the
**LLM-as-reranker** (``aegis.retrieval.reranker``, re-exported here as ``rerank``) is the
loud fallback behind it. See ``NOTES.md`` for why the local one is allowed on this hardware
after all.
"""

from __future__ import annotations

from aegis.retrieval.local_reranker import (
    DEFAULT_LOCAL_RERANK_MODEL,
    LocalCrossEncoderReranker,
    rerank_scored_local_first,
)
from aegis.retrieval.reranker import rerank

__all__ = [
    "DEFAULT_LOCAL_RERANK_MODEL",
    "LocalCrossEncoderReranker",
    "rerank",
    "rerank_scored_local_first",
]
