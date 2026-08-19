"""Explicit memory CRUD — list, read, write, correct and forget a subject's facts.

The consolidation write path (:mod:`aegis.memory.consolidate`) owns the *automatic*
lifecycle (ADD/UPDATE/INVALIDATE/PRUNE). This module adds the *explicit*, operator- or
subject-initiated operations a UI needs: enumerate a subject's durable facts, write one
by hand, correct one that is wrong, and forget one (right-to-be-forgotten). Every
operation is subject+tenant scoped (the NULL-safe app-level isolator; RLS is never
relied upon) and audited in ``memory_write_log`` under the matching op — the same table
``GET /memory/writes`` reads, so a hand-written fact appears in the changelog beside
every model-decided one instead of arriving through a side door.

**A hand-written fact is untrusted text.** It is stored now and injected into a future
prompt as trusted context, which makes an unscreened write a stored prompt injection
with a delay fuse. Screening is the *host's* job and happens before these functions are
reached (the API runs ``check_input`` and passes the screened string), because the rail
stack is a host concern and this package is LLM-agnostic — but the requirement is
written here too, because this is the door.

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
from aegis.memory.recall import _tenant_clause
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
    # NULL-symmetric tenant predicate, shared with the recall path. The earlier
    # ``if tenant_id is not None`` form silently degraded an unscoped call to "any
    # tenant" — it reads as defensive coding and behaves as a cross-tenant read.
    stmt = select(MemoryFact).where(
        MemoryFact.subject_id == subject_id,
        _tenant_clause(MemoryFact, tenant_id),
    )
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
        MemoryFact.id == fact_id,
        MemoryFact.subject_id == subject_id,
        _tenant_clause(MemoryFact, tenant_id),
    )
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


async def add_fact(
    session: AsyncSession,
    *,
    subject_id: str,
    tenant_id: int | None = None,
    text: str,
    fact_type: str = "entity_attr",
    subject: str = "",
    predicate: str = "",
    object_: str = "",
    confidence: float = 1.0,
    importance: int = 5,
    embedding: list[float] | None = None,
    actor: str | None = None,
    reason: str | None = None,
) -> MemoryFact:
    """Write one durable fact by hand and audit it as an ``ADD``.

    The explicit counterpart to the extractor: same table, same bitemporal columns,
    same write-log op, so nothing downstream (recall, the belief timeline, the
    changelog) can tell the difference except by reading ``model`` on the audit row —
    which is exactly the distinction that should be visible, and the only one.

    ``confidence`` defaults to ``1.0``: a person stating a fact about themselves or
    their own tenant is not a model guessing. ``embedding`` is optional and its absence
    is honest degradation rather than an error — a fact with no vector is still found by
    the recency-only recall arm, and the Chroma mirror picks up any row that has one.

    Does **not** commit; the caller owns the transaction and the subsequent
    :class:`~aegis.memory.cache.MemorySemanticCache` invalidation for the subject.

    Args:
        session: Async DB session.
        subject_id: The memory subject (app-level isolation key; required).
        tenant_id: The tenant the row belongs to.
        text: The natural-language rendering — **already screened by the host's input
            rail**. This is the string that gets injected into a future prompt.
        fact_type: One of the domain's fact types.
        subject: Canonical entity the fact is about.
        predicate: The relation.
        object_: The value. Named with a trailing underscore so it does not shadow the
            builtin; the column is ``object``.
        confidence: Confidence in [0, 1].
        importance: Poignancy 1..10.
        embedding: Optional recall vector for the text.
        actor: Who wrote it, recorded on the audit row (e.g. ``"operator:alice"``).
        reason: Optional human reason recorded on the audit row.

    Returns:
        The persisted :class:`~aegis.memory.stores.MemoryFact` (flushed, so ``id`` is
        populated).
    """
    fact = MemoryFact(
        subject_id=subject_id,
        tenant_id=tenant_id,
        fact_type=fact_type,
        subject=subject,
        predicate=predicate,
        object=object_,
        text=text,
        embedding=embedding,
        confidence=confidence,
        importance=importance,
        source_turn_ids=[],
    )
    session.add(fact)
    await session.flush()

    _write_log(
        session,
        subject_id=subject_id,
        tenant_id=tenant_id,
        op=WriteOp.ADD,
        fact_id=fact.id,
        before={},
        after=_fact_snapshot(fact),
        reason=reason or "written by hand from the memory control plane",
        trace_id=None,
        model=actor,
    )
    return fact


async def correct_fact(
    session: AsyncSession,
    *,
    fact_id: int,
    subject_id: str,
    tenant_id: int | None = None,
    text: str,
    predicate: str | None = None,
    object_: str | None = None,
    importance: int | None = None,
    embedding: list[float] | None = None,
    actor: str | None = None,
    reason: str | None = None,
) -> tuple[MemoryFact, MemoryFact] | None:
    """Correct a fact by **superseding** it, and audit the pair as an ``UPDATE``.

    A correction is not an in-place edit, and that is a deliberate refusal rather than
    an implementation detail. These rows are bitemporal: ``expired_at`` +
    ``supersedes_id`` is how a refinement is recorded everywhere else in this package,
    and overwriting ``text`` on the original row would erase the answer to "what did it
    believe last week, and who changed it" — the exact question the write log exists to
    answer. The old row leaves hot recall the moment it is closed, so the corrected
    value is the only one a run can see.

    Does **not** commit; the caller owns the transaction and the cache invalidation.

    Args:
        session: Async DB session.
        fact_id: The fact to correct.
        subject_id: The owning subject (isolation guard; a mismatch returns ``None``).
        tenant_id: Optional tenant scope.
        text: The corrected rendering — **already screened by the host's input rail**.
        predicate: New relation, or ``None`` to carry the old one forward.
        object_: New value, or ``None`` to carry the old one forward.
        importance: New poignancy, or ``None`` to carry the old one forward.
        embedding: Optional recall vector for the corrected text.
        actor: Who corrected it, recorded on the audit row.
        reason: Optional human reason recorded on the audit row.

    Returns:
        ``(old, new)`` — the closed row and its replacement — or ``None`` when no such
        fact exists under this subject/tenant.
    """
    old = await get_fact(
        session, fact_id=fact_id, subject_id=subject_id, tenant_id=tenant_id
    )
    if old is None:
        return None

    before = _fact_snapshot(old)
    now = datetime.now(UTC)
    new = MemoryFact(
        subject_id=subject_id,
        tenant_id=tenant_id,
        fact_type=old.fact_type,
        subject=old.subject,
        predicate=old.predicate if predicate is None else predicate,
        object=old.object if object_ is None else object_,
        text=text,
        embedding=embedding,
        confidence=old.confidence,
        importance=old.importance if importance is None else importance,
        source_turn_ids=list(old.source_turn_ids or []),
        supersedes_id=old.id,
    )
    old.expired_at = now
    session.add(new)
    await session.flush()

    _write_log(
        session,
        subject_id=subject_id,
        tenant_id=tenant_id,
        op=WriteOp.UPDATE,
        fact_id=new.id,
        before=before,
        after=_fact_snapshot(new),
        reason=reason or "corrected by hand from the memory control plane",
        trace_id=None,
        model=actor,
    )
    return old, new


__all__ = ["add_fact", "correct_fact", "forget_fact", "get_fact", "list_facts"]
