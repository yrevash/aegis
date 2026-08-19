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
pgvector ``vector`` type: ANN search runs on the embedded vector store
(:class:`aegis.retrieval.vector_store.QdrantVectorStore`), so the SQL column is only the
durable source-of-record that the memory mirror reads, never a search index.

This module imports nothing from any host application — it is self-contained under the
``aegis[data]`` extra (just ``sqlalchemy[asyncio]``; pgvector was removed once vector
search moved to the embedded vector store).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
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
    the vector store; it is **not** a search index and carries no pgvector distance operators.
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


class UtcDateTime(TypeDecorator[datetime]):
    """A timestamp column that is *always* an instant in UTC, on every dialect.

    The application layer is uniformly timezone-aware (``datetime.now(UTC)``), so the
    storage layer must be too. A plain ``Mapped[datetime]`` maps to ``TIMESTAMP WITHOUT
    TIME ZONE``, which is a trap on PostgreSQL:

    - asyncpg refuses to encode an *aware* datetime for a naive column and raises
      ``DataError: can't subtract offset-naive and offset-aware datetimes`` — so every
      write **and** every ``WHERE ts < :now`` comparison from aware application code
      blows up at runtime (this is what killed the SLA sweeper).
    - ``server_default=func.now()`` on a naive column stores the *server's local wall
      clock*, not UTC. On a box with ``TimeZone = Asia/Kolkata`` every ``created_at``
      is silently +05:30 off, and the API then re-labels it ``+00:00``.

    This decorator closes both holes at the one place they can be enforced:

    - PostgreSQL → ``TIMESTAMP WITH TIME ZONE``. Real instants; ``now()`` is correct
      regardless of the server's ``TimeZone``; aware binds are natively supported.
    - Every other dialect (the SQLite unit-test database) → plain ``DATETIME`` holding
      naive **UTC**, because SQLite has no timezone-aware type.
    - Binding normalises either input form (naive is read as UTC) so callers may pass
      aware *or* naive-UTC datetimes and get identical, correct storage.
    - Loading always returns an **aware UTC** datetime, so downstream arithmetic like
      ``datetime.now(UTC) - row.created_at`` can never raise the naive/aware TypeError.
    """

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:  # noqa: ANN401 - SQLAlchemy hook
        """Return ``timestamptz`` on PostgreSQL, portable naive-UTC ``DATETIME`` else."""
        if dialect.name == "postgresql":
            return dialect.type_descriptor(TIMESTAMP(timezone=True))
        return dialect.type_descriptor(DateTime())

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:  # noqa: ANN401
        """Normalise a bound value to UTC (naive input is interpreted as UTC)."""
        if value is None:
            return None
        value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        if dialect.name == "postgresql":
            return value
        # SQLite (and friends) store the naive UTC wall clock; the offset would
        # otherwise be dropped silently and corrupt lexical ordering.
        return value.replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:  # noqa: ANN401, ARG002
        """Return the stored instant as an aware UTC datetime."""
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class AegisBase(DeclarativeBase):
    """Declarative base for every table an Aegis data-layer module contributes.

    Modules (``aegis.memory``, later ``aegis.governance``) register their mapped classes
    on this shared metadata; the host application creates them with
    ``AegisBase.metadata.create_all`` alongside its own tables.

    Every ``Mapped[datetime]`` on this base materialises as :class:`UtcDateTime` (see
    ``type_annotation_map``) — the single enforcement point that keeps timestamps true
    UTC instants on PostgreSQL and stops the naive/aware mismatch class of bug from
    being reintroduced by a future column.
    """

    type_annotation_map = {datetime: UtcDateTime}


__all__ = ["EMBED_DIM", "AegisBase", "JsonB", "UtcDateTime", "VectorColumn"]
