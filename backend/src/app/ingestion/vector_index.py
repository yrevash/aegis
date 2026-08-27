"""Rebuild the dense index from the embeddings already stored, and audit the result.

The outage this module repairs, stated once so it is not rediscovered
--------------------------------------------------------------------

Document 7 was ingested at 2026-08-19 21:02. Commit ``3dafbdb`` ("Qdrant everywhere,
Chroma deleted") landed at 2026-08-20 02:22, five hours later. The chunks survived in
PostgreSQL because they are a different store; the vectors did not, because they lived in
the store that was deleted. A vector-engine migration shipped with no re-index step, and
retrieval answered from an empty index without ever saying so.

The second half of the outage is worse than the first, and is the reason this module
exists rather than a one-off script. A re-index *did* exist
(:func:`app.ingestion.reindex.reindex_corpus`) and it could not have helped, because the
``index`` stage it re-runs reported success while writing nothing — see
:func:`verify_document_index` for the mechanism and the evidence. Recovery was
unreachable through the recovery path.

Why this reads ``chunks.embedding`` and never calls the provider
----------------------------------------------------------------

``chunks.embedding`` is the embedding of record. :class:`aegis.jobs.models.Chunk` says so
("the durable JSON source-of-record that index rebuilds replay from, not a search index")
and the ``embed`` stage says why ("an index rebuilt from scratch replays these rows
instead of paying the provider a second time for text that has not changed"). The design
was already right; nothing had implemented the replay.

So a rebuild here costs no tokens and no money. It reads vectors that already exist and
writes them where the search engine can see them. That is what makes it safe to run on a
suspicion rather than a diagnosis — which matters, because the failure it repairs is
invisible from the outside.

Rows with an empty ``embedding`` are skipped, not published and not fabricated. An empty
list is the ``chunk`` stage's explicit "not embedded yet" sentinel (the column is
``NOT NULL``, so the row needs *some* value before ``embed`` runs, and a zero vector of
the right width would be a valid-looking vector pointing nowhere). Those rows need the
``embed`` stage, not this one, and the report names them rather than quietly rounding the
success count down.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from aegis.retrieval.chunk_index import (
    effective_workspace,
    lightrag_point_id,
    prune_stale_chunk_points,
    DEFAULT_CHUNK_COLLECTION,
    ChunkPoint,
    IndexDrift,
    audit_chunk_index,
    publish_chunk_points,
)
from aegis.retrieval.types import chunk_source_id, tenant_metadata_value
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.ingestion.chunk_kv import ChunkKVResult, kv_rows_from_points, publish_chunk_kv

logger = logging.getLogger(__name__)

__all__ = [
    "DenseIndexReport",
    "chunk_points",
    "open_qdrant_client",
    "rebuild_dense_index",
    "verify_document_index",
]

#: How a chunk's owner is written into ``file_path``. Kept identical to
#: ``aegis.retrieval.lightrag_backend._tag_file_path`` — the read path splits on this
#: separator to decide whether a recalled row may be shown to the asking tenant, and a
#: row it cannot attribute is refused outright. The two must not drift, so the shape is
#: asserted by ``test_rebuilt_points_carry_the_owner_tag_recall_demands``.
_TENANT_TAG_SEP = "::"

#: Read in pages rather than all at once: a corpus is unbounded and a rebuild must not be
#: the thing that exhausts the worker's memory.
_PAGE = 500


@dataclass
class DenseIndexReport:
    """What a rebuild did, in numbers that came back from the stores.

    Attributes:
        documents: Documents whose chunks were considered.
        rows: Durable chunk rows read.
        published: Points written into the dense index.
        pruned: Point ids deleted because the current chunking no longer produces them.
        unembedded: Rows skipped because they carry the "not embedded yet" sentinel.
            These need the ``embed`` stage; naming them keeps a partial rebuild from
            reading as a complete one.
        drift: The post-rebuild comparison of the two stores, when it was run.
        kv: What LightRAG's chunk KV confirmed holding for the same rows, when it could
            be asked. Reported beside the vectors rather than folded into them because
            they answer different questions: the vectors are what the dense arm searches,
            the KV is what the graph arm quotes from, and a corpus can have one and not
            the other — which it did, for the whole of the corpus, at 0 rows.
    """

    documents: int = 0
    rows: int = 0
    published: int = 0
    #: Point ids removed because the current chunking no longer produces them. Reported
    #: rather than silent: a rebuild that quietly deletes is a rebuild nobody can audit,
    #: and this list is the evidence that a re-chunk converged instead of accumulating.
    pruned: list[str] = field(default_factory=list)
    unembedded: list[int] = field(default_factory=list)
    drift: IndexDrift | None = None
    kv: ChunkKVResult | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the report as plain data, for a job result or a CLI dump."""
        return {
            "documents": self.documents,
            "rows": self.rows,
            "published": self.published,
            "pruned": len(self.pruned),
            "unembedded": list(self.unembedded),
            "indexed": len(self.drift.present) if self.drift else None,
            "missing": len(self.drift.missing) if self.drift else None,
            "orphaned": len(self.drift.orphaned) if self.drift else None,
            "chunk_kv": self.kv.rows if self.kv else None,
        }


