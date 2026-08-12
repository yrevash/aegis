"""Portable ORM foundation shared by Aegis data-layer modules (memory, governance).

A minimal, dependency-injected SQLAlchemy 2.0 base that any Aegis module needing durable
relational + vector storage can register its tables on, without coupling to a host
application's engine, config or session lifecycle. The host owns the engine/sessionmaker
and drives ``AegisBase.metadata.create_all`` (plus any RLS bootstrap); this module owns
only the *shape* of the store.

Portability note: the vector and JSON columns are declared with cross-dialect type
decorators so the schema materialises on SQLite (used by unit tests, which must run with
no Postgres) as well as on PostgreSQL. The embedding-of-record column is a portable
``list[float]`` stored as JSON — ``jsonb`` on PostgreSQL, ``JSON`` elsewhere — **not** a
pgvector ``vector`` type: ANN search runs on Qdrant
(:class:`aegis.retrieval.vector_store.QdrantVectorStore`), so the SQL column is only the
durable source-of-record that the memory mirror reads, never a search index.

This module imports nothing from any host application — it is self-contained under the
``aegis[data]`` extra (just ``sqlalchemy[asyncio]``; pgvector was removed once vector
search moved to Qdrant).
"""

from __future__ import annotations

from typing import Any

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


class VectorColumn(TypeDecorator[list[float]]):
    """A portable embedding-of-record column: a ``list[float]`` stored as JSON.

    The vector is persisted as a JSON array — native ``jsonb`` on PostgreSQL, portable
    ``JSON`` on every other dialect (e.g. the SQLite unit-test database). It is the
    durable *source-of-record* embedding that the memory index lazily mirrors into
    Qdrant; it is **not** a search index and carries no pgvector distance operators.
    ANN search runs on :class:`aegis.retrieval.vector_store.QdrantVectorStore`.

    ``dim`` is retained for documentation/parity with the embedding dimensionality;
    JSON storage does not enforce it (the mirror skips off-dim rows at query time).
    """

    impl = JSON
    cache_ok = True

    def __init__(self, dim: int | None = None) -> None:
        """Record the (documentary) embedding dimensionality and init the JSON impl."""
        self.dim = dim
        super().__init__()

    def load_dialect_impl(self, dialect: Any) -> Any:  # noqa: ANN401 - SQLAlchemy hook
        """Return native ``jsonb`` on PostgreSQL, portable ``JSON`` elsewhere."""
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class AegisBase(DeclarativeBase):
    """Declarative base for every table an Aegis data-layer module contributes.

    Modules (``aegis.memory``, later ``aegis.governance``) register their mapped classes
    on this shared metadata; the host application creates them with
    ``AegisBase.metadata.create_all`` alongside its own tables.
    """


__all__ = ["EMBED_DIM", "AegisBase", "JsonB", "VectorColumn"]
