"""Semantic recall for the memory tiers — Qdrant ANN, never in-Python cosine.

Memory's vector recall (nearest facts / turns) runs through a real
:class:`~aegis.retrieval.vector_store.QdrantVectorStore` — the official qdrant-client
engine — instead of a pgvector ``<=>`` scan or a hand-rolled cosine loop over a Python
list. The durable relational tier stays authoritative: the **Postgres row is the source
of truth** and the Qdrant point only carries the row id (plus tenant/subject scope) in its
payload, so every hit is joined back to its authoritative row before it is returned.

Isolation is enforced twice, on purpose:

* **Qdrant payload filter** — every search is scoped by ``subject_id`` (and ``tenant_id``
  when given), so the ANN engine never even ranks another subject's/tenant's points.
* **SQL re-filter (source of truth)** — the returned point ids are loaded back from the
  authoritative table with the SAME ``subject_id`` / ``tenant_id`` predicates (plus
  ``valid_only`` / ``predicate`` where asked). A stale or mis-scoped point can therefore
  never surface a row: the join drops anything that is not a live, in-scope row *now*.

Because validity (Zep ``invalid_at`` / ``expired_at`` supersession) and predicate matching
are authoritative SQL concerns, they are applied at the SQL join rather than mirrored into
Qdrant — the index only has to answer "which of this subject's rows are nearest", and SQL
decides which of those are still eligible. This keeps the vector index free of the
bitemporal bookkeeping and impossible to desync from the truth.

The embedding of record still lives on the ORM row's ``embedding`` column (written by the
injected embedder on the consolidation/ingest path — unchanged this slice). The column is
no longer *searched*; it is lazily mirrored into the Qdrant collection for the subject on
the recall/reconcile path, and the ANN search runs against Qdrant. One collection is used
per ``(memory kind, embedding dim)`` so a lite 256-dim vector is never compared against a
full-dim one — exactly the dim-skip the old cosine path did, now expressed as collection
routing.

Modes follow the store's contract: ``server`` fails loud if the node is unreachable;
``local`` is the embedded, offline qdrant engine (``:memory:`` for tests, on-disk for dev)
— a real HNSW index, **never** an in-RAM dict fallback. The process-wide default is an
embedded index; a host wires a production node with :func:`set_default_index`.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aegis.retrieval.vector_store import QdrantVectorStore

#: Payload keys carried on every memory point for isolation-scoped filtering.
_SUBJECT_KEY = "subject_id"
_TENANT_KEY = "tenant_id"

#: Prefix for memory collections; the kind + embedding dim are appended.
_COLLECTION_PREFIX = "aegis_mem"


class MemoryVectorIndex:
    """A Qdrant-backed semantic index over the memory ORM rows.

    Wraps a single :class:`~aegis.retrieval.vector_store.QdrantVectorStore`. The store's
    mode (``server`` / embedded ``local``) is chosen at construction and is honest at every
    call site — there is no silent RAM fallback.
    """

    def __init__(
        self, store: QdrantVectorStore, *, collection_prefix: str = _COLLECTION_PREFIX
    ) -> None:
        """Hold the vector store this index mirrors memory rows into and searches."""
        self._store = store
        self._prefix = collection_prefix

    @classmethod
    def local(cls, *, path: str | None = None) -> MemoryVectorIndex:
        """Build an embedded, offline index (on-disk ``path`` or ``:memory:``)."""
        return cls(QdrantVectorStore.local(path=path))

    @classmethod
    def server(
        cls,
        *,
        url: str,
        api_key: str | None = None,
        timeout: float | None = None,
        prefer_grpc: bool = False,
    ) -> MemoryVectorIndex:
        """Build a production index against a live Qdrant node (fail loud if down)."""
        return cls(
            QdrantVectorStore.server(
                url=url, api_key=api_key, timeout=timeout, prefer_grpc=prefer_grpc
            )
        )

    @property
    def store(self) -> QdrantVectorStore:
        """The underlying vector store (for honest logging / isolation proofs)."""
        return self._store

    def _collection(self, table: str, dim: int) -> str:
        """Collection name for a ``(kind, dim)`` — dim routing keeps mixed dims apart."""
        return f"{self._prefix}_{table}_d{dim}"

    async def _sync_subject(
        self,
        session: AsyncSession,
        model: Any,  # noqa: ANN401 - a mapped ORM class with subject_id/embedding
        *,
        collection: str,
        subject_id: str,
        tenant_id: int | None,
        dim: int,
    ) -> None:
        """Mirror this subject's embedded rows into ``collection`` (idempotent upsert).

        Reads the embedding of record from the ORM rows (the durable column) and upserts
        the same-dim ones into Qdrant under the row's own tenant/subject scope. Re-upsert
        is idempotent (deterministic point id per ``(collection, row id)``), so the ANN
        index always reflects the current authoritative rows for the subject.
        """
        stmt = select(model).where(
            model.subject_id == subject_id, model.embedding.is_not(None)
        )
        if tenant_id is not None:
            stmt = stmt.where(model.tenant_id == tenant_id)
        rows = (await session.execute(stmt)).scalars().all()

        ids: list[str] = []
        vectors: list[list[float]] = []
        payloads: list[dict[str, Any]] = []
        for row in rows:
            emb = row.embedding
            if not emb or len(emb) != dim:
                continue  # dim mismatch (e.g. a lite 256-dim vector) → different collection
            ids.append(str(row.id))
            vectors.append(list(emb))
            payloads.append(
                {_SUBJECT_KEY: row.subject_id, _TENANT_KEY: row.tenant_id}
            )
        if not ids:
            return
        self._store.ensure_collection(collection, dim)
        self._store.upsert(collection, ids, vectors, payloads)

    async def search_rows(
        self,
        session: AsyncSession,
        model: Any,  # noqa: ANN401 - a mapped ORM class with subject_id/embedding
        *,
        subject_id: str,
        query_vec: list[float] | None,
        k: int,
        tenant_id: int | None = None,
        valid_only: bool = False,
        predicate: str | None = None,
    ) -> list[tuple[Any, float]]:
        """Return the ``k`` rows nearest ``query_vec`` via Qdrant, joined back to SQL.

        Same contract as the old cosine helper, but the ranking is a real ANN search:

        1. Mirror the subject's embedded rows into the ``(kind, dim)`` collection.
        2. ANN-search Qdrant, scoped by ``subject_id`` (+ ``tenant_id``), over-fetching so
           the authoritative SQL filter below can still yield ``k`` eligible rows.
        3. Load the hit ids back from the authoritative table under the SAME scope plus
           ``valid_only`` / ``predicate`` — the source-of-truth gate. Rows that are gone,
           out of scope, invalidated, expired, or off-predicate are dropped here.

        Returns ``(row, cosine_score)`` pairs, best score first, at most ``k``.
        """
        if not query_vec or k <= 0:
            return []
        dim = len(query_vec)
        collection = self._collection(model.__tablename__, dim)

        await self._sync_subject(
            session,
            model,
            collection=collection,
            subject_id=subject_id,
            tenant_id=tenant_id,
            dim=dim,
        )

        scope: dict[str, Any] = {_SUBJECT_KEY: subject_id}
        if tenant_id is not None:
            scope[_TENANT_KEY] = tenant_id
        # Over-fetch: SQL may drop invalidated/expired/off-predicate hits, so ask Qdrant
        # for more than k and let the authoritative join trim back to the eligible top-k.
        fetch_k = k * 4 + 16 if (valid_only or predicate is not None) else k
        hits = self._store.search(collection, query_vec, fetch_k, filter=scope)
        if not hits:
            return []

        score_by_id: dict[int, float] = {}
        for hit in hits:
            try:
                rid = int(hit.id)
            except (TypeError, ValueError):
                continue
            # First (best) score wins if a point somehow recurs.
            score_by_id.setdefault(rid, hit.score)
        if not score_by_id:
            return []

        # Authoritative re-fetch: the Postgres/SQLite row is the source of truth. Re-apply
        # subject/tenant (defence in depth) and the bitemporal/predicate gates in SQL.
        auth = select(model).where(
            model.id.in_(list(score_by_id)), model.subject_id == subject_id
        )
        if tenant_id is not None:
            auth = auth.where(model.tenant_id == tenant_id)
        if valid_only:
            auth = auth.where(model.invalid_at.is_(None), model.expired_at.is_(None))
        if predicate is not None:
            auth = auth.where(model.predicate == predicate)
        rows_by_id = {r.id: r for r in (await session.execute(auth)).scalars().all()}

        scored = [
            (rows_by_id[rid], score)
            for rid, score in score_by_id.items()
            if rid in rows_by_id
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:k]


# --------------------------------------------------------------------------- default

_DEFAULT_INDEX: MemoryVectorIndex | None = None


def get_default_index() -> MemoryVectorIndex:
    """Return the process-wide memory index, building an embedded one on first use.

    The default is an embedded (``:memory:``) Qdrant engine — offline, real ANN, the
    sanctioned dev/test mode — never a dict. A host swaps in a production node via
    :func:`set_default_index`.
    """
    global _DEFAULT_INDEX
    if _DEFAULT_INDEX is None:
        _DEFAULT_INDEX = MemoryVectorIndex.local()
    return _DEFAULT_INDEX


def set_default_index(index: MemoryVectorIndex | None) -> None:
    """Install the process-wide memory index (e.g. a server-backed one), or clear it."""
    global _DEFAULT_INDEX
    _DEFAULT_INDEX = index


def reset_default_index() -> None:
    """Drop the cached default index so the next use rebuilds a fresh embedded one."""
    global _DEFAULT_INDEX
    _DEFAULT_INDEX = None


async def topk_by_cosine(
    session: AsyncSession,
    model: Any,  # noqa: ANN401 - a mapped ORM class with subject_id/embedding
    *,
    subject_id: str,
    query_vec: list[float] | None,
    k: int,
    tenant_id: int | None = None,
    valid_only: bool = False,
    predicate: str | None = None,
) -> list[tuple[Any, float]]:
    """Top-k semantic recall for ``model``, subject-scoped, via the default Qdrant index.

    Thin seam over :meth:`MemoryVectorIndex.search_rows` on the process-wide index, kept
    at this name/signature so recall + consolidation call sites (and the backend shim) are
    unchanged while the engine underneath is Qdrant, not in-Python cosine.
    """
    if not query_vec or k <= 0:
        return []
    return await get_default_index().search_rows(
        session,
        model,
        subject_id=subject_id,
        query_vec=query_vec,
        k=k,
        tenant_id=tenant_id,
        valid_only=valid_only,
        predicate=predicate,
    )


__all__ = [
    "MemoryVectorIndex",
    "get_default_index",
    "reset_default_index",
    "set_default_index",
    "topk_by_cosine",
]