def open_qdrant_client(settings: Settings | None = None) -> Any:  # noqa: ANN401
    """Return a Qdrant client pointed at the node this deployment searches.

    Built from the same ``QDRANT_URL`` the retrieval stack and LightRAG both read, which
    is the point: a rebuild that wrote to a second node would leave ``points_count``
    rising on a store no query reads — a more convincing version of the same outage.

    Args:
        settings: Application settings; defaults to the process singleton.

    Returns:
        A connected ``qdrant_client.QdrantClient``.

    Raises:
        RuntimeError: If no ``QDRANT_URL`` is configured. Falling back to an embedded
            store here would produce a rebuild that "succeeds" into a directory nothing
            serves from.
    """
    from qdrant_client import QdrantClient  # noqa: PLC0415 - lazy, heavy

    settings = settings or get_settings()
    url = (settings.qdrant_url or "").strip()
    if not url:
        raise RuntimeError(
            "QDRANT_URL is not configured, so there is no dense index to rebuild into; "
            "an embedded fallback would write to a store no query reads"
        )
    api_key = (getattr(settings, "qdrant_api_key", "") or "").strip() or None
    return QdrantClient(url=url, api_key=api_key)


async def chunk_points(
    session: AsyncSession,
    *,
    tenant_id: int | None = None,
    document_id: int | None = None,
) -> tuple[list[ChunkPoint], list[int], int]:
    """Read the durable chunk rows for a scope and shape them for the dense index.

    The keys, owner tag and payload built here are byte-for-byte what
    :func:`app.ingestion.stages.index_stage` publishes for the same rows. That is not a
    coincidence to be maintained by hand — it is what makes a rebuild an *overwrite* of
    the ingest's own points rather than a second, parallel set of rows for the same text.
    A duplicate index is the failure mode that looks most like success.

    Args:
        session: A session that can see the rows in scope.
        tenant_id: Restrict to one tenant; ``None`` means every tenant this session sees.
        document_id: Restrict to one document.

    Returns:
        ``(points, unembedded_chunk_ids, document_count)``.
    """
    clauses = ["c.embedding IS NOT NULL"]
    params: dict[str, Any] = {}
    if tenant_id is not None:
        clauses.append("c.tenant_id = :tenant_id")
        params["tenant_id"] = tenant_id
    if document_id is not None:
        clauses.append("c.document_id = :document_id")
        params["document_id"] = document_id

    sql = text(
        f"""
        SELECT c.id, c.tenant_id, c.document_id, c.content, c.meta, c.embedding,
               d.filename
          FROM chunks c
          JOIN documents d ON d.id = c.document_id
         WHERE {" AND ".join(clauses)}
         ORDER BY c.id
        """  # noqa: S608 - clauses are literals chosen above, values stay bound
    )

    points: list[ChunkPoint] = []
    unembedded: list[int] = []
    documents: set[int] = set()

    result = await session.stream(sql, params)
    async for row in result.mappings().partitions(_PAGE):
        for record in row:
            documents.add(int(record["document_id"]))
            vector = record["embedding"] or []
            if not vector:
                # The "not embedded yet" sentinel. Named in the report rather than
                # silently dropped: a rebuild that quietly indexes 30 of 37 chunks is
                # the same shape of lie as one that indexes none.
                unembedded.append(int(record["id"]))
                continue
            meta = record["meta"] or {}
            owner = tenant_metadata_value(int(record["tenant_id"]))
            source = meta.get("source") or record["filename"]
            points.append(
                ChunkPoint(
                    key=chunk_source_id(
                        int(record["tenant_id"]),
                        meta.get("content_id") or record["id"],
                    ),
                    content=record["content"],
                    file_path=f"{owner}{_TENANT_TAG_SEP}{source}",
                    full_doc_id=str(record["document_id"]),
                    vector=vector,
                    ordinal=int(meta.get("ordinal") or 0),
                )
            )
    return points, unembedded, len(documents)


