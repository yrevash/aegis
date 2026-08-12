"""Explicit memory CRUD — list, read, and subject-initiated forget/delete of facts.

The consolidation write path (:mod:`aegis.memory.consolidate`) owns the *automatic*
lifecycle (ADD/UPDATE/INVALIDATE/PRUNE). This module adds the *explicit*, operator- or
subject-initiated operations a UI needs: enumerate a subject's durable facts and forget a
specific one (right-to-be-forgotten). Every operation is subject+tenant scoped (the
NULL-safe app-level isolator; RLS is never relied upon) and — for the forget path —
audited in ``memory_write_log`` under the ``DELETE`` op.

Forget is **soft by default**: like a supersession it closes the fact in transaction-time
(``expired_at``) and world-time (``invalid_at``) so it drops out of hot recall but the row
survives for audit and the belief timeline. Pass ``hard=True`` only for a genuine erasure
(e.g. a data-subject deletion request), which removes the row entirely.

The durable SQL rows are authoritative; the derived :class:`~aegis.memory.cache.\
MemorySemanticCache` must be invalidated for the subject after any forget — the streamed
``stream_forget`` facade does exactly that.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aegis.memory.consolidate import _fact_snapshot, _write_log
from aegis.memory.stores import MemoryFact, WriteOp


async def list_facts(
    session: AsyncSession,
    *,
    subject_id: str,
    tenant_id: int | None = None,
    valid_only: bool = True,
    limit: int = 100,
) -> list[MemoryFact]:
    """Return a subject's durable facts, newest valid first (subject+tenant scoped).

    Args:
        session: Async DB session.
        subject_id: The memory subject (app-level isolation key; required).
        tenant_id: Optional tenant scope.
        valid_only: When ``True`` (default), only currently-valid facts
            (``invalid_at IS NULL AND expired_at IS NULL``); when ``False``, the full
            bitemporal history.
        limit: Maximum rows to return.

    Returns:
        The matching :class:`~aegis.memory.stores.MemoryFact` rows.
    """
    stmt = select(MemoryFact).where(MemoryFact.subject_id == subject_id)
    if tenant_id is not None:
        stmt = stmt.where(MemoryFact.tenant_id == tenant_id)
    if valid_only:
        stmt = stmt.where(MemoryFact.invalid_at.is_(None), MemoryFact.expired_at.is_(None))
    stmt = stmt.order_by(MemoryFact.valid_at.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def get_fact(
    session: AsyncSession,
    *,
    fact_id: int,
    subject_id: str,
    tenant_id: int | None = None,
) -> MemoryFact | None:
    """Return one fact by id, only if it belongs to ``subject_id`` (+ ``tenant_id``)."""
    stmt = select(MemoryFact).where(
        MemoryFact.id == fact_id, MemoryFact.subject_id == subject_id
    )
    if tenant_id is not None:
        stmt = stmt.where(MemoryFact.tenant_id == tenant_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def forget_fact(
    session: AsyncSession,
    *,
    fact_id: int,
    subject_id: str,
    tenant_id: int | None = None,
    reason: str | None = None,
    trace_id: str | None = None,
    hard: bool = False,
) -> MemoryFact | None:
    """Forget one durable fact (subject+tenant scoped), auditing a ``DELETE`` write.

    Soft by default: closes the fact in both time axes (``invalid_at`` + ``expired_at``)
    so it leaves hot recall while the row is retained for audit. ``hard=True`` deletes the
    row outright (data-subject erasure).

    Does **not** commit — the caller (or the streamed facade) owns the transaction and the
    subsequent cache invalidation.

    Args:
        session: Async DB session.
        fact_id: The fact to forget.
        subject_id: The owning subject (isolation guard; a mismatch returns ``None``).
        tenant_id: Optional tenant scope.
        reason: Optional human reason recorded on the audit row.
        trace_id: Optional trace id recorded on the audit row.
        hard: When ``True``, hard-delete the row instead of soft-closing it.

    Returns:
        The forgotten fact (its in-memory snapshot), or ``None`` if it was not found under
        this subject/tenant.
    """
    fact = await get_fact(
        session, fact_id=fact_id, subject_id=subject_id, tenant_id=tenant_id
    )
    if fact is None:
        return None

    before = _fact_snapshot(fact)
    now = datetime.now(UTC)
    if hard:
        await session.execute(sa_delete(MemoryFact).where(MemoryFact.id == fact.id))
        after: dict = {}
    else:
        if fact.invalid_at is None:
            fact.invalid_at = now
        fact.expired_at = now
        await session.flush()
        after = _fact_snapshot(fact)

    _write_log(
        session,
        subject_id=subject_id,
        tenant_id=tenant_id,
        op=WriteOp.DELETE,
        fact_id=fact_id,
        before=before,
        after=after,
        reason=reason or ("hard delete (erasure)" if hard else "explicit forget"),
        trace_id=trace_id,
    )
    return fact


__all__ = ["forget_fact", "get_fact", "list_facts"]
