"""Durable approvals-inbox data layer — the source of truth for a paused run (§1.3).

A run that trips the human gate does not live in RAM: it is a persisted
:class:`~app.data.models.Approval` row (``PENDING``) plus a LangGraph checkpoint
keyed by ``thread_id == run_id``. That makes the pause survive a restart and lets
an admin resolve it out-of-band from the inbox, decoupled from the SSE socket.

This module owns the typed CRUD over that table:

- :func:`enqueue_approval` — INSERT a ``PENDING`` row when the gate fires.
- :func:`list_pending` — the *claim* query (``ORDER BY sla_deadline``), with a
  ``FOR UPDATE SKIP LOCKED`` path so many workers never double-process a row.
- :func:`list_approvals` — the inbox *screen*'s query (§7.1): status, ``since``,
  tenant and requester filters, every one of which narrows and none of which widens.
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
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aegis.governance.audit import chain_row

from app.api.schemas import ApprovalDecision, ApprovalRow, RiskLevel
from app.config import get_settings

from .models import Approval, ApprovalStatus, AuditLog
from .session import bind_scope_for_session, get_sessionmaker, set_tenant_scope

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
        actions=[dict(a) for a in (row.actions or []) if isinstance(a, dict)],
        requested_by=row.requested_by,
        decided_at=_iso_utc(row.decided_at),
        decided_by=row.decided_by,
    )


#: Where an approver goes to decide **this** gate — the section slug and the gate's own
#: id, with no ``/app/<portal>`` prefix.
#:
#: It was ``/app/tenant_admin/approvals``. A gate's alert is tenant-scoped, so it is
#: also read by that tenant's ``ai_team`` and ``client`` (whose own gates these often
#: are) and by platform staff; for all of them the absolute path named a portal their
#: session may not enter, and the route guard sent them home without a word. The
#: emitter names the screen and the entity — the reader resolves it against its own
#: portal. See :mod:`app.data.notifications` for the field's contract.
_APPROVALS_TARGET = "approvals?approval={approval_id}"


async def _notify_approval_awaiting(row: Approval) -> None:
    """Announce a gate that is now waiting on a human. Never fails the enqueue.

    Targets the **tenant** (``user_id`` left ``None``), not the user whose run raised the
    gate. That is the whole point of a gate: the person who needs to see it is an
    approver, and the requester is by construction not authorised to decide it.

    A ``HIGH``-risk gate is ``critical`` rather than ``warning``, because the SLA policy
    treats it differently too — an unanswered HIGH-risk gate is auto-**rejected** when
    its deadline passes (decision D5), so the cost of not looking is a refused action
    rather than a lapsed one.

    Args:
        row: The freshly inserted ``PENDING`` approval.
    """
    from .notifications import emit

    await emit(
        tenant_id=row.tenant_id,
        kind="approval.awaiting",
        severity="critical" if row.risk is RiskLevel.HIGH else "warning",
        title="Approval needed",
        # A person reads this on a bell, not in a log. An ISO-8601 stamp with
        # microseconds and a UTC offset — "by 2026-08-23T05:22:00.125264+00:00" — is
        # precise and unreadable, and it is the deadline that makes the alert urgent.
        body=(
            f"{row.action} is waiting on a decision "
            f"({row.risk.value} risk, {_deadline_phrase(row.sla_deadline)})."
        ),
        entity_ref=f"approval:{row.id}",
        href=_APPROVALS_TARGET.format(approval_id=row.id),
        # The approval id is already the gate's idempotency key (``enqueue_approval`` is
        # documented idempotent on it), so it is the right dedupe key here too: one
        # announcement per gate, whatever re-enters the node.
        dedupe_key=f"approval.awaiting:{row.id}",
    )


async def _notify_sla_auto_decided(row: Approval, new_status: ApprovalStatus) -> None:
    """Announce that the sweeper, not a person, decided a gate.

    Only for the ``REJECTED`` half. An ``EXPIRED`` row is a gate that lapsed with no
    consequence beyond itself, and alerting on every one of them would fill a tenant's
    bell with the routine end of gates nobody meant to answer. A HIGH-risk
    auto-**reject** is different: an action the platform was asked to take was refused,
    by a timer, with no human in the loop — the audit row this sweeper already writes
    (``approval.sla_expired``) records it, and an audit row is something you find only if
    you go looking.

    Args:
        row: The approval the sweeper just transitioned.
        new_status: What it was transitioned to.
    """
    if new_status is not ApprovalStatus.REJECTED:
        return
    from .notifications import emit

    await emit(
        tenant_id=row.tenant_id,
        kind="sla.auto_decided",
        severity="critical",
        title="Auto-rejected on SLA",
        body=(
            f"{row.action} was auto-rejected: a {row.risk.value}-risk action passed its "
            "SLA deadline undecided, and the safe answer to an unanswered gate is no."
        ),
        entity_ref=f"approval:{row.id}",
        href=_APPROVALS_TARGET.format(approval_id=row.id),
        # A row can only make this transition once (the sweep is guarded on PENDING), so
        # the id alone is the key; it is here so a second sweeper process racing the
        # first cannot produce a second alert either.
        dedupe_key=f"sla.auto_decided:{row.id}",
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


def _deadline_phrase(deadline: datetime | None) -> str:
    """Render an SLA deadline the way a person reads a deadline: as time remaining.

    "due in 4h", not "by 2026-08-23T05:22:00.125264+00:00". The absolute instant is
    still on the approvals screen for anyone who needs it; what the alert has to convey
    is urgency, and a microsecond-precision timestamp conveys it to nobody.
    """
    if deadline is None:
        return "no deadline"
    remaining = deadline - _now()
    seconds = remaining.total_seconds()
    if seconds <= 0:
        return "past its deadline"
    if seconds < 3600:
        return f"due in {max(1, int(seconds // 60))}m"
    if seconds < 86400:
        return f"due in {int(seconds // 3600)}h"
    return f"due in {int(seconds // 86400)}d"


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
    requested_by: int | None = None,
    actions: list[dict[str, Any]] | None = None,
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
        requested_by: The ``users.id`` whose run raised the gate, when a real user
            did. It is what scopes the inbox for a tenant's own user: they see the
            fate of the gates they raised and nothing else.
        actions: Every call approving this gate will run, not just ``action`` (the
            representative). A fan-out gate authorises several; a row that stored
            only the representative made the inbox understate what Approve does.
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
        # §9.5. Bound for the session's whole life, not for one transaction: this path
        # commits and then ``refresh``es, and the GUC is transaction-local — so a
        # one-shot ``set_tenant_scope`` would leave the refresh (and the row it returns
        # to the caller) unscoped. The scope auditor found this exact shape in seven
        # places; ``bind_scope_for_session`` is what closes the class rather than one of
        # them.
        bind_scope_for_session(session, tenant_id)
        existing = await session.get(Approval, approval_id)
        if existing is not None:
            return to_row(existing)
        row = Approval(
            id=approval_id,
            run_id=run_id,
            thread_id=thread_id or run_id,
            tenant_id=tenant_id,
            status=ApprovalStatus.PENDING,
            requested_by=requested_by,
            persona=persona,
            action=action,
            args=dict(args or {}),
            actions=[dict(a) for a in (actions or [])] or None,
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
    # After the commit and outside the session block, deliberately. A gate that exists
    # and nobody knows about is the pull-only failure this platform had everywhere, and
    # a gate that was *announced* and then rolled back would be worse. ``emit`` opens its
    # own transaction and swallows every failure it can have, so this line can neither
    # fail the enqueue nor delay the run past its own commit — see
    # :func:`app.data.notifications.emit`.
    #
    # The early return above (``existing is not None``) never reaches here, so a
    # re-emitted gate from a resumed run announces nothing a second time. The emitter's
    # ``dedupe_key`` on the approval id is the backstop for that, not the mechanism.
    await _notify_approval_awaiting(row)
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


async def list_approvals(
    *,
    tenant_id: int | None = None,
    requested_by: int | None = None,
    statuses: Sequence[ApprovalStatus] | None = None,
    since: datetime | None = None,
    limit: int = 50,
) -> list[ApprovalRow]:
    """Return the approvals matching every filter given — the inbox query (§7.1).

    Separate from :func:`list_pending`, which stays exactly what it is: the *claim*
    path, with its ``FOR UPDATE SKIP LOCKED`` lock and its one hard-wired status. This
    is the read a screen makes, and it has to answer three different questions from
    three different callers without ever letting one of them widen its own scope.

    Every filter narrows. There is no argument here that opens the query up: the
    caller resolves its authority first (``_scope_tenant`` for an admin, its own user
    id for everyone else) and hands the result down. ``tenant_id=None`` therefore
    means "the caller already established it may read every tenant" — it is never
    reached by omission, because :mod:`app.api.routes` cannot produce it without the
    explicit :data:`~aegis.retrieval.types.ALL_TENANTS` authority.

    Ordering follows the question. A pure ``pending`` filter is the actionable queue,
    so it comes back soonest-SLA-deadline first — the row about to expire is the row
    to look at. Any other filter is a history ("what happened to my gates?"), so it
    comes back newest first; sorting a decided list by a deadline that has already
    passed would order it by nothing a reader cares about.

    Args:
        tenant_id: Restrict to this tenant's rows. ``None`` applies no tenant
            predicate — only ever passed by a caller holding platform authority.
        requested_by: Restrict to gates raised by this ``users.id``. This is the
            client's scope and it is strictly tighter than the tenant's: a gate a
            user raised belongs, by construction, to that user's tenant.
        statuses: Restrict to these lifecycle statuses; ``None`` means every status.
        since: Restrict to rows created at or after this instant.
        limit: Maximum rows to return.

    Returns:
        Up to ``limit`` :class:`ApprovalRow` records in the order described above.
    """
    wanted = list(statuses) if statuses is not None else []
    async with get_sessionmaker()() as session:
        # Engage Postgres RLS for this connection (no-op on SQLite; H1).
        await set_tenant_scope(session, tenant_id)
        stmt = select(Approval).limit(limit)
        if wanted:
            stmt = stmt.where(Approval.status.in_(wanted))
        if tenant_id is not None:
            stmt = stmt.where(Approval.tenant_id == tenant_id)
        if requested_by is not None:
            stmt = stmt.where(Approval.requested_by == requested_by)
        if since is not None:
            stmt = stmt.where(Approval.created_at >= since)
        if wanted == [ApprovalStatus.PENDING]:
            stmt = stmt.order_by(Approval.sla_deadline.asc(), Approval.created_at.asc())
        else:
            stmt = stmt.order_by(Approval.created_at.desc(), Approval.id.asc())
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

    **It sweeps every tenant's inbox, and says so** (§9.5). The SLA is a platform
    obligation — a gate nobody answered expires whoever it belonged to — so this is a
    genuinely platform-wide read and write. It used to make that claim by binding no
    scope at all and leaning on the ``tenant_isolation`` predicate's fail-open branch,
    which is spelled identically to a path that simply forgot. Now it binds the platform
    scope explicitly, which is what keeps the sweeper working when the posture flips.

    Note that moving the sweeper to the **owner** engine would not have been a fix: the
    policies are installed with ``FORCE ROW LEVEL SECURITY``, so a non-superuser table
    owner is subject to them exactly like the serving role. The scope is the fix; the
    engine is not.

    Args:
        now: The reference time (defaults to the current UTC time); injectable for
            deterministic tests.

    Returns:
        The list of :class:`SweepAction` transitions applied this pass.
    """
    cutoff = now or _now()
    actions: list[SweepAction] = []
    #: The transitions to announce once the transaction has committed. Collected rather
    #: than emitted inline — see the loop after ``session.commit()``.
    decided: list[tuple[Approval, ApprovalStatus]] = []
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, None)
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
            # The audit row is written in THIS transaction, beside the status change,
            # because the sweeper is the only decision-maker on this platform that is
            # not a person: a human decision leaves ``approval.decision`` and a name,
            # and this used to leave nothing at all — an automated REJECT of a HIGH-risk
            # action appeared in the inbox as decided, by nobody, for no stated reason.
            # Committing them together is what stops the decision and its record from
            # ever disagreeing, which a second connection or a post-commit write could.
            # Chained in this transaction rather than inserted bare. An unchained row
            # sitting mid-chain is one the verifier can only report as uncovered, which
            # quietly shrinks what the trail proves every time the sweeper runs.
            session.add(
                await chain_row(
                    session,
                    tenant_id=row.tenant_id,
                    ts=cutoff,
                    action="approval.sla_expired",
                    actor="sla-sweeper",
                    trace_id=row.trace_id,
                    payload={
                        "approval_id": row.id,
                        "run_id": row.run_id,
                        "action_requested": row.action,
                        "risk": row.risk.value,
                        "status": new_status.value,
                        "sla_deadline": row.sla_deadline.isoformat()
                        if row.sla_deadline is not None
                        else None,
                        # Why this row and not another: HIGH fails safe, the rest lapse.
                        "reason": (
                            "auto-rejected: a HIGH-risk action passed its SLA deadline "
                            "undecided, and the safe answer to an unanswered gate is no"
                            if new_status is ApprovalStatus.REJECTED
                            else "expired: the SLA deadline passed with no decision"
                        ),
                    },
                )
            )
            actions.append(
                SweepAction(
                    id=row.id, run_id=row.run_id, status=new_status, risk=row.risk
                )
            )
            decided.append((row, new_status))
        await session.commit()
    # After the commit, and outside the session block: the audit row above is written in
    # the sweeper's transaction because a decision and its record must not be able to
    # disagree, but a notification is neither — it is a copy, and one published for a
    # transition that then rolled back would be an alert about something that did not
    # happen. Each call swallows its own failures, so a sweeper cycle cannot die here.
    for row, new_status in decided:
        await _notify_sla_auto_decided(row, new_status)
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
