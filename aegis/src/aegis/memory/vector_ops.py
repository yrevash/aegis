"""Dialect-agnostic top-k cosine search over a subject-bounded candidate set.

The critic's blocker: pgvector's ``<=>`` operator does not exist on the SQLite test DB,
so a single pgvector query returns nothing under test. The portable, verified approach
used here — on BOTH dialects — is to **filter to the subject's candidate rows in SQL**
(cheap, indexed on ``subject_id``, plus the valid-only / predicate predicates) and then
compute cosine **in Python** over that bounded set via
:func:`aegis.retrieval.vectors.cosine_similarity`. Because the set is always scoped to one
``subject_id`` (and, for facts, to currently-valid rows), it is small — never a
full-table scan — so this is correct and fast at enterprise-per-user scale.

Future optimization (needs a Postgres test env + a ``VectorType`` comparator exposing
``cosine_distance``): on ``postgresql`` swap the Python sort for
``ORDER BY embedding <=> :qvec LIMIT k`` against an HNSW/IVFFlat index. The function
signature below is the seam for that; callers do not change.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aegis.retrieval.vectors import cosine_similarity


async def topk_by_cosine(
    session: AsyncSession,
    model: Any,  # noqa: ANN401 - a mapped ORM class with .subject_id/.embedding
    *,
    subject_id: str,
    query_vec: list[float] | None,
    k: int,
    tenant_id: int | None = None,
    valid_only: bool = False,
    predicate: str | None = None,
) -> list[tuple[Any, float]]:
    """Return the ``k`` rows most cosine-similar to ``query_vec``, subject-scoped.

    App-level isolation is enforced here: the query ALWAYS filters ``subject_id`` (and
    ``tenant_id`` when given) — the primary, NULL-safe isolator that does not depend on
    Postgres RLS.

    Args:
        session: The async DB session.
        model: The mapped memory model (``MemoryFact`` / ``MemoryMessage``) — must have
            ``subject_id`` and ``embedding`` columns.
        subject_id: The memory subject to scope to (required).
        query_vec: The query embedding; ``None``/empty → returns ``[]`` (caller may fall
            back to recency-only recall).
        k: Max rows to return (``<= 0`` → ``[]``).
        tenant_id: Optional tenant scope (added to the WHERE when not ``None``).
        valid_only: Restrict to currently-valid facts (``invalid_at IS NULL AND
            expired_at IS NULL``); requires those columns on ``model``.
        predicate: Optional exact ``predicate`` match (narrows the fact neighborhood).

    Returns:
        ``(row, cosine_similarity)`` pairs, highest similarity first, at most ``k``.
    """
    if not query_vec or k <= 0:
        return []

    stmt = select(model).where(model.subject_id == subject_id)
    if tenant_id is not None:
        stmt = stmt.where(model.tenant_id == tenant_id)
    stmt = stmt.where(model.embedding.is_not(None))
    if valid_only:
        stmt = stmt.where(model.invalid_at.is_(None), model.expired_at.is_(None))
    if predicate is not None:
        stmt = stmt.where(model.predicate == predicate)

    rows = (await session.execute(stmt)).scalars().all()
    scored: list[tuple[Any, float]] = []
    for row in rows:
        emb = row.embedding
        if not emb or len(emb) != len(query_vec):
            continue  # dim mismatch (e.g. a lite 256-dim vector) → skip, never crash
        scored.append((row, cosine_similarity(query_vec, emb)))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:k]


__all__ = ["topk_by_cosine"]
