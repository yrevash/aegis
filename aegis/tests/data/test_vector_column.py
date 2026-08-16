"""Data-layer contract after the pgvector -> embedded-vector-store migration.

Proves the embedding-of-record column is now a portable JSON ``list[float]`` (not a
pgvector ``vector`` type), that ``VectorType`` is gone, and that nothing under
``aegis/`` imports ``pgvector`` any more. Vector ANN search lives in the embedded
vector store; these SQL columns are only the durable mirror source the memory index
reads.
"""

from __future__ import annotations

import pathlib

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, insert, select
from sqlalchemy.dialects.mysql import dialect as non_pg_dialect
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import dialect as pg_dialect

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
    # Any other dialect → portable JSON, so the column is not welded to one vendor.
    other_impl = col.load_dialect_impl(non_pg_dialect())
    assert "JSON" in type(other_impl).__name__.upper()


async def test_vectorcolumn_roundtrips_list_of_float_on_postgres(pg_owner_engine) -> None:
    """The durable mirror really survives a PostgreSQL round-trip, floats and all.

    Round-tripped through the actual ``jsonb`` column the application will use rather
    than through the throwaway SQLite file this test used to build. Those are not the
    same claim: ``jsonb`` normalises numbers on the way in, so "the list comes back
    equal, and still as floats" is a fact about PostgreSQL that a SQLite round-trip
    cannot establish. Run over the owning role because it issues DDL.
    """
    md = MetaData()
    probe = Table(
        "emb_probe",
        md,
        Column("id", Integer, primary_key=True),
        Column("embedding", VectorColumn(EMBED_DIM)),
    )
    vec = [0.1, 0.2, 0.3, -0.4]
    async with pg_owner_engine.begin() as conn:
        await conn.run_sync(md.create_all)
        await conn.execute(insert(probe).values(id=1, embedding=vec))
        stored = (await conn.execute(select(probe.c.embedding))).scalar_one()
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