def _audit_scope(
    *, tenant_id: int | None = None, document_id: int | None = None
) -> dict[str, str]:
    """Return the payload filters that narrow an audit to the rows' own scope.

    The rule is one sentence: whatever restricted the ``SELECT`` must restrict the read
    of the index too, or the two sides of the comparison are not answering the same
    question. ``orphaned`` is the direction that goes wrong — it is defined as "points
    the index holds *for this scope* that no durable row claims", and an unscoped read
    hands it every point belonging to every document the operator did not ask about.

    Args:
        tenant_id: The tenant the rebuild was restricted to, if any.
        document_id: The document the rebuild was restricted to, if any.

    Returns:
        Keyword arguments for :func:`audit_chunk_index`; empty for an unscoped run,
        which genuinely does want the whole workspace.
    """
    scope: dict[str, str] = {}
    if tenant_id is not None:
        # The owner tag is a *prefix* of ``file_path``, which is why this needs no
        # payload index — see ``stored_point_ids``.
        scope["file_path_prefix"] = (
            f"{tenant_metadata_value(tenant_id)}{_TENANT_TAG_SEP}"
        )
    if document_id is not None:
        # ``documents.id`` is a global primary key, so the document alone pins the
        # scope; no tenant filter is needed alongside it.
        scope["full_doc_id"] = str(document_id)
    return scope


