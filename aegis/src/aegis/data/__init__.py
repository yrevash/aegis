"""Aegis shared data-layer foundation — the portable ORM base for durable modules.

Re-exports the declarative base (:class:`AegisBase`), the cross-dialect column types
(:class:`VectorColumn`, :data:`JsonB`) and the fixed embedding dimensionality
(:data:`EMBED_DIM`). Available under the ``aegis[data]`` extra (``sqlalchemy[asyncio]``);
imports nothing from any host application. Embeddings persist as JSON of record — vector
search lives in Qdrant, so pgvector is no longer a dependency.
"""

from __future__ import annotations

from aegis.data.base import EMBED_DIM, AegisBase, JsonB, VectorColumn

__all__ = ["EMBED_DIM", "AegisBase", "JsonB", "VectorColumn"]
