"""Aegis shared data-layer foundation — the portable ORM base for durable modules.

Re-exports the declarative base (:class:`AegisBase`), the cross-dialect column types
(:class:`VectorType`, :data:`JsonB`) and the fixed embedding dimensionality
(:data:`EMBED_DIM`). Available under the ``aegis[data]`` extra
(``sqlalchemy[asyncio]`` + ``pgvector``); imports nothing from any host application.
"""

from __future__ import annotations

from aegis.data.base import EMBED_DIM, AegisBase, JsonB, VectorType

__all__ = ["EMBED_DIM", "AegisBase", "JsonB", "VectorType"]
