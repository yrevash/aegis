"""Portable ORM foundation shared by Aegis data-layer modules (memory, governance).

A minimal, dependency-injected SQLAlchemy 2.0 base that any Aegis module needing durable
relational + vector storage can register its tables on, without coupling to a host
application's engine, config or session lifecycle. The host owns the engine/sessionmaker
and drives ``AegisBase.metadata.create_all`` (plus any RLS bootstrap); this module owns
only the *shape* of the store.

Portability note: the vector and JSON columns are declared with cross-dialect type
decorators so the schema materialises on SQLite (used by unit tests, which must run with
no Postgres) as well as on PostgreSQL. On PostgreSQL the columns compile to the native
``vector`` and ``jsonb`` types; on other dialects they fall back to ``JSON`` so
``metadata.create_all`` still succeeds.

This module imports nothing from any host application — it is self-contained under the
``aegis[data]`` extra (``sqlalchemy[asyncio]`` + ``pgvector``).
"""

from __future__ import annotations

from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

# Embedding dimensionality of the fixed embedding model
# (``genailab-maas-text-embedding-3-large`` → 3072 dims).
EMBED_DIM = 3072

# A JSON column that uses native ``jsonb`` on PostgreSQL and portable ``JSON``
# elsewhere (keeps ``create_all`` working on the SQLite test database).
JsonB = JSON().with_variant(JSONB, "postgresql")


class VectorType(TypeDecorator[list[float]]):
    """A pgvector column that degrades to ``JSON`` on non-Postgres dialects.

    On PostgreSQL this compiles to ``vector(dim)`` and supports the pgvector
    distance operators; on SQLite (unit tests) it stores the vector as a JSON
    array so the table can still be created and rows round-tripped.
    """

    impl = Vector
    cache_ok = True

    def __init__(self, dim: int) -> None:
        """Store the vector dimensionality and initialise the underlying type."""
        self.dim = dim
        super().__init__(dim)

    def load_dialect_impl(self, dialect: Any) -> Any:  # noqa: ANN401 - SQLAlchemy hook
        """Return ``vector`` on PostgreSQL, ``JSON`` on every other dialect."""
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Vector(self.dim))
        return dialect.type_descriptor(JSON())


class AegisBase(DeclarativeBase):
    """Declarative base for every table an Aegis data-layer module contributes.

    Modules (``aegis.memory``, later ``aegis.governance``) register their mapped classes
    on this shared metadata; the host application creates them with
    ``AegisBase.metadata.create_all`` alongside its own tables.
    """


__all__ = ["EMBED_DIM", "AegisBase", "JsonB", "VectorType"]
