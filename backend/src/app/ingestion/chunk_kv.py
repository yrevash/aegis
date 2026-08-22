"""Write the chunk KV LightRAG resolves an entity back into a passage through.

The half of the graph arm that returned entities and no text
------------------------------------------------------------

:mod:`app.ingestion.graph_vectors` made the ``local`` arm able to *find* an entity, and
that half works: LightRAG's own log went from nothing to ``Local query: 5 entites, 9
relations``. It still contributed **0 candidates** to the merged ranking, because finding
an entity is not the same as finding a passage. ``local`` turns one into the other in
:func:`lightrag.operate._find_related_text_unit_from_entities`, which reads ``source_id``
off the matched **graph node**, splits it on ``<SEP>`` into chunk ids, and hands those ids
to ``text_chunks.get_by_ids`` — the KV store, table ``LIGHTRAG_DOC_CHUNKS``. On the live
deployment that table held **0 rows for every workspace**, and the nodes carried no
``source_id`` at all, so the function logged ``No entities with text chunks found`` and
returned ``[]`` before it ever reached the store.

Both ends of that lookup were missing and both are now written: the node's ``source_id``
by :mod:`app.ingestion.graph_projection`, and the rows it points at by this module.

Nothing wrote them because nothing was ever going to. ``text_chunks.upsert`` is called
from exactly one place in LightRAG — inside ``ainsert``/``ainsert_custom_chunks`` — and
:meth:`aegis.retrieval.lightrag_backend.LightRAGBackend.publish_vectors` deliberately
bypasses that route (see its docstring: 36 of 37 chunks rejected as duplicate documents,
73 ``FAILED`` rows in ``lightrag_doc_status`` to prove it). This module is to the chunk KV
what :mod:`aegis.retrieval.chunk_index` is to the chunk vectors: the same rows, written
directly, under LightRAG's own storage contract.

The contract, read out of the installed library rather than guessed
-------------------------------------------------------------------

* **The table** — ``lightrag.kg.postgres_impl.NAMESPACE_TABLE_MAP`` maps
  ``KV_STORE_TEXT_CHUNKS`` to ``LIGHTRAG_DOC_CHUNKS``, whose DDL and
  ``(workspace, id)`` primary key are in ``TABLES`` in the same module.
* **The row** — ``PGKVStorage.upsert`` builds its tuple from ``v["tokens"]``,
  ``v["chunk_order_index"]``, ``v["full_doc_id"]``, ``v["content"]``, ``v["file_path"]``
  (plus three JSONB columns it defaults). ``ainsert_custom_chunks`` is what fills that
  mapping, and it fills exactly those five keys.
* **The read** — ``SQL_TEMPLATES["get_by_ids_text_chunks"]`` selects
  ``WHERE workspace=$1 AND id = ANY($2)``, and the caller keeps a row only when it carries
  ``content``. So ``id``, ``workspace`` and ``content`` are the three fields that decide
  whether a row exists at all; ``file_path`` is what decides whether the row may be
  *shown* (see below).

``tokens`` is written ``NULL``, deliberately. LightRAG derives it from its own tokenizer,
which Aegis does not run, and no reader on the retrieval path looks at it — the only
readers of a chunk's ``tokens`` in the installed package are the chunkers, which the
``ainsert`` bypass means we never reach. A plausible-looking number computed a different
way would be a fabrication in a column that has a precise meaning; ``NULL`` says what is
true, which is that this writer did not count LightRAG's tokens.

The workspace is resolved LightRAG's way, and it is **not** the Qdrant rule
--------------------------------------------------------------------------

``PGKVStorage.initialize`` resolves ``POSTGRES_WORKSPACE`` → the instance's ``workspace``
(which is ``os.getenv("WORKSPACE", "")``) → the literal ``"default"``. That last fallback
is where this differs from :func:`aegis.retrieval.chunk_index.effective_workspace`, whose
Qdrant-side fallback is ``"_"``. Two different stores, two different defaults, and the
workspace is part of the primary key here — so a row written under the wrong one is not a
row that fails to match, it is a row in a partition nobody reads. That is the same class
of failure the Neo4j label bug was, and it cost a whole knowledge graph.

Why a failure here is reported and never raised
-----------------------------------------------

Same posture, and for the same reason, as :mod:`app.ingestion.graph_vectors`: the durable
stores are all still correct when this fails. The text is in ``chunks.content``, the
vector is in the dense index and was verified there, the graph is in Neo4j and was
verified there. What is lost is one arm's ability to quote a passage it can already find —
a derived index, rebuildable from durable rows at any time by
:func:`app.ingestion.vector_index.rebuild_dense_index`. Discarding a wholly correct
document over it would be the worse trade. So the result carries ``None`` (never a
fabricated zero) plus a ``skipped``/``failed`` reason, and the caller records it.

The write runs inside a ``SAVEPOINT`` for the same reason it is caught: a failed statement
poisons its transaction, and the transaction it shares is the ingest's. The savepoint is
what keeps "this index blipped" from becoming "this stage rolled back".
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_KV_WORKSPACE",
    "LIGHTRAG_CHUNK_TABLE",
    "ChunkKVResult",
    "ChunkKVRow",
    "kv_rows_from_points",
    "kv_workspace",
    "publish_chunk_kv",
]

#: The table ``lightrag.kg.postgres_impl.NAMESPACE_TABLE_MAP`` maps LightRAG's
#: ``KV_STORE_TEXT_CHUNKS`` namespace to. Lower-cased because Postgres folds the unquoted
#: identifier in LightRAG's own DDL.
LIGHTRAG_CHUNK_TABLE = "lightrag_doc_chunks"

#: ``PGKVStorage``'s last-resort workspace. Not ``"_"`` — see the module docstring.
DEFAULT_KV_WORKSPACE = "default"

#: Rows per statement. The KV rows carry a chunk's full text, so this is about not
#: building one multi-megabyte ``INSERT`` out of a long document, not about round-trips.
_BATCH = 100


@dataclass(frozen=True, slots=True)
class ChunkKVRow:
    """One chunk as LightRAG's KV store holds it.

    Attributes:
        key: The chunk id — the same ``t<tenant>:<content hash>`` string
            :func:`aegis.retrieval.types.chunk_source_id` gives the dense point and the
            graph node's ``source_id``. All three must be one string: the id is the only
            thing joining a matched entity to the text it was extracted from, and an id
            that differs by a character resolves to nothing at all.
        content: The chunk's text. The field the reader keeps a row for; a row without it
            is dropped by ``_find_related_text_unit_from_entities`` without comment.
        file_path: The tenant-tagged source path (``"t1::policy.pdf"``). Load-bearing:
            the passages this arm produces are filtered by
            :func:`aegis.retrieval.lightrag_backend._scoped_recall`, which reads the owner
            tag off exactly this field. A row written untagged is refused at read time —
            which is the safe direction, and still a defect.
        full_doc_id: The owning document's id, as a string.
        chunk_order_index: The chunk's ordinal within its document.
    """

    key: str
    content: str
    file_path: str
    full_doc_id: str
    chunk_order_index: int = 0


@dataclass(frozen=True, slots=True)
class ChunkKVResult:
    """What the chunk KV confirmed holding, counted by reading it back.

    Attributes:
        rows: Rows the store confirmed for the keys just written, or ``None`` when that
            could not be established. Never a fabricated zero.
        attempted: Distinct rows sent.
        skipped: Why nothing was attempted, or ``None``. "This database has no LightRAG
            chunk table yet" and "the write failed" are different facts with different
            responses, so they are never collapsed.
        failed: Why an attempt did not complete, or ``None``.
    """

    rows: int | None = 0
    attempted: int = 0
    skipped: str | None = None
    failed: str | None = None

    @property
    def complete(self) -> bool:
        """True when every row sent was confirmed present afterwards."""
        return self.rows is not None and self.rows >= self.attempted


def kv_workspace(workspace: str | None = None) -> str:
    """Return the workspace LightRAG's Postgres KV partitions its rows under.

    Replicates ``PGKVStorage.initialize``'s resolution order exactly — ``POSTGRES_WORKSPACE``
    (via ``PostgreSQLDB.workspace``) outranks the LightRAG instance's own ``workspace``,
    which is ``os.getenv("WORKSPACE", "")``, which falls back to ``"default"``.

    Args:
        workspace: An explicit workspace, which outranks the environment.

    Returns:
        The workspace to write and read under. It is half of the table's primary key, so
        a wrong answer here writes rows that exist and are never found.
    """
    if workspace:
        return workspace
    forced = os.environ.get("POSTGRES_WORKSPACE", "")
    if forced.strip():
        return forced.strip()
    return os.environ.get("WORKSPACE", "").strip() or DEFAULT_KV_WORKSPACE


async def _unavailable(session: AsyncSession, table: str) -> str | None:
    """Return why the chunk KV must not be written, or ``None`` when it may be.

    Two refusals, and each is a deliberate configuration rather than a failure — the same
    stance :func:`app.ingestion.graph_projection._unavailable` takes:

    * **Not PostgreSQL.** The statements below are ``ON CONFLICT``/``to_regclass``
      Postgres, because LightRAG's KV table is Postgres. A session bound to anything else
      is a deployment that does not have this store.
    * **No table yet.** LightRAG declares ``LIGHTRAG_DOC_CHUNKS`` in its own
      ``initialize_storages()``. Creating it here would make Aegis the owner of a schema it
      does not own, and a column LightRAG later adds would then be missing without anybody
      noticing.

    Args:
        session: The session the write would run on.
        table: The table name to look for.

    Returns:
        A reason, or ``None``.
    """
    dialect = getattr(getattr(session, "bind", None), "dialect", None)
    name = getattr(dialect, "name", None)
    if name != "postgresql":
        return f"the session is bound to {name or 'an unknown dialect'}, not PostgreSQL"
    present = (
        await session.execute(text("SELECT to_regclass(:name)"), {"name": table})
    ).scalar()
    if present is None:
        return (
            f"{table} does not exist in this database; LightRAG creates it in its own "
            "initialize_storages(), and writing a table it has not declared yet would "
            "make Aegis the owner of a schema it does not own"
        )
    return None


async def publish_chunk_kv(
    session: AsyncSession,
    rows: Sequence[ChunkKVRow],
    *,
    workspace: str | None = None,
    table: str = LIGHTRAG_CHUNK_TABLE,
    dry_run: bool = False,
) -> ChunkKVResult:
    """Upsert ``rows`` into LightRAG's chunk KV and report what the store confirms.

    Idempotent by the table's own primary key: ``(workspace, id)`` with an
    ``ON CONFLICT DO UPDATE``, and the id is the chunk's content-addressed key, so a
    re-ingest of unchanged text rewrites the same row rather than adding one. Nothing is
    deleted first, so there is no window in which the graph arm can find an entity whose
    passages have gone missing.

    Args:
        session: The session to write on — the caller's own, so the rows commit with the
            stage that produced them. The statement runs inside a ``SAVEPOINT``: a failure
            here must not poison a transaction that is otherwise correct.
        rows: The chunks to write.
        workspace: The workspace to write under; ``None`` resolves LightRAG's own.
        table: The KV table; parameterised for the tests that pin the contract against
            LightRAG's DDL rather than for deployments.
        dry_run: Read back only, write nothing. The confirmation still runs, so a dry run
            answers "is the KV already correct?" without spending a write.

    Returns:
        What was attempted and what the store confirmed. Never raises for an unwritable
        store: see the module docstring for why this degrades and reports instead.
    """
    if not rows:
        return ChunkKVResult()

    reason = await _unavailable(session, table)
    if reason is not None:
        return ChunkKVResult(rows=None, attempted=len(rows), skipped=reason)

    ws = kv_workspace(workspace)
    # Two chunks of one document cannot share a content-addressed key, but a caller that
    # replays several documents at once can hand over the same key twice for text that is
    # genuinely identical. Postgres refuses an ON CONFLICT that hits the same row twice in
    # one statement, so the collapse happens here rather than as a runtime error.
    unique: dict[str, ChunkKVRow] = {}
    for row in rows:
        if not row.key:
            raise ValueError(
                "a chunk key is required to address a KV row; an empty key would "
                "collapse every chunk of the document onto one row"
            )
        unique.setdefault(row.key, row)
    ordered = list(unique.values())

    upsert = text(
        f"""
        INSERT INTO {table}
            (workspace, id, tokens, chunk_order_index, full_doc_id, content, file_path,
             create_time, update_time)
        VALUES (:workspace, :id, NULL, :chunk_order_index, :full_doc_id, :content,
                :file_path, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT (workspace, id) DO UPDATE
           SET chunk_order_index = EXCLUDED.chunk_order_index,
               full_doc_id       = EXCLUDED.full_doc_id,
               content           = EXCLUDED.content,
               file_path         = EXCLUDED.file_path,
               update_time       = EXCLUDED.update_time
        """  # noqa: S608 - ``table`` is this module's own constant or a test's literal
    )
    confirm = text(
        f"SELECT id FROM {table} WHERE workspace = :workspace AND id = ANY(:ids)"  # noqa: S608
    )

    try:
        async with session.begin_nested():
            if not dry_run:
                for start in range(0, len(ordered), _BATCH):
                    await session.execute(
                        upsert,
                        [
                            {
                                "workspace": ws,
                                "id": row.key,
                                "chunk_order_index": int(row.chunk_order_index),
                                "full_doc_id": row.full_doc_id,
                                "content": row.content,
                                "file_path": row.file_path,
                            }
                            for row in ordered[start : start + _BATCH]
                        ],
                    )
            # Read back by exact key, never the writer's own count: the whole defect
            # class here is a writer that believed itself.
            present = (
                await session.execute(
                    confirm, {"workspace": ws, "ids": [row.key for row in ordered]}
                )
            ).scalars().all()
    except Exception as exc:  # noqa: BLE001 - every failure here is the same outcome
        logger.exception(
            "could not write %d chunk(s) into LightRAG's chunk KV under workspace %r; "
            "the graph arm will find entities for them and no passages",
            len(ordered),
            ws,
        )
        # ``str(TimeoutError())`` is the empty string; an empty reason reads as none.
        return ChunkKVResult(
            rows=None, attempted=len(ordered), failed=str(exc) or type(exc).__name__
        )

    logger.info(
        "%s %d chunk KV row(s) under workspace %r; the store reports holding %d",
        "would write" if dry_run else "wrote",
        len(ordered),
        ws,
        len(present),
    )
    return ChunkKVResult(rows=len(present), attempted=len(ordered))


def kv_rows_from_points(points: Sequence[Any]) -> list[ChunkKVRow]:
    """Shape dense-index points into KV rows for the same chunks.

    The two stores must agree about which chunks exist and what each one says, so they are
    built from one list rather than from two reads that could drift. A chunk in the dense
    index whose KV row is missing is an entity the graph arm matches and cannot quote; a
    KV row with no dense point is a passage no query reaches.

    Args:
        points: :class:`aegis.retrieval.chunk_index.ChunkPoint` objects.

    Returns:
        One :class:`ChunkKVRow` per point, in the order given.
    """
    return [
        ChunkKVRow(
            key=point.key,
            content=point.content,
            file_path=point.file_path,
            full_doc_id=point.full_doc_id,
            chunk_order_index=point.ordinal,
        )
        for point in points
    ]
