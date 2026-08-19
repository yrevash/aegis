"""Semantic recall for the memory tiers — Chroma ANN, never in-Python cosine.

Memory's vector recall (nearest facts / turns) runs through a real
:class:`~aegis.retrieval.vector_store.ChromaVectorStore` — the official chromadb engine —
instead of a pgvector ``<=>`` scan or a hand-rolled cosine loop over a Python list. The
durable relational tier stays authoritative: the **Postgres row is the source of truth**
and the Chroma point only carries the row id (plus tenant/subject scope) in its metadata,
so every hit is joined back to its authoritative row before it is returned.

Isolation is enforced twice, on purpose:

* **Chroma metadata filter** — every search is scoped by ``subject_id`` **and**
  ``tenant_id``, so the ANN engine never even ranks another subject's/tenant's points.
  The tenant condition is NULL-symmetric: ``tenant_id=None`` is the *null-tenant scope*,
  matching null-tenant points only, never a wildcard over every tenant. (The store
  encodes that ``None`` as an explicit sentinel, because Chroma would otherwise drop the
  key entirely and turn the condition into "no condition".)
* **SQL re-filter (source of truth)** — the returned point ids are loaded back from the
  authoritative table with the SAME ``subject_id`` / ``tenant_id`` predicates (plus
  ``valid_only`` / ``predicate`` where asked). A stale or mis-scoped point can therefore
  never surface a row: the join drops anything that is not a live, in-scope row *now*.

Because validity (Zep ``invalid_at`` / ``expired_at`` supersession) and predicate matching
are authoritative SQL concerns, they are applied at the SQL join rather than mirrored into
Chroma — the index only has to answer "which of this subject's rows are nearest", and SQL
decides which of those are still eligible. This keeps the vector index free of the
bitemporal bookkeeping and impossible to desync from the truth.

The embedding of record still lives on the ORM row's ``embedding`` column (written by the
injected embedder on the consolidation/ingest path). The column is no longer *searched*;
it is **incrementally** mirrored into the Chroma collection for the subject on the
recall/reconcile path, and the ANN search runs against Chroma. One collection is used per
``(memory kind, embedding dim)`` so a lite 256-dim vector is never compared against a
full-dim one — exactly the dim-skip the old cosine path did, now expressed as collection
routing.

The mirror is *incremental*, not a full rescan: memory rows are append-only (a fact is
never re-embedded in place — a refinement or contradiction inserts a superseding row), so
a per-scope high-water mark on the primary key is a sound sync cursor. Only rows newer
than the mark are read and upserted, which is what makes the ANN path cheaper than the
cosine loop it replaced instead of strictly worse than it.

Every chromadb call is synchronous (an HTTP round-trip in server mode, a blocking native
call in embedded mode), so each one is run in a worker thread — the recall path is
``async`` and must never block the event loop.

Modes follow the store's contract: ``server`` fails loud if the node is unreachable;
``local`` is the embedded, offline chroma engine (``:memory:`` for tests, on-disk for dev)
— a real HNSW index that needs **no server binary at all**, and **never** an in-RAM dict
fallback. There is **no implicit process-wide default**: a host installs the index it
wants with :func:`set_default_index` (the backend does exactly that at startup — the
on-disk store for a real deployment, the explicit ephemeral one for dev), and recall
raises until it does rather than conjuring a non-durable index nobody asked for.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aegis.retrieval.vector_store import (
    ChromaVectorStore,
    VectorStoreNotConfiguredError,
)

#: Payload keys carried on every memory point for isolation-scoped filtering.
_SUBJECT_KEY = "subject_id"
_TENANT_KEY = "tenant_id"

#: Prefix for memory collections; the kind + embedding dim are appended.
_COLLECTION_PREFIX = "aegis_mem"


class MemoryVectorIndex:
    """A Chroma-backed semantic index over the memory ORM rows.

    Wraps a single :class:`~aegis.retrieval.vector_store.ChromaVectorStore`. The store's
    mode (``server`` / embedded ``local``) is chosen at construction and is honest at every
    call site — there is no silent RAM fallback.
    """

    def __init__(
        self, store: ChromaVectorStore, *, collection_prefix: str = _COLLECTION_PREFIX
    ) -> None:
        """Hold the vector store this index mirrors memory rows into and searches."""
        self._store = store
        self._prefix = collection_prefix
        #: Sync high-water marks: ``(collection, subject_id, tenant tag) -> max row id
        #: already mirrored``. Bounded by the number of live scopes, holds ints only.
        self._synced_to: dict[tuple[str, str, str], int] = {}

    @classmethod
    def local(cls, *, path: str | None = None) -> MemoryVectorIndex:
        """Build an embedded, offline index (on-disk ``path`` or ``:memory:``)."""
        return cls(ChromaVectorStore.local(path=path))

    @classmethod
    def server(
        cls,
        *,
        url: str,
        api_key: str | None = None,
        timeout: float | None = None,
    ) -> MemoryVectorIndex:
        """Build an index against a live Chroma server (fail loud if it is down)."""
        return cls(
            ChromaVectorStore.server(url=url, api_key=api_key, timeout=timeout)
        )

    @property
    def store(self) -> ChromaVectorStore:
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
        """Mirror this scope's **newly-added** embedded rows into ``collection``.

        Reads the embedding of record from the ORM rows (the durable column) and upserts
        the same-dim ones into Chroma under the row's own tenant/subject scope. Re-upsert
        is idempotent: the row id **is** the collection-scoped point id, so a repeat
        replaces in place rather than duplicating.

        The scan is bounded by a per-scope high-water mark on the primary key rather than
        re-reading the whole subject on every query. That is sound because the memory
        tables are **append-only in the embedding**: a fact is never re-embedded in place
        (a refinement or contradiction inserts a superseding row, and invalidation only
        writes the bitemporal columns), and messages are immutable once written. Ids are
        monotonic, so rows a *different* process wrote after this one's last sync still sit
        above the mark and are picked up on the next call.

        Without the mark this ran a full ``SELECT`` + full re-upsert of the subject per
        ANN query — for an 8-candidate consolidation, eight full scans and eight full
        re-index passes — which made the "real index" strictly more expensive than the
        in-Python cosine loop it replaced.
        """
        key = (collection, subject_id, "-" if tenant_id is None else str(tenant_id))
        watermark = self._synced_to.get(key, 0)

        stmt = select(model).where(
            model.subject_id == subject_id,
            model.embedding.is_not(None),
            model.id > watermark,
        )
        # Null-safe tenant scoping, symmetric with the authoritative SQL join below: the
        # null tenant is its own scope, never a wildcard over every tenant's rows.
        stmt = stmt.where(
            model.tenant_id == tenant_id
            if tenant_id is not None
            else model.tenant_id.is_(None)
        )
        rows = (await session.execute(stmt)).scalars().all()
        if not rows:
            return

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
        if ids:
            # chromadb is synchronous; keep the round-trips off the event loop.
            await asyncio.to_thread(self._store.ensure_collection, collection, dim)
            await asyncio.to_thread(self._store.upsert, collection, ids, vectors, payloads)
        # Advance past every row *considered*, including the dim-mismatched ones that
        # belong to another collection — otherwise they would be re-read forever.
        self._synced_to[key] = max(row.id for row in rows)

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
        """Return the ``k`` rows nearest ``query_vec`` via Chroma, joined back to SQL.

        Same contract as the old cosine helper, but the ranking is a real ANN search:

        1. Mirror the scope's rows added since the last sync into the ``(kind, dim)``
           collection (incremental — see :meth:`_sync_subject`).
        2. ANN-search Chroma, scoped by ``subject_id`` **and** ``tenant_id``, over-fetching
           so the authoritative SQL filter below can still yield ``k`` eligible rows.
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

        # Chroma metadata pre-filter, NULL-symmetric on tenant. ``tenant_id`` is ALWAYS
        # part of the scope — including when it is ``None``, which means the *null-tenant*
        # scope and matches null-tenant points only. Dropping the key for a null tenant
        # (as the Qdrant path had to, lacking a matchable null) would turn the condition
        # into "any tenant" and hand a colliding subject id another tenant's points; the
        # store encodes ``None`` as an explicit sentinel precisely so this stays exact.
        # SQL still re-gates below — defence in depth, not a substitute.
        scope: dict[str, Any] = {_SUBJECT_KEY: subject_id, _TENANT_KEY: tenant_id}
        # Over-fetch: SQL may drop invalidated/expired/off-predicate hits, so ask Chroma
        # for more than k and let the authoritative join trim back to the eligible top-k.
        fetch_k = k * 4 + 16 if (valid_only or predicate is not None) else k
        # Synchronous chromadb call → off the event loop (a server-mode search is a
        # network round-trip and this runs on the hot recall path).
        hits = await asyncio.to_thread(
            self._store.search, collection, query_vec, fetch_k, filter=scope
        )
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
        # Null-safe tenant gate: ``tenant_id=None`` is the *null-tenant* scope, not "any
        # tenant". A bare ``if tenant_id is not None`` would let an unscoped recall return
        # a tenant's row for a colliding subject id and inject it verbatim into a prompt.
        auth = auth.where(
            model.tenant_id == tenant_id
            if tenant_id is not None
            else model.tenant_id.is_(None)
        )
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
        # Deterministic order, engine-independent. Score decides; an exact tie (two rows
        # carrying the *same* embedding is routine in memory — a restated preference, a
        # duplicated message) is broken by descending row id, i.e. most recent first.
        # Without this the winner is whatever order the ANN index happened to emit, which
        # differs between engines and even between builds of one engine — and consolidation
        # reads ``neighbors[0]`` to decide dedup-vs-reconcile, so an arbitrary tie-break is
        # an arbitrary write decision.
        scored.sort(key=lambda pair: (pair[1], pair[0].id), reverse=True)
        return scored[:k]


