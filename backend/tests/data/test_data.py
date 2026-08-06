"""Unit tests for the data layer (async SQLAlchemy models + audit + bootstrap).

No Postgres required: an aiosqlite database (temp file) is bound via
``configure_engine``; the pgvector and JSONB columns degrade to JSON so the schema
materialises and rows round-trip.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from app.api.schemas import Role
from app.data import (
    AuditLog,
    Chunk,
    EvalResult,
    User,
    bootstrap,
    configure_engine,
    get_sessionmaker,
    record_audit,
    to_asyncpg_dsn,
)


@pytest_asyncio.fixture
async def sqlite_db(tmp_path):
    """Bind an aiosqlite engine, create all tables, and yield the sessionmaker."""
    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    configure_engine(engine)
    await bootstrap(engine)
    yield get_sessionmaker()
    await engine.dispose()


def test_to_asyncpg_dsn_rewrites_driver():
    assert (
        to_asyncpg_dsn("postgresql://u:p@localhost/db")
        == "postgresql+asyncpg://u:p@localhost/db"
    )
    assert (
        to_asyncpg_dsn("postgres://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    )
    # Already-async and non-postgres URLs are left untouched.
    assert to_asyncpg_dsn("postgresql+asyncpg://x/db") == "postgresql+asyncpg://x/db"
    assert to_asyncpg_dsn("sqlite+aiosqlite:///x.db") == "sqlite+aiosqlite:///x.db"


async def test_user_role_enum_roundtrip(sqlite_db):
    async with sqlite_db() as session:
        session.add(User(username="alice", role=Role.ADMIN))
        await session.commit()

    async with sqlite_db() as session:
        user = (await session.execute(select(User))).scalar_one()
        assert user.username == "alice"
        assert user.role is Role.ADMIN


async def test_record_audit_writes_row(sqlite_db):
    await record_audit(
        action="tool:create_ticket",
        actor="alice",
        model="genailab-maas-gpt-4o",
        trace_id="a" * 32,
        payload={"title": "bug"},
        approved_by="bob",
    )

    async with sqlite_db() as session:
        row = (await session.execute(select(AuditLog))).scalar_one()
        assert row.action == "tool:create_ticket"
        assert row.actor == "alice"
        assert row.approved_by == "bob"
        assert row.payload == {"title": "bug"}
        assert row.ts is not None  # server_default populated


async def test_chunk_embedding_roundtrip(sqlite_db):
    async with sqlite_db() as session:
        session.add(
            Chunk(
                doc_id="doc-1",
                persona="analyst",
                content="hello",
                embedding=[0.1, 0.2, 0.3],
                meta={"page": 1},
            )
        )
        await session.commit()

    async with sqlite_db() as session:
        chunk = (await session.execute(select(Chunk))).scalar_one()
        assert chunk.doc_id == "doc-1"
        assert chunk.persona == "analyst"
        assert list(chunk.embedding) == [0.1, 0.2, 0.3]
        assert chunk.meta == {"page": 1}


async def test_eval_result_roundtrip(sqlite_db):
    async with sqlite_db() as session:
        session.add(
            EvalResult(
                run_id="run-9",
                metric="faithfulness",
                score=0.87,
                passed=True,
                detail={"judge": "phi-4"},
            )
        )
        await session.commit()

    async with sqlite_db() as session:
        row = (await session.execute(select(EvalResult))).scalar_one()
        assert row.metric == "faithfulness"
        assert row.score == pytest.approx(0.87)
        assert row.passed is True
        assert row.detail == {"judge": "phi-4"}
