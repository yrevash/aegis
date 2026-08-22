"""The chunk KV must be the one LightRAG reads, or the graph arm quotes nothing.

One claim, one failure mode.

The claim: an entity the ``local`` arm matches can be turned back into the **text** it was
extracted from. That lookup is ``entity["source_id"]`` → chunk ids →
``text_chunks.get_by_ids`` → ``LIGHTRAG_DOC_CHUNKS``, and on the live deployment that
table held **0 rows for every workspace**, so
``lightrag.operate._find_related_text_unit_from_entities`` logged ``No entities with text
chunks found`` and returned ``[]``. The arm reported ``Local query: 5 entites, 9
relations`` in the same breath and contributed **0 candidates** to the merged ranking.

The failure mode is not "nothing was written" — that one is visible. It is a row written
under the wrong **workspace** or the wrong **id**, which inserts cleanly, raises the table's
row count, and is never returned to anybody: ``(workspace, id)`` is the primary key and the
read is ``WHERE workspace=$1 AND id = ANY($2)``, so a row in the wrong partition is
indistinguishable from a row that does not exist. The workspace rule is the easy one to get
wrong, because LightRAG's Postgres default (``"default"``) is *not* its Qdrant default
(``"_"``), and this platform writes both.

So the tests here are the contract, the round trip, and the one degradation:

* :func:`test_the_table_and_the_row_are_lightrags_own` compares the table name, the column
  set and the workspace resolution against the **installed** ``lightrag`` rather than
  against this module's own comments. Both halves of a round-trip test would drift
  together; only a comparison with the real library catches a contract that moved.
* :func:`test_a_written_chunk_is_found_the_way_lightrag_looks_for_it` writes into a real
  PostgreSQL table created from LightRAG's own DDL string and reads it back with
  LightRAG's own ``get_by_ids`` SQL — because "the row is in the table" was already true of
  a table nothing could find rows in.
* :func:`test_a_missing_kv_table_is_reported_and_never_reported_as_zero` pins the
  degradation this write is allowed: it may not fail an otherwise-correct ingest, it may
  not poison the transaction it shares, and it may not answer ``0`` when the truth is
  "could not be asked".
"""

from __future__ import annotations

import pytest
from lightrag.kg.postgres_impl import SQL_TEMPLATES, TABLES
from sqlalchemy import text

from app.data import get_sessionmaker
from app.data.session import get_admin_engine
from app.ingestion.chunk_kv import (
    DEFAULT_KV_WORKSPACE,
    LIGHTRAG_CHUNK_TABLE,
    ChunkKVRow,
    kv_workspace,
    publish_chunk_kv,
)

pytestmark = pytest.mark.asyncio

_WORKSPACE = "run-under-test"

#: LightRAG's own DDL for the table, taken from the installed package. Using the library's
#: string rather than a transcription of it is the point: a column this writer names that
#: LightRAG does not declare fails here instead of in production.
_DDL = TABLES["LIGHTRAG_DOC_CHUNKS"]["ddl"]


async def _create_table() -> None:
    """Create LightRAG's chunk table in the scratch database, readable by everyone.

    The grant is deliberately wide because this is a scratch database with one role that
    matters: what is under test is the write and the read-back, not the privilege model.
    """
    async with get_admin_engine().begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {LIGHTRAG_CHUNK_TABLE}"))
        await conn.execute(text(_DDL))
        await conn.execute(text(f"GRANT ALL ON {LIGHTRAG_CHUNK_TABLE} TO PUBLIC"))


async def _drop_table() -> None:
    """Remove the table again, so it cannot leak into another test's scope check."""
    async with get_admin_engine().begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {LIGHTRAG_CHUNK_TABLE}"))


async def _lightrag_reads(keys: list[str], *, workspace: str) -> list[dict]:
    """Read chunks back with LightRAG's own ``get_by_ids`` statement.

    Args:
        keys: The chunk ids to ask for.
        workspace: The workspace to ask under.

    Returns:
        The rows LightRAG's ``PGKVStorage.get_by_ids`` would have returned.
    """
    sql = SQL_TEMPLATES["get_by_ids_text_chunks"].replace("$1", ":ws").replace(
        "$2", ":ids"
    )
    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(text(sql), {"ws": workspace, "ids": keys})
        ).mappings()
        return [dict(row) for row in rows]