# --------------------------------------------------------------------------- default

_DEFAULT_INDEX: MemoryVectorIndex | None = None


def get_default_index() -> MemoryVectorIndex:
    """Return the process-wide memory index, or fail loud if none was configured.

    There is no lazily-built default any more (§8.4). Until this raised, a host that
    forgot :func:`set_default_index` got an ephemeral ``:memory:`` Chroma engine on
    first recall: memory appeared to work, every fact it "remembered" was lost on
    restart, and nothing in the logs or on the wire said so. A missing step now names
    itself, exactly as ``aegis.governance``'s ``configure_audit`` already did.

    Returns:
        The :class:`MemoryVectorIndex` installed by :func:`set_default_index`.

    Raises:
        VectorStoreNotConfiguredError: If no index was installed. The message carries
            both honest choices — the durable on-disk index and the explicit ephemeral
            one — so the fix is in the failure rather than in a document.
    """
    if _DEFAULT_INDEX is None:
        raise VectorStoreNotConfiguredError(
            "aegis.memory has no vector index configured, so semantic recall has "
            "nowhere to search. Call "
            "aegis.memory.set_default_index(MemoryVectorIndex.local(path=...)) at "
            "startup for a DURABLE on-disk index, or "
            "set_default_index(MemoryVectorIndex.local()) to choose the EPHEMERAL "
            "in-process engine explicitly (dev/tests). It used to build the ephemeral "
            "one for you on first use, so a forgotten call looked exactly like working "
            "memory until the process restarted and every recalled fact was gone."
        )
    return _DEFAULT_INDEX


def set_default_index(index: MemoryVectorIndex | None) -> None:
    """Install the process-wide memory index (e.g. a server-backed one), or clear it."""
    global _DEFAULT_INDEX
    _DEFAULT_INDEX = index


def reset_default_index() -> None:
    """Clear the installed index (test teardown); the next use raises again."""
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
    """Top-k semantic recall for ``model``, subject-scoped, via the default Chroma index.

    Thin seam over :meth:`MemoryVectorIndex.search_rows` on the process-wide index, kept
    at this name/signature so recall + consolidation call sites (and the backend shim) are
    unchanged while the engine underneath is Chroma, not in-Python cosine.
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
    "VectorStoreNotConfiguredError",
    "get_default_index",
    "reset_default_index",
    "set_default_index",
    "topk_by_cosine",
]
