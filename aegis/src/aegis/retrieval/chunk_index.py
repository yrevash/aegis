"""Publish the embedding of record into the dense index, and prove it landed.

This module exists because of a specific outage, and its shape is the lesson from it.
``chunks.embedding`` is the platform's **embedding of record** — the ``embed`` stage's
own docstring says so, and says why: "an index rebuilt from scratch replays these rows
instead of paying the provider a second time for text that has not changed". The vector
*index* is a derived artifact built from those rows. When the vector engine was swapped
(Chroma deleted for Qdrant in ``3dafbdb``), the derived artifact went with it and the
rows survived — exactly as designed — but nothing existed to replay them. Retrieval
returned nothing for five months of corpus while the source of truth sat intact one
table away.

So: **rebuilding the dense index never calls the embedding provider.** It reads the
vectors already stored beside the text and writes them into the search engine. That is
what makes a re-index cost nothing, run in seconds, and be safe to re-run on a hunch.

Speaking LightRAG's dialect, deliberately
-----------------------------------------

The dense arm this platform searches is LightRAG's ``chunks`` vector store, so the points
written here are addressed and shaped the way *LightRAG* addresses and shapes them —
:func:`lightrag_point_id` and :data:`_PAYLOAD_KEYS` replicate
``lightrag.kg.qdrant_impl``'s scheme rather than :mod:`aegis.retrieval.vector_store`'s
own (which is a different, equally deliberate dialect for collections Aegis owns
outright).

That replication is the one real risk in this file, and it is answered by a test rather
than by a comment: ``test_point_id_matches_lightrag_exactly`` asserts this module's id
against ``lightrag.kg.qdrant_impl.compute_mdhash_id_for_qdrant`` for the same inputs. If
LightRAG ever changes the scheme, that test fails — instead of this module quietly
writing points beside the ones LightRAG reads, which is a duplicate index and the same
class of silent disagreement this file was written to end.

Why not simply import LightRAG's function? Because importing ``lightrag`` costs seconds
and drags in the whole storage stack, and the standalone ``aegis`` package must stay
importable without it. The test imports it; the runtime does not.

Ids, and why re-running is safe
-------------------------------

A point's id is derived from the chunk's **content-addressed** key, which is derived from
the chunk's text. Re-publishing an unchanged chunk therefore computes the same point id
and *overwrites* the same row. Running a re-index twice leaves exactly one of everything;
running it against a corpus where one chunk changed replaces exactly that one. There is
no delete-then-insert window in which the tenant's index is empty.

The tenant travels twice, on purpose
------------------------------------

``file_path`` carries the ``t<id>::`` owner tag that
:func:`aegis.retrieval.lightrag_backend._scoped_recall` reads to decide whether a
recalled row may be shown to the asking tenant — a row that arrives without it is refused
at read time. ``workspace_id`` is LightRAG's own partition key and is what the engine
filters on before ranking. Neither is redundant: one is the store's filter, the other is
the platform's proof, and the outage this module repairs is what a store looks like when
a write path and a read path agree about a contract that nothing checks.
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "DEFAULT_CHUNK_COLLECTION",
    "ChunkPoint",
    "IndexDrift",
    "audit_chunk_index",
    "effective_workspace",
    "lightrag_point_id",
    "point_payload",
    "publish_chunk_points",
    "stored_point_ids",
]

#: The collection LightRAG's dense (``naive``) arm searches. Named as the default rather
#: than hardcoded at the call sites so a deployment that runs a suffixed collection can
#: pass its own, and so the one place this string lives is greppable.
DEFAULT_CHUNK_COLLECTION = "lightrag_vdb_chunks"

#: LightRAG's fallback workspace when neither ``QDRANT_WORKSPACE`` nor ``WORKSPACE`` is
#: exported — ``lightrag.kg.qdrant_impl.DEFAULT_WORKSPACE``. A single underscore, which
#: is easy to mistake for a placeholder and is not one: points written under any other
#: workspace are invisible to a reader filtering on this one.
DEFAULT_WORKSPACE = "_"

#: The payload keys LightRAG's chunk store writes and its reader consumes.
#:
#: ``content`` and ``file_path`` are read back by ``lightrag.operate._get_vector_context``
#: (a payload lacking ``content`` is silently dropped from the result set — which is how
#: a subtly-wrong payload becomes another silent empty answer); ``id`` is the caller's own
#: key, handed back for deduplication; ``full_doc_id`` is chunk-store metadata;
#: ``workspace_id`` is the partition every query filters on; ``created_at`` is stamped for
#: parity with LightRAG's own writes.
_PAYLOAD_KEYS = ("id", "workspace_id", "created_at", "full_doc_id", "content", "file_path")


@dataclass(frozen=True, slots=True)
class ChunkPoint:
    """One chunk, its stored vector, and the provenance the reader needs.

    Attributes:
        key: The chunk's content-addressed, tenant-prefixed key — the id LightRAG stores
            in the payload and the seed for the point id. Stable across re-index runs for
            unchanged text, which is what makes publishing idempotent.
        content: The chunk's text. Carried in the payload because the reader returns it
            as the grounded passage; a point whose payload omits it is dropped without
            comment by LightRAG's reader.
        file_path: The tenant-tagged source path (``"t1::paper.pdf"``). The owner tag is
            load-bearing at read time — see the module docstring.
        full_doc_id: The owning document's id, as a string.
        vector: The embedding of record, read from ``chunks.embedding``. Never recomputed
            here.
    """

    key: str
    content: str
    file_path: str
    full_doc_id: str
    vector: Sequence[float]


@dataclass(frozen=True, slots=True)
class IndexDrift:
    """What :func:`audit_chunk_index` found when it compared the two stores.

    The point of naming the two directions separately is that they are different bugs
    with different repairs. ``missing`` is the outage this module was written for: rows
    exist, vectors do not, retrieval is silently empty. ``orphaned`` is its mirror image:
    points survive for chunks that were deleted or re-chunked, so retrieval grounds an
    answer in text that no longer exists in the corpus — which reads as a hallucination
    and is not one.

    Attributes:
        expected: Point ids the durable rows say should be in the index.
        present: Of those, the ids the index actually holds.
        missing: Expected ids the index does not hold.
        orphaned: Ids the index holds for this scope that no durable row claims. Empty
            when the audit was given no way to enumerate the collection.
    """

    expected: frozenset[str]
    present: frozenset[str]
    missing: frozenset[str]
    orphaned: frozenset[str] = frozenset()

    @property
    def agrees(self) -> bool:
        """Whether the durable rows and the dense index tell the same story."""
        return not self.missing and not self.orphaned

    def describe(self) -> str:
        """Return a one-line summary fit for a log line or an assertion message."""
        return (
            f"{len(self.present)}/{len(self.expected)} chunk vector(s) indexed; "
            f"{len(self.missing)} missing, {len(self.orphaned)} orphaned"
        )


def effective_workspace(workspace: str | None = None) -> str:
    """Return the Qdrant workspace LightRAG partitions its points under.

    Replicates ``QdrantVectorDBStorage``'s own resolution order exactly, because a
    mismatch here is invisible: points land in the collection, ``points_count`` rises,
    and every query still returns nothing because the workspace filter excludes them all.
    That failure looks like success from every angle except the answer.

    Args:
        workspace: An explicit workspace, which outranks the environment. ``None``
            resolves it the way the running platform does.

    Returns:
        The workspace to write and filter under.
    """
    if workspace:
        return workspace
    forced = os.environ.get("QDRANT_WORKSPACE", "")
    if forced.strip():
        return forced.strip()
    return os.environ.get("WORKSPACE", "") or DEFAULT_WORKSPACE


def lightrag_point_id(key: str, *, workspace: str) -> str:
    """Return the Qdrant point id LightRAG would give ``key`` under ``workspace``.

    Qdrant point ids must be UUIDs or unsigned ints, and ``key`` is neither, so LightRAG
    hashes ``workspace + key`` and reads the first 16 bytes as a UUID. Deterministic by
    construction: the same chunk under the same workspace always addresses the same row,
    so a re-publish overwrites rather than duplicates.

    The workspace is part of the hashed material rather than only a payload field. That
    is LightRAG's choice and it is a good one — two workspaces holding the same chunk get
    two rows instead of racing to overwrite one — but it does mean an id computed under
    the wrong workspace is simply a different row, silently.

    Args:
        key: The caller's own chunk key.
        workspace: The effective workspace (see :func:`effective_workspace`).

    Returns:
        The point id, as a 32-character hex UUID with no dashes.

    Raises:
        ValueError: If ``key`` is empty. LightRAG raises here too; an empty key would
            make every such chunk collide on one point id, so the whole document would
            index as a single row.
    """
    if not key:
        raise ValueError(
            "a chunk key is required to address a vector point; an empty key would "
            "collapse every chunk of the document onto one point id"
        )
    digest = hashlib.sha256((workspace + key).encode("utf-8")).digest()
    return uuid.UUID(bytes=digest[:16], version=4).hex


def point_payload(
    point: ChunkPoint, *, workspace: str, created_at: int | None = None
) -> dict[str, Any]:
    """Return the payload LightRAG's chunk reader expects for ``point``.

    Args:
        point: The chunk being published.
        workspace: The effective workspace to tag the point with.
        created_at: Unix seconds to stamp; defaults to now.

    Returns:
        A payload holding exactly :data:`_PAYLOAD_KEYS`.
    """
    return {
        "id": point.key,
        "workspace_id": workspace,
        "created_at": int(time.time()) if created_at is None else int(created_at),
        "full_doc_id": point.full_doc_id,
        "content": point.content,
        "file_path": point.file_path,
    }


def _require_client(client: object) -> Any:  # noqa: ANN401 - a qdrant_client duck type
    """Return ``client`` if it can serve as a Qdrant client, else raise.

    Args:
        client: The candidate client.

    Returns:
        The client, unchanged.

    Raises:
        TypeError: If it does not expose the calls this module makes.
    """
    for method in ("upsert", "scroll", "collection_exists"):
        if not callable(getattr(client, method, None)):
            raise TypeError(
                f"{client!r} is not a Qdrant client: it has no callable {method!r}, so "
                "publishing against it would not reach a vector store"
            )
    return client


def publish_chunk_points(
    client: object,
    points: Sequence[ChunkPoint],
    *,
    collection: str = DEFAULT_CHUNK_COLLECTION,
    workspace: str | None = None,
    batch_size: int = 128,
) -> int:
    """Write ``points`` into the dense index and return how many were written.

    Idempotent: every point id is derived from the chunk's content-addressed key, so a
    second run over unchanged text overwrites the same rows. There is no delete step —
    a re-index never opens a window in which the tenant's index is empty.

    Args:
        client: A ``qdrant_client.QdrantClient`` (or anything with its ``upsert``).
        points: The chunks to publish, each carrying its stored vector.
        collection: The collection to write into.
        workspace: The workspace to tag with; ``None`` resolves the platform's.
        batch_size: Points per upsert request. LightRAG's own default is 128 and the
            payloads here are the same shape, so the same bound applies.

    Returns:
        The number of points written.

    Raises:
        TypeError: If ``client`` is not a Qdrant client.
        LookupError: If ``collection`` does not exist. Created lazily by whoever owns the
            collection's dimensionality — creating it here at a guessed width is how a
            3072-dim corpus ends up in a 1536-dim collection.
        ValueError: If any vector is empty, which is the ``embed`` stage's explicit
            "not embedded yet" sentinel and must never be published as if it were one.
    """
    from qdrant_client import models  # noqa: PLC0415 - lazy: heavy optional dependency

    qdrant = _require_client(client)
    if not points:
        return 0
    if not qdrant.collection_exists(collection):
        raise LookupError(
            f"vector collection {collection!r} does not exist; it is created by the "
            "process that owns its dimensionality, and creating it here at a guessed "
            "width would silently build an index no query can use"
        )

    ws = effective_workspace(workspace)
    stamp = int(time.time())
    structs = []
    for point in points:
        if not point.vector:
            raise ValueError(
                f"chunk {point.key!r} carries no embedding; an empty vector is the "
                "'not embedded yet' sentinel the embed stage writes, and publishing it "
                "would put an unsearchable row into the index under a real chunk's id"
            )
        structs.append(
            models.PointStruct(
                id=lightrag_point_id(point.key, workspace=ws),
                vector=[float(value) for value in point.vector],
                payload=point_payload(point, workspace=ws, created_at=stamp),
            )
        )

    for start in range(0, len(structs), batch_size):
        qdrant.upsert(
            collection_name=collection,
            points=structs[start : start + batch_size],
            wait=True,
        )
    return len(structs)


def stored_point_ids(
    client: object,
    *,
    collection: str = DEFAULT_CHUNK_COLLECTION,
    workspace: str | None = None,
    file_path_prefix: str | None = None,
    full_doc_id: str | None = None,
    limit: int = 10_000,
) -> frozenset[str]:
    """Return the ids of the points this scope holds in the dense index.

    Read from the engine rather than from a counter the writer kept, because the whole
    defect class this module addresses is a writer that believed itself. A count that
    comes back from the store is evidence; a count the writer incremented is a claim.

    Args:
        client: A Qdrant client.
        collection: The collection to read.
        workspace: The workspace to filter on; ``None`` resolves the platform's.
        file_path_prefix: When given, keep only points whose ``file_path`` starts with
            it — the owner tag (``"t1::"``) is a prefix, so this scopes the read to one
            tenant without needing a payload index on the field.
        full_doc_id: When given, keep only points whose payload names this owning
            document. Matched in Python for the same reason as ``file_path_prefix``: a
            server-side condition on an unindexed payload field is a full scan anyway,
            and requiring an index would make the audit fail on a collection LightRAG
            created rather than merely run slower.
        limit: Ceiling on points scanned, so an audit against a large corpus cannot turn
            into an unbounded read.

    Returns:
        The point ids present, as hex UUID strings.

    Raises:
        TypeError: If ``client`` is not a Qdrant client.
    """
    from qdrant_client import models  # noqa: PLC0415 - lazy: heavy optional dependency

    qdrant = _require_client(client)
    if not qdrant.collection_exists(collection):
        return frozenset()

    ws = effective_workspace(workspace)
    scope_filter = models.Filter(
        must=[
            models.FieldCondition(
                key="workspace_id", match=models.MatchValue(value=ws)
            )
        ]
    )

    found: set[str] = set()
    offset: Any = None
    while len(found) < limit:
        records, offset = qdrant.scroll(
            collection_name=collection,
            scroll_filter=scope_filter,
            limit=min(256, limit - len(found)),
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for record in records:
            payload = record.payload or {}
            if file_path_prefix is not None:
                path = str(payload.get("file_path") or "")
                if not path.startswith(file_path_prefix):
                    continue
            if full_doc_id is not None and str(
                payload.get("full_doc_id") or ""
            ) != str(full_doc_id):
                continue
            found.add(str(record.id).replace("-", ""))
        if offset is None or not records:
            break
    return frozenset(found)


def audit_chunk_index(
    client: object,
    expected: Iterable[ChunkPoint] | Mapping[str, Any] | Iterable[str],
    *,
    collection: str = DEFAULT_CHUNK_COLLECTION,
    workspace: str | None = None,
    file_path_prefix: str | None = None,
    full_doc_id: str | None = None,
) -> IndexDrift:
    """Compare what the durable rows claim against what the dense index holds.

    This is the check whose absence *was* the outage. The chunks and their vectors live
    in two stores that can each be correct on their own while disagreeing with each
    other, and nothing in the platform ever asked them the same question. Asking is
    cheap; the five months of silently-empty retrieval were not.

    Args:
        client: A Qdrant client.
        expected: The chunks that should be indexed — :class:`ChunkPoint` objects, or
            their keys as strings.
        collection: The collection to audit.
        workspace: The workspace to audit within; ``None`` resolves the platform's.
        file_path_prefix: Restrict the "what is actually there" read to one tenant's
            owner tag, so ``orphaned`` means "orphaned for this tenant" rather than
            "belongs to a tenant this audit did not ask about".
        full_doc_id: Restrict that read further, to one document. Both scopes exist for
            the same reason: an audit that asks a narrow question of the rows and a wide
            one of the index reports every *other* document's healthy points as orphans,
            and a scoped re-index that always exits non-zero teaches operators to ignore
            the exit code — which is how the funnel's collapse to 1 went unread for
            months.

    Returns:
        The :class:`IndexDrift` between the two stores.
    """
    ws = effective_workspace(workspace)
    keys = [
        item.key if isinstance(item, ChunkPoint) else str(item)
        for item in expected  # type: ignore[union-attr]
    ]
    expected_ids = frozenset(
        lightrag_point_id(key, workspace=ws) for key in keys if key
    )
    actual = stored_point_ids(
        client,
        collection=collection,
        workspace=ws,
        file_path_prefix=file_path_prefix,
        full_doc_id=full_doc_id,
    )
    return IndexDrift(
        expected=expected_ids,
        present=expected_ids & actual,
        missing=expected_ids - actual,
        orphaned=actual - expected_ids,
    )