def test_the_table_and_the_row_are_lightrags_own(monkeypatch) -> None:
    """The table, the columns and the workspace rule come from the installed library.

    Every one of these is a *storage layout* this module has to produce byte-compatibly,
    not an API it calls, so each is checked against ``lightrag`` itself. The workspace is
    the one worth the most: ``PGKVStorage`` falls back to ``"default"`` while
    ``QdrantVectorDBStorage`` falls back to ``"_"``, and this platform writes into both. A
    writer that carried the Qdrant rule over to Postgres would put every row in a partition
    the reader never queries — rows that exist, a table that grows, and an arm that still
    quotes nothing.
    """
    from lightrag.kg.postgres_impl import NAMESPACE_TABLE_MAP
    from lightrag.namespace import NameSpace

    assert (
        NAMESPACE_TABLE_MAP[NameSpace.KV_STORE_TEXT_CHUNKS].lower()
        == LIGHTRAG_CHUNK_TABLE
    )

    declared = {
        line.strip().split()[0].lower()
        for line in _DDL.split("(", 1)[1].splitlines()
        if line.strip() and not line.strip().upper().startswith("CONSTRAINT")
    }
    written = {
        "workspace",
        "id",
        "tokens",
        "chunk_order_index",
        "full_doc_id",
        "content",
        "file_path",
        "create_time",
        "update_time",
    }
    assert written <= declared, (
        f"this writer names column(s) LightRAG does not declare: {written - declared}"
    )

    # The three the reader actually depends on: the primary key, and the field a row is
    # dropped for lacking (``_find_related_text_unit_from_entities`` keeps a row only
    # ``if chunk_data is not None and "content" in chunk_data``).
    read_sql = SQL_TEMPLATES["get_by_ids_text_chunks"]
    assert "workspace=$1" in read_sql and "id = ANY($2)" in read_sql
    assert "content" in read_sql and "file_path" in read_sql

    monkeypatch.delenv("POSTGRES_WORKSPACE", raising=False)
    monkeypatch.setenv("WORKSPACE", "run1")
    assert kv_workspace() == "run1"
    monkeypatch.setenv("POSTGRES_WORKSPACE", "forced")
    assert kv_workspace() == "forced"
    monkeypatch.delenv("POSTGRES_WORKSPACE")
    monkeypatch.delenv("WORKSPACE")
    assert kv_workspace() == DEFAULT_KV_WORKSPACE == "default"


async def test_a_written_chunk_is_found_the_way_lightrag_looks_for_it(db) -> None:
    """A published row comes back out of LightRAG's own read, and a re-run leaves one.

    The read is LightRAG's ``get_by_ids_text_chunks`` verbatim, bound to the same workspace
    the writer resolved — same partition, same key, same columns. That is the whole claim:
    not that the row exists, but that the statement the graph arm actually issues returns
    it, carrying the ``content`` it will quote and the tenant-tagged ``file_path``
    ``_scoped_recall`` will decide on.
    """
    await _create_table()
    try:
        rows = [
            ChunkKVRow(
                key="t1:aaa",
                content="Refunds above 500 USD escalate to a manager.",
                file_path="t1::refund-policy.pdf",
                full_doc_id="7",
                chunk_order_index=0,
            ),
            ChunkKVRow(
                key="t1:bbb",
                content="Escalations are acknowledged within one business day.",
                file_path="t1::refund-policy.pdf",
                full_doc_id="7",
                chunk_order_index=1,
            ),
        ]
        async with get_sessionmaker()() as session:
            result = await publish_chunk_kv(session, rows, workspace=_WORKSPACE)
            await session.commit()
        assert result.complete and result.rows == 2, result

        found = await _lightrag_reads(["t1:aaa", "t1:bbb"], workspace=_WORKSPACE)
        assert {row["id"] for row in found} == {"t1:aaa", "t1:bbb"}
        by_id = {row["id"]: row for row in found}
        assert by_id["t1:aaa"]["content"] == rows[0].content
        assert by_id["t1:aaa"]["file_path"] == "t1::refund-policy.pdf"

        # A workspace the writer did not use is a different partition, not a near miss.
        assert await _lightrag_reads(["t1:aaa"], workspace="another-run") == []

        async with get_sessionmaker()() as session:
            again = await publish_chunk_kv(session, rows, workspace=_WORKSPACE)
            await session.commit()
        assert again.rows == 2
        async with get_sessionmaker()() as session:
            total = (
                await session.execute(
                    text(f"SELECT count(*) FROM {LIGHTRAG_CHUNK_TABLE}")  # noqa: S608
                )
            ).scalar()
        assert total == 2, "a second run duplicated rows the primary key should collapse"
    finally:
        await _drop_table()


async def test_a_missing_kv_table_is_reported_and_never_reported_as_zero(db) -> None:
    """No KV table means an honest unknown, a usable transaction and a live ingest.

    Three things at once because they are one decision. This index is derived: the chunk
    text, the embeddings, the dense index and the graph are all still correct when it
    cannot be written, so the ingest must survive — which means the failure cannot escape
    as an exception *and* cannot leave the stage's transaction poisoned, since the two
    share it. And ``None`` is not ``0``: "the store could not be asked" and "the store
    holds nothing" call for opposite responses, and collapsing them is how an empty index
    passed for a healthy one for five months.
    """
    await _drop_table()
    async with get_sessionmaker()() as session:
        result = await publish_chunk_kv(
            session,
            [ChunkKVRow(key="t1:aaa", content="x", file_path="t1::a.pdf", full_doc_id="7")],
            workspace=_WORKSPACE,
        )
        assert result.rows is None, "a KV that could not be asked reported a count"
        assert result.skipped is not None and LIGHTRAG_CHUNK_TABLE in result.skipped
        assert result.failed is None
        # The transaction the ingest shares with this write is still usable.
        assert (await session.execute(text("SELECT 1"))).scalar() == 1