async def rebuild_dense_index(
    session: AsyncSession,
    *,
    tenant_id: int | None = None,
    document_id: int | None = None,
    client: Any = None,  # noqa: ANN401 - a qdrant_client
    collection: str = DEFAULT_CHUNK_COLLECTION,
    dry_run: bool = False,
) -> DenseIndexReport:
    """Replay the stored chunks for a scope into both of LightRAG's chunk stores.

    Idempotent by construction — every point id is derived from the chunk's
    content-addressed key and the KV's primary key is ``(workspace, that same key)``, so a
    second run overwrites the same rows and a corpus that has not changed converges rather
    than growing. Nothing is deleted first, so there is no window in which the tenant's
    index is empty.

    Two stores, because LightRAG reads chunks two ways and this platform had only ever
    written one of them. The vectors answer the dense arm. The KV
    (``lightrag_doc_chunks``) is what the graph arm resolves a matched entity's chunk ids
    through, and it held 0 rows for every workspace — see :mod:`app.ingestion.chunk_kv`.
    The KV outcome is reported separately and never folded into ``published``: they are
    different stores and a run can legitimately populate one and be unable to reach the
    other.

    Args:
        session: A session that can see the chunks in scope.
        tenant_id: Restrict to one tenant.
        document_id: Restrict to one document.
        client: An open Qdrant client; ``None`` opens the configured one.
        collection: The dense collection to rebuild into.
        dry_run: Read and report, write nothing. The audit still runs, so a dry run is
            the honest way to ask "is the index already correct?" before spending a
            write.

    Returns:
        The :class:`DenseIndexReport`, whose counts are read back from the store.
    """
    points, unembedded, documents = await chunk_points(
        session, tenant_id=tenant_id, document_id=document_id
    )
    report = DenseIndexReport(
        documents=documents,
        rows=len(points) + len(unembedded),
        unembedded=unembedded,
    )

    owned = client is None
    qdrant = client or open_qdrant_client()
    try:
        if not dry_run:
            report.published = publish_chunk_points(
                qdrant, points, collection=collection
            )
            # Prune AFTER publishing, never before.
            #
            # Order matters: publish-then-prune means the scope is never empty, so a
            # crash between the two leaves a superset (retrievable, with some
            # duplicates) rather than a hole (silently unretrievable). The old order
            # of operations had no prune at all, which is why a re-chunk left the
            # previous chunking in place and 30% of this deployment's points became
            # orphans that were still being retrieved and cited.
            #
            # Scoped with the SAME arguments the audit uses, so a per-document rebuild
            # can never reach another document's points, and the helper itself refuses
            # an empty expected set.
        # Pruned whether or not this is a dry run — with the flag FORWARDED, so a dry
        # run reports what it WOULD delete instead of reporting nothing. A destructive
        # step whose preview is always "0" is a preview nobody can act on, which is how
        # the first version of this call read.
        if points:
            report.pruned = sorted(
                prune_stale_chunk_points(
                    qdrant,
                    [
                        lightrag_point_id(point.key, workspace=effective_workspace())
                        for point in points
                    ],
                    collection=collection,
                    dry_run=dry_run,
                    **_audit_scope(tenant_id=tenant_id, document_id=document_id),
                )
            )

        # Audited whether or not anything was written, and audited from the *store*.
        # A rebuild that reports what it intended to write is the writer believing
        # itself, which is the defect this whole module answers.
        #
        # The audit is given the *same* scope the rows were read under. Without that it
        # asks a narrow question of PostgreSQL and a collection-wide one of Qdrant, so
        # every other tenant's healthy points come back as orphans and a scoped rebuild
        # exits non-zero on a corpus that is entirely correct. A failure signal that
        # fires on success is worse than none: it is the funnel's "1" all over again.
        report.drift = audit_chunk_index(
            qdrant,
            points,
            collection=collection,
            **_audit_scope(tenant_id=tenant_id, document_id=document_id),
        )
    finally:
        if owned:
            qdrant.close()

    # LightRAG's *other* chunk store, replayed from the same list. A corpus whose vectors
    # are perfect and whose KV is empty is exactly what this deployment was: the graph arm
    # matched entities and could not quote one line of them, because
    # ``text_chunks.get_by_ids`` had nothing to return. Same rows, same keys, same tenant
    # tag — see :mod:`app.ingestion.chunk_kv`.
    report.kv = await publish_chunk_kv(
        session, kv_rows_from_points(points), dry_run=dry_run
    )

    logger.info(
        "dense index rebuild (tenant=%s document=%s dry_run=%s): %s; %d row(s) "
        "still unembedded",
        tenant_id,
        document_id,
        dry_run,
        report.drift.describe() if report.drift else "not audited",
        len(unembedded),
    )
    return report


async def verify_document_index(
    session: AsyncSession,
    *,
    document_id: int,
    client: Any = None,  # noqa: ANN401 - a qdrant_client
    collection: str = DEFAULT_CHUNK_COLLECTION,
) -> IndexDrift:
    """Return whether the dense index actually holds this document's chunks.

    This is the question the ``index`` stage never asked, and not asking it is the whole
    defect. ``LightRAG.ainsert`` records per-document failures into its own doc-status
    store and **returns normally**; the stage then logged "indexed 37 chunk(s)" and
    reported success while the collection stayed at zero points. The platform's Jobs
    funnel showed the shape of it — parse 199, chunk/enrich/embed 156, index/graph 1 —
    and the collapse to 1 was read as a display bug for months.

    A count that comes back from the store is evidence. A count the writer incremented is
    a claim. This function only returns the former.

    Args:
        session: A session that can see the document's chunks.
        document_id: The document to check.
        client: An open Qdrant client; ``None`` opens the configured one.
        collection: The dense collection to check.

    Returns:
        The :class:`IndexDrift` between this document's rows and the index.
    """
    points, _unembedded, _documents = await chunk_points(
        session, document_id=document_id
    )
    owned = client is None
    qdrant = client or open_qdrant_client()
    try:
        return audit_chunk_index(
            qdrant,
            points,
            collection=collection,
            **_audit_scope(document_id=document_id),
        )
    finally:
        if owned:
            qdrant.close()
