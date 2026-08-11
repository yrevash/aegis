"""Backend shim: context-aware query rewriting now lives in ``aegis.retrieval.query_rewrite``."""

from __future__ import annotations

from aegis.retrieval.query_rewrite import CallUsage, RewriteResult, rewrite_query, usage_of

__all__ = ["CallUsage", "RewriteResult", "rewrite_query", "usage_of"]
