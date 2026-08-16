"""Durable approvals-inbox data layer — the source of truth for a paused run (§1.3).

A run that trips the human gate does not live in RAM: it is a persisted
:class:`~app.data.models.Approval` row (``PENDING``) plus a LangGraph checkpoint
keyed by ``thread_id == run_id``. That makes the pause survive a restart and lets
an admin resolve it out-of-band from the inbox, decoupled from the SSE socket.

This module owns the typed CRUD over that table:

- :func:`enqueue_approval` — INSERT a ``PENDING`` row when the gate fires.
- :func:`list_pending` — the admin inbox query (``ORDER BY sla_deadline``), with a
  ``FOR UPDATE SKIP LOCKED`` claim path so many workers never double-process a row.
- :func:`resolve_approval` — an **optimistic** ``PENDING → RESUMING/REJECTED``
  transition; only the winning caller (rowcount 1) proceeds, so a double-decision
  can never double-resume (idempotency).
- :func:`sweep_expired` / :func:`run_sla_sweeper` — the asyncio SLA sweeper that
  marks past-deadline rows ``EXPIRED`` and auto-``REJECT``s HIGH-risk ones (D5).

``FOR UPDATE SKIP LOCKED`` is applied only on PostgreSQL; on the SQLite test
database (which does not support it) the lock clause is omitted so the same code
runs with no Postgres.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ApprovalDecision, ApprovalRow, RiskLevel
from app.config import get_settings

from .models import Approval, ApprovalStatus
from .session import get_sessionmaker, set_tenant_scope

logger = logging.getLogger(__name__)


def _now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def _iso_utc(ts: datetime | None) -> str | None:
    """Render a (possibly naive) timestamp as an ISO 8601 UTC string, or ``None``.

    Timestamps stored via ``func.now()`` may come back naive; they are treated as
    UTC so the wire format is unambiguous for the admin inbox view.
    """
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).isoformat()


def _supports_skip_locked(session: AsyncSession) -> bool:
    """Return whether the bound dialect supports ``FOR UPDATE SKIP LOCKED``."""
    bind = session.get_bind()
    return bind.dialect.name == "postgresql"


def to_row(row: Approval) -> ApprovalRow:
    """Project an :class:`Approval` ORM row onto the wire :class:`ApprovalRow`."""
    return ApprovalRow(
        id=row.id,
        run_id=row.run_id,
        tenant_id=row.tenant_id,
        action=row.action,
        args=dict(row.args or {}),
        risk=row.risk,
        rationale=row.rationale,
        status=row.status.value,
        persona=row.persona,
        sla_deadline=_iso_utc(row.sla_deadline),
        created_at=_iso_utc(row.created_at) or _iso_utc(_now()) or "",
        ml_snapshot=dict(row.ml_snapshot or {}),
    )


@dataclass(frozen=True)
class ApprovalResolution:
    """The outcome of an optimistic decision transition on one approval.

    Attributes:
        id: The approval id the decision targeted.
        won: Whether this caller effected the transition (rowcount 1). ``False`` on
            a replayed/late decision — the idempotency guard that prevents a second
            resume.
        status: The approval's status *after* the call (or the current status when
            the transition did not fire), ``None`` when the id is unknown.
        run_id: The parked run's id (``thread_id``), when known.
        decision: The decision that was applied, for the resumer.
    """

    id: str
    won: bool
    status: str | None
    run_id: str | None
    decision: ApprovalDecision | None


async def enqueue_approval(
    *,
    approval_id: str,
    run_id: str,
    action: str,
    args: dict[str, Any] | None = None,
    risk: RiskLevel = RiskLevel.LOW,
    rationale: str | None = None,
    ml_snapshot: dict[str, Any] | None = None,
    thread_id: str | None = None,
    tenant_id: int | None = None,
    persona: str | None = None,
    trace_id: str | None = None,
    assignee_tier: str | None = None,
    sla_seconds: int | None = None,
) -> ApprovalRow:
    """Persist a ``PENDING`` approvals-inbox row for a run paused at the gate.

    Idempotent on ``approval_id``: a re-emitted gate (e.g. a resumed run that
    re-enters the node) returns the existing row rather than inserting a duplicate.

    Args:
        approval_id: The gate id (primary key); also the tool idempotency key.
        run_id: The paused run's id; equals ``thread_id`` for checkpoint lookup.
        action: The proposed action awaiting a human decision.
        args: The action's arguments.
        risk: The action's declared risk (drives the SLA auto-reject policy).
        rationale: Why the gate fired (risk/uncertainty).
        ml_snapshot: Model evidence frozen at gate time. NOT populated by the agent
            any more — no ML step runs in the graph, so the live gate omits it and
            every new row stores ``{}``. Retained as a parameter (and as a column)
            because dropping it needs a migration, which is deferred; an empty
            snapshot is the expected state, not a missing write.
        thread_id: The checkpoint thread id; defaults to ``run_id``.
        tenant_id: Owning tenant, for inbox scoping.
        persona: The persona that raised the run.
        trace_id: OTel trace id correlating the run's spans.
        assignee_tier: Approver tier; defaults to ``approval_default_tier``.
        sla_seconds: SLA window; defaults to ``approval_sla_seconds``.

    Returns:
        The enqueued (or pre-existing) row as an :class:`ApprovalRow`.
    """
    settings = get_settings()
    window = settings.approval_sla_seconds if sla_seconds is None else sla_seconds
    deadline = _now() + timedelta(seconds=window)
    tier = assignee_tier or settings.approval_default_tier

    async with get_sessionmaker()() as session:
        existing = await session.get(Approval, approval_id)
        if existing is not None:
            return to_row(existing)
        row = Approval(
            id=approval_id,
            run_id=run_id,
            thread_id=thread_id or run_id,
            tenant_id=tenant_id,
            status=ApprovalStatus.PENDING,
            persona=persona,
            action=action,
            args=dict(args or {}),
            risk=risk,
            rationale=rationale,
            ml_snapshot=dict(ml_snapshot or {}),
            trace_id=trace_id,
            assignee_tier=tier,
            sla_deadline=deadline,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return to_row(row)


async def get_approval(approval_id: str) -> ApprovalRow | None:
    """Return one approval row by id (or ``None`` if unknown)."""
    async with get_sessionmaker()() as session:
        row = await session.get(Approval, approval_id)
        return to_row(row) if row is not None else None


async def list_pending(
    tenant_id: int | None = None,
    limit: int = 50,
    *,
    for_update: bool = False,
) -> list[ApprovalRow]:
    """Return pending approvals, soonest SLA deadline first (the admin inbox query).

    Args:
        tenant_id: When given, restrict to this tenant's rows.
        limit: Maximum rows to return.
        for_update: Take a ``FOR UPDATE SKIP LOCKED`` lock on the claim path so
            concurrent workers never double-process a row. Applied only on
            PostgreSQL; a no-op on SQLite (which does not support the clause).

    Returns:
        Up to ``limit`` :class:`ApprovalRow` records ordered by ``sla_deadline``.
    """
    async with get_sessionmaker()() as session:
        # Engage Postgres RLS for this connection (no-op on SQLite; H1).
        await set_tenant_scope(session, tenant_id)
        stmt = (
            select(Approval)
            .where(Approval.status == ApprovalStatus.PENDING)
            .order_by(Approval.sla_deadline.asc(), Approval.created_at.asc())
            .limit(limit)
        )
        if tenant_id is not None:
            stmt = stmt.where(Approval.tenant_id == tenant_id)
        if for_update and _supports_skip_locked(session):
            stmt = stmt.with_for_update(skip_locked=True)
        rows = (await session.execute(stmt)).scalars().all()
        return [to_row(r) for r in rows]


async def count_approved(tenant_id: int | None = None) -> int:
    """Return how many human-gate approvals have been cleared (the Overview figure).

    Counts durable :class:`Approval` rows in the terminal ``APPROVED`` state — a gate
    a human approved *and* whose resume finalised (an approve decision moves the row
    ``PENDING → RESUMING`` and finalisation flips it to ``APPROVED``). This is the
    honest "actions approved" tally: an in-flight ``RESUMING`` row is not yet counted,
    and there is never a fabricated figure. Cheap and side-effect-free (a single
    ``COUNT``); the caller degrades to ``0`` when the store is unavailable.

    Args:
        tenant_id: When given, restrict the count to this tenant's rows (and engage
            Postgres RLS for the connection); ``None`` counts platform-wide.

    Returns:
        The number of ``APPROVED`` approval rows in scope.
    """
    async with get_sessionmaker()() as session:
        # Engage Postgres RLS for this connection (no-op on SQLite; H1).
        await set_tenant_scope(session, tenant_id)
        stmt = (
            select(func.count())
            .select_from(Approval)
            .where(Approval.status == ApprovalStatus.APPROVED)
        )
        if tenant_id is not None:
            stmt = stmt.where(Approval.tenant_id == tenant_id)
        return int((await session.execute(stmt)).scalar_one())


async def resolve_approval(
    approval_id: str,
    decision: ApprovalDecision,
    approver: str | None = None,
) -> ApprovalResolution:
    """Optimistically transition a ``PENDING`` approval on a human decision.

    The whole decision is a single atomic, guarded ``UPDATE``: on approve the row
    moves ``PENDING → RESUMING`` (arming exactly one resumer); on reject it moves
    ``PENDING → REJECTED`` (terminal, no execution). Because the ``WHERE`` clause
    pins ``status == PENDING``, only the first caller wins — a replayed or racing
    decision finds the row no longer pending and becomes a no-op, so a run can never
    be resumed (or executed) twice.

    Args:
        approval_id: The approval to resolve.
        decision: The human's verdict.
        approver: Who decided (recorded for the audit trail).

    Returns:
        An :class:`ApprovalResolution`; ``won`` is ``True`` only for the caller that
        effected the transition.
    """
    target = (
        ApprovalStatus.RESUMING
        if decision is ApprovalDecision.APPROVE
        else ApprovalStatus.REJECTED
    )
    async with get_sessionmaker()() as session:
        result = await session.execute(
            update(Approval)
            .where(
                Approval.id == approval_id,
                Approval.status == ApprovalStatus.PENDING,
            )
            .values(status=target, decided_at=_now(), decided_by=approver)
        )
        await session.commit()
        won = (result.rowcount or 0) == 1
        row = await session.get(Approval, approval_id)
        if row is not None:
            await session.refresh(row)
        return ApprovalResolution(
            id=approval_id,
            won=won,
            status=row.status.value if row is not None else None,
            run_id=row.run_id if row is not None else None,
            decision=decision if won else None,
        )


async def finalize_resumed(approval_id: str) -> None:
    """Mark a ``RESUMING`` approval ``APPROVED`` once its resume has completed."""
    async with get_sessionmaker()() as session:
        await session.execute(
            update(Approval)
            .where(
                Approval.id == approval_id,
                Approval.status == ApprovalStatus.RESUMING,
            )
            .values(status=ApprovalStatus.APPROVED)
        )
        await session.commit()


@dataclass(frozen=True)
class SweepAction:
    """One transition the SLA sweeper applied to an expired row."""

    id: str
    run_id: str
    status: ApprovalStatus
    risk: RiskLevel


async def sweep_expired(now: datetime | None = None) -> list[SweepAction]:
    """Apply the SLA policy to every past-deadline ``PENDING`` row (one pass).

    HIGH-risk rows are auto-``REJECT``ed (fail-safe, decision D5); everything else
    is marked ``EXPIRED``. Each transition is guarded on ``status == PENDING`` so it
    never races a concurrent human decision.

    Args:
        now: The reference time (defaults to the current UTC time); injectable for
            deterministic tests.

    Returns:
        The list of :class:`SweepAction` transitions applied this pass.
    """
    cutoff = now or _now()
    actions: list[SweepAction] = []
    async with get_sessionmaker()() as session:
        stmt = select(Approval).where(
            Approval.status == ApprovalStatus.PENDING,
            Approval.sla_deadline.is_not(None),
            Approval.sla_deadline < cutoff,
        )
        if _supports_skip_locked(session):
            stmt = stmt.with_for_update(skip_locked=True)
        rows = (await session.execute(stmt)).scalars().all()
        for row in rows:
            new_status = (
                ApprovalStatus.REJECTED
                if row.risk is RiskLevel.HIGH
                else ApprovalStatus.EXPIRED
            )
            row.status = new_status
            row.decided_at = cutoff
            row.decided_by = "sla-sweeper"
            actions.append(
                SweepAction(
                    id=row.id, run_id=row.run_id, status=new_status, risk=row.risk
                )
            )
        await session.commit()
    return actions


async def run_sla_sweeper(
    stop: asyncio.Event,
    *,
    interval: float | None = None,
) -> None:
    """Run the SLA sweeper until ``stop`` is set (the in-process asyncio task).

    Each cycle runs one :func:`sweep_expired` pass then waits ``interval`` seconds
    (or until ``stop`` fires). All errors are logged and swallowed so a transient
    database blip never kills the sweeper.

    Args:
        stop: An event that ends the loop when set.
        interval: Seconds between passes; defaults to
            ``approval_sweeper_interval_seconds``.
    """
    period = interval or get_settings().approval_sweeper_interval_seconds
    while not stop.is_set():
        try:
            await sweep_expired()
        except Exception:  # noqa: BLE001 - the sweeper must survive transient errors
            logger.warning("SLA sweeper pass failed", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=period)
        except TimeoutError:
            continue
