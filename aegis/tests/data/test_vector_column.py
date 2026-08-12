"""Data-layer contract after the pgvector → Qdrant migration.

Proves the embedding-of-record column is now a portable JSON ``list[float]`` (not a
pgvector ``vector`` type), that ``VectorType`` is gone, and that nothing under
``aegis/`` imports ``pgvector`` any more. Vector ANN search lives in Qdrant; these SQL
columns are only the durable mirror source the memory index reads.
"""

from __future__ import annotations

import pathlib

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, insert, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import dialect as pg_dialect
from sqlalchemy.dialects.sqlite import dialect as sqlite_dialect

import aegis.data
from aegis.data import EMBED_DIM, VectorColumn


def test_vectortype_is_gone_and_vectorcolumn_exported() -> None:
    assert not hasattr(aegis.data, "VectorType"), "VectorType must be deleted"
    assert "VectorColumn" in aegis.data.__all__
    assert EMBED_DIM == 3072


def test_vectorcolumn_compiles_to_json_not_pgvector() -> None:
    col = VectorColumn(EMBED_DIM)
    # PostgreSQL → native jsonb (NOT a pgvector ``vector(dim)`` type).
    pg_impl = col.load_dialect_impl(pg_dialect())
    assert isinstance(pg_impl, JSONB)
    # SQLite (tests) → portable JSON.
    sqlite_impl = col.load_dialect_impl(sqlite_dialect())
    assert "JSON" in type(sqlite_impl).__name__.upper()


def test_vectorcolumn_roundtrips_list_of_float_on_sqlite() -> None:
    md = MetaData()
    t = Table(
        "emb_probe",
        md,
        Column("id", Integer, primary_key=True),
        Column("embedding", VectorColumn(EMBED_DIM)),
    )
    engine = create_engine("sqlite://")
    md.create_all(engine)
    vec = [0.1, 0.2, 0.3, -0.4]
    with engine.begin() as conn:
        conn.execute(insert(t).values(id=1, embedding=vec))
        stored = conn.execute(select(t.c.embedding)).scalar_one()
    assert stored == vec
    assert all(isinstance(x, float) for x in stored)


def test_no_pgvector_import_anywhere_under_aegis() -> None:
    """A grep-style guard: no source file under ``aegis/src/aegis`` imports pgvector."""
    src = pathlib.Path(aegis.data.__file__).resolve().parents[1]  # .../src/aegis
    offenders: list[str] = []
    for path in src.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue  # comments explaining the removal are allowed
            if "import pgvector" in line or "from pgvector" in line:
                offenders.append(f"{path}: {line.strip()}")
    assert not offenders, offenders


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
