"""Memory retention — the horizon past which stored memory is actually deleted.

The rest of this package is built to *keep* things: a fact is superseded rather than
overwritten, the forgetting sweep (:func:`~aegis.memory.consolidate.prune_forgotten`)
soft-archives rather than deletes, and every write leaves a row in
``memory_write_log``. That is the right default for auditability and it has one cost
nothing else in the package pays: **a subject's memory grows without bound**. Two
separate problems wear that one shape —

* **cost** — every turn writes a ``memory_message`` row, and the Chroma mirror carries
  a point for each embedded one, so the store and the index grow linearly with usage
  forever; and
* **privacy** — "we keep every word you ever typed, indefinitely, because nothing was
  ever written that removes it" is not a data-protection posture a tenant can be shown.

So this module is the one place in memory that performs an **unconditional, scheduled
hard delete**, and it is deliberately narrow about what it will remove:

``memory_message`` / ``memory_session``
    Episodic turns older than :attr:`RetentionPolicy.episodic_days`, and the sessions
    that have no turns left and have been idle at least as long. This is the bulk of
    the volume and the most sensitive material — raw conversation.

``memory_fact``
    Only facts that are **already closed** (superseded or invalidated) and have been
    closed for at least :attr:`RetentionPolicy.closed_fact_days`. A currently-valid
    fact is never touched by retention at any age: it is the distilled knowledge the
    whole subsystem exists to hold, and deleting it on a timer would silently
    lobotomise the agent while every screen still read healthy.

``memory_consolidation_job``
    Terminal queue rows past the episodic horizon. Pure bookkeeping.

**``memory_write_log`` is never swept.** It is the append-only "why does the agent
believe X" trail, and a retention policy that quietly erased its own evidence would be
the opposite of the thing it is for. It is small — one row per fact write, not per turn
— and a subject-initiated erasure
(:func:`~aegis.memory.crud.forget_fact` / the host's right-to-erasure endpoint) still
removes it, which is the correct seam for "delete what you hold about me".

**Scope is never implicit.** ``tenant_id=None`` is genuinely ambiguous in this codebase
— it is both "the null-tenant scope" (the recall path's NULL-symmetric predicate) and
"unrestricted" (the HTTP layer's platform-staff filter) — and this module deletes rows,
so the ambiguity is removed by construction. Exactly one of three has to be said:

* ``tenant_id=<id>`` — that tenant's rows;
* ``untenanted=True`` — rows belonging to no tenant (platform staff's own memory);
* ``unrestricted=True`` — every row, which only a resolved platform-wide authority reaches.

Passing none of them raises rather than defaulting to the most destructive reading.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from aegis.memory.stores import (
    ConsolidationStatus,
    MemoryConsolidationJob,
    MemoryFact,
    MemoryMessage,
    MemorySession,
)

__all__ = [
    "RetentionPolicy",
    "RetentionSweep",
    "apply_retention",
    "retention_preview",
]


@dataclass(frozen=True)
class RetentionPolicy:
    """How long each removable tier of memory is kept, in days.

    A non-positive value **disables** that half of the policy — nothing is deleted for
    it, ever. "Off" is a real, sayable state: a deployment under a legal hold wants
    exactly that, and it must be expressible without commenting out a sweeper.

    Attributes:
        episodic_days: Age past which raw turns (and the sessions left empty by their
            removal) are hard-deleted. ``0`` or less keeps them forever.
        closed_fact_days: How long an **already superseded or invalidated** fact is
            retained for the belief timeline before its row goes. ``0`` or less keeps
            them forever. Valid facts are never affected by this number.
    """

    episodic_days: int = 90
    closed_fact_days: int = 30

    @property
    def sweeps_anything(self) -> bool:
        """Whether this policy would delete anything at all."""
        return self.episodic_days > 0 or self.closed_fact_days > 0


@dataclass(frozen=True)
class RetentionSweep:
    """What a retention pass removed — or, in a preview, what it would remove.

    The same shape either way on purpose: an operator's confirmation dialogue and the
    receipt it gets afterwards must be able to disagree only about the tense.
    """

    messages: int = 0
    sessions: int = 0
    facts: int = 0
    jobs: int = 0

    @property
    def total(self) -> int:
        """Every row across the four tiers."""
        return self.messages + self.sessions + self.facts + self.jobs

    def as_dict(self) -> dict[str, int]:
        """A JSON-serialisable mapping for an audit payload or an API response."""
        return {
            "messages": self.messages,
            "sessions": self.sessions,
            "facts": self.facts,
            "jobs": self.jobs,
        }


def _scope_clause(
    model: type[MemoryMessage]
    | type[MemorySession]
    | type[MemoryFact]
    | type[MemoryConsolidationJob],
    *,
    tenant_id: int | None,
    unrestricted: bool,
    untenanted: bool = False,
    subject_id: str | None,
) -> list[ColumnElement[bool]]:
    """Build the tenant + subject predicates for one model, refusing an unstated scope.

    Args:
        model: The mapped memory class being swept.
        tenant_id: The tenant to confine the sweep to.
        unrestricted: Whether the caller has resolved platform-wide authority. The only
            way to reach a sweep that crosses tenants.
        untenanted: Confine to rows belonging to no tenant at all.
        subject_id: An optional single subject to confine the sweep to.

    Returns:
        The ``WHERE`` terms to AND into the statement.

    Raises:
        ValueError: If no scope was stated at all. A delete with no stated scope is the
            one thing this module must not guess.
    """
    if tenant_id is None and not unrestricted and not untenanted:
        raise ValueError(
            "retention needs a scope: pass tenant_id=<id> for one tenant, "
            "untenanted=True for the rows that belong to none, or unrestricted=True "
            "for a platform-wide sweep. Deleting under an unstated scope is not a "
            "default this function will assume."
        )
    terms: list[ColumnElement[bool]] = []
    if tenant_id is not None:
        terms.append(model.tenant_id == tenant_id)
    elif untenanted:
        terms.append(model.tenant_id.is_(None))
    if subject_id is not None:
        terms.append(model.subject_id == subject_id)
    return terms


def _cutoff(days: int, *, now: datetime) -> datetime:
    """The instant a row must predate to be eligible under a ``days`` horizon."""
    return now - timedelta(days=days)


def _empty_session_ids(  # noqa: ANN202 - a SQLAlchemy Select, typed by the ORM
    *, terms: list[ColumnElement[bool]], cutoff: datetime
):
    """Select idle sessions that have no surviving messages.

    Ordering matters and is the reason this is a separate select rather than a join in
    the delete: the messages are removed first, so "has no messages" is evaluated
    against the post-delete state and a session whose entire transcript just aged out
    goes with it. A session still holding one in-horizon turn survives, summary intact.
    """
    return (
        select(MemorySession.id)
        .where(*terms, MemorySession.last_active_at < cutoff)
        .where(
            ~select(MemoryMessage.id)
            .where(MemoryMessage.session_id == MemorySession.id)
            .exists()
        )
    )


async def _count(session: AsyncSession, stmt) -> int:  # noqa: ANN001 - a SQLAlchemy Select
    """Run a ``SELECT count(*)`` over ``stmt``'s row set."""
    return int(
        (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    )


async def retention_preview(
    session: AsyncSession,
    *,
    policy: RetentionPolicy,
    tenant_id: int | None = None,
    unrestricted: bool = False,
    untenanted: bool = False,
    subject_id: str | None = None,
    now: datetime | None = None,
) -> RetentionSweep:
    """Count what :func:`apply_retention` would remove, without removing it.

    The number an operator is shown *before* they confirm. It is deliberately computed
    from the same predicates the delete uses, so the two cannot describe different row
    sets — with the one honest exception documented on :func:`_empty_session_ids`: the
    session count here is measured against the messages that still exist, so it is a
    **lower bound** (a session whose last turns are about to age out is not yet empty).
    Under-promising is the safe direction for a confirmation dialogue.

    Args:
        session: Async DB session.
        policy: The horizons to measure against.
        tenant_id: Confine to one tenant.
        unrestricted: Platform-wide authority (see :func:`_scope_clause`).
        untenanted: Confine to the rows that belong to no tenant.
        subject_id: Confine to one memory subject.
        now: Injectable clock for tests.

    Returns:
        The per-tier counts.
    """
    now = now or datetime.now(UTC)
    counts = {"messages": 0, "sessions": 0, "facts": 0, "jobs": 0}

    if policy.episodic_days > 0:
        cutoff = _cutoff(policy.episodic_days, now=now)
        msg_terms = _scope_clause(
            MemoryMessage,
            tenant_id=tenant_id,
            unrestricted=unrestricted,
            untenanted=untenanted,
            subject_id=subject_id,
        )
        counts["messages"] = await _count(
            session, select(MemoryMessage.id).where(*msg_terms, MemoryMessage.created_at < cutoff)
        )
        sess_terms = _scope_clause(
            MemorySession,
            tenant_id=tenant_id,
            unrestricted=unrestricted,
            untenanted=untenanted,
            subject_id=subject_id,
        )
        counts["sessions"] = await _count(
            session, _empty_session_ids(terms=sess_terms, cutoff=cutoff)
        )
        job_terms = _scope_clause(
            MemoryConsolidationJob,
            tenant_id=tenant_id,
            unrestricted=unrestricted,
            untenanted=untenanted,
            subject_id=subject_id,
        )
        counts["jobs"] = await _count(
            session,
            select(MemoryConsolidationJob.id).where(
                *job_terms,
                MemoryConsolidationJob.created_at < cutoff,
                MemoryConsolidationJob.status.in_(
                    (ConsolidationStatus.DONE, ConsolidationStatus.ERROR)
                ),
            ),
        )

    if policy.closed_fact_days > 0:
        fact_cutoff = _cutoff(policy.closed_fact_days, now=now)
        fact_terms = _scope_clause(
            MemoryFact,
            tenant_id=tenant_id,
            unrestricted=unrestricted,
            untenanted=untenanted,
            subject_id=subject_id,
        )
        counts["facts"] = await _count(
            session,
            select(MemoryFact.id).where(*fact_terms, _closed_before(fact_cutoff)),
        )

    return RetentionSweep(**counts)


def _closed_before(cutoff: datetime) -> ColumnElement[bool]:
    """A fact closed — superseded or invalidated — before ``cutoff``.

    Both bitemporal axes count, and either alone is enough: ``expired_at`` is set by a
    refinement (transaction time) and ``invalid_at`` by a contradiction (world time),
    and a fact carrying either has already left hot recall. A fact carrying neither is
    live and is never eligible, which is the single most important line in this module.
    """
    return or_(
        MemoryFact.expired_at.is_not(None) & (MemoryFact.expired_at < cutoff),
        MemoryFact.invalid_at.is_not(None) & (MemoryFact.invalid_at < cutoff),
    )


async def apply_retention(
    session: AsyncSession,
    *,
    policy: RetentionPolicy,
    tenant_id: int | None = None,
    unrestricted: bool = False,
    untenanted: bool = False,
    subject_id: str | None = None,
    now: datetime | None = None,
) -> RetentionSweep:
    """Hard-delete every memory row past its horizon and report what went.

    Does **not** commit — the caller owns the transaction, and (for the fact half) the
    subject's :class:`~aegis.memory.cache.MemorySemanticCache` must be invalidated after
    the commit, exactly as the forget path does.

    Args:
        session: Async DB session.
        policy: The horizons to enforce. A policy that sweeps nothing is a no-op that
            still returns a zero-filled result rather than raising.
        tenant_id: Confine to one tenant.
        unrestricted: Platform-wide authority (see :func:`_scope_clause`).
        untenanted: Confine to the rows that belong to no tenant.
        subject_id: Confine to one memory subject.
        now: Injectable clock for tests.

    Returns:
        The per-tier counts actually deleted.

    Raises:
        ValueError: If the scope was left unstated (see :func:`_scope_clause`).
    """
    now = now or datetime.now(UTC)
    counts = {"messages": 0, "sessions": 0, "facts": 0, "jobs": 0}

    if policy.episodic_days > 0:
        cutoff = _cutoff(policy.episodic_days, now=now)
        msg_terms = _scope_clause(
            MemoryMessage,
            tenant_id=tenant_id,
            unrestricted=unrestricted,
            untenanted=untenanted,
            subject_id=subject_id,
        )
        res = await session.execute(
            delete(MemoryMessage).where(*msg_terms, MemoryMessage.created_at < cutoff)
        )
        counts["messages"] = int(res.rowcount or 0)

        # After the turns, so "empty" means empty *now* rather than before the sweep.
        sess_terms = _scope_clause(
            MemorySession,
            tenant_id=tenant_id,
            unrestricted=unrestricted,
            untenanted=untenanted,
            subject_id=subject_id,
        )
        doomed = list(
            (
                await session.execute(_empty_session_ids(terms=sess_terms, cutoff=cutoff))
            ).scalars()
        )
        if doomed:
            res = await session.execute(
                delete(MemorySession).where(MemorySession.id.in_(doomed))
            )
            counts["sessions"] = int(res.rowcount or 0)

        job_terms = _scope_clause(
            MemoryConsolidationJob,
            tenant_id=tenant_id,
            unrestricted=unrestricted,
            untenanted=untenanted,
            subject_id=subject_id,
        )
        res = await session.execute(
            delete(MemoryConsolidationJob).where(
                *job_terms,
                MemoryConsolidationJob.created_at < cutoff,
                MemoryConsolidationJob.status.in_(
                    (ConsolidationStatus.DONE, ConsolidationStatus.ERROR)
                ),
            )
        )
        counts["jobs"] = int(res.rowcount or 0)

    if policy.closed_fact_days > 0:
        fact_cutoff = _cutoff(policy.closed_fact_days, now=now)
        fact_terms = _scope_clause(
            MemoryFact,
            tenant_id=tenant_id,
            unrestricted=unrestricted,
            untenanted=untenanted,
            subject_id=subject_id,
        )
        res = await session.execute(
            delete(MemoryFact).where(*fact_terms, _closed_before(fact_cutoff))
        )
        counts["facts"] = int(res.rowcount or 0)

    return RetentionSweep(**counts)
