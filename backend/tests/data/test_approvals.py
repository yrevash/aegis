"""Durable approvals-inbox data-layer tests, over the shared scratch PostgreSQL.

Covers the source-of-truth CRUD for a paused run: enqueue on gate, the admin
``list_pending`` query, the optimistic single-winner decision transition (the
idempotency guard that stops a double-resume), and the SLA sweeper's EXPIRED /
auto-reject-HIGH policy (decision D5).
"""

from __future__ import annotations

import pytest

from app.api.schemas import ApprovalDecision, RiskLevel
from app.data import (
    ApprovalStatus,
    enqueue_approval,
    finalize_resumed,
    get_approval,
    list_pending,
    resolve_approval,
    sweep_expired,
)

pytestmark = pytest.mark.asyncio


async def test_enqueue_persists_pending_row(db):
    row = await enqueue_approval(
        approval_id="ap-1",
        run_id="run-1",
        action="update_request_status",
        args={"request_id": "R1", "status": "resolved"},
        risk=RiskLevel.HIGH,
        rationale="wide conformal interval",
        ml_snapshot={"prediction": 12.0},
        persona="operations_lead",
    )
    assert row.id == "ap-1"
    assert row.status == "pending"
    assert row.risk is RiskLevel.HIGH
    assert row.sla_deadline is not None  # SLA deadline stamped
    assert row.created_at  # ISO timestamp present
    assert row.ml_snapshot == {"prediction": 12.0}
    assert row.args == {"request_id": "R1", "status": "resolved"}


async def test_enqueue_is_idempotent_on_id(db):
    first = await enqueue_approval(approval_id="dup", run_id="r", action="x")
    second = await enqueue_approval(approval_id="dup", run_id="r", action="x")
    assert first.id == second.id
    assert len(await list_pending()) == 1


async def test_list_pending_orders_by_sla_deadline(db):
    await enqueue_approval(approval_id="later", run_id="r1", action="x", sla_seconds=600)
    await enqueue_approval(approval_id="sooner", run_id="r2", action="x", sla_seconds=60)
    rows = await list_pending()
    assert [r.id for r in rows] == ["sooner", "later"]  # soonest deadline first


async def test_resolve_optimistic_single_winner_is_idempotent(db):
    await enqueue_approval(approval_id="ap-2", run_id="run-2", action="x", risk=RiskLevel.HIGH)

    first = await resolve_approval("ap-2", ApprovalDecision.APPROVE, "alice")
    assert first.won is True
    assert first.status == ApprovalStatus.RESUMING.value  # armed exactly one resumer
    assert first.run_id == "run-2"

    # A racing/replayed decision finds the row no longer PENDING → no-op.
    second = await resolve_approval("ap-2", ApprovalDecision.APPROVE, "bob")
    assert second.won is False
    assert await list_pending() == []  # no longer in the actionable queue


async def test_resolve_reject_is_terminal(db):
    await enqueue_approval(approval_id="ap-3", run_id="run-3", action="x", risk=RiskLevel.HIGH)
    res = await resolve_approval("ap-3", ApprovalDecision.REJECT, "alice")
    assert res.won is True
    assert res.status == ApprovalStatus.REJECTED.value


async def test_resolve_unknown_id_reports_not_won(db):
    res = await resolve_approval("ghost", ApprovalDecision.APPROVE, "alice")
    assert res.won is False
    assert res.status is None
    assert res.run_id is None


async def test_finalize_resumed_marks_approved(db):
    await enqueue_approval(approval_id="ap-4", run_id="run-4", action="x")
    await resolve_approval("ap-4", ApprovalDecision.APPROVE, "alice")  # -> RESUMING
    await finalize_resumed("ap-4")
    row = await get_approval("ap-4")
    assert row is not None
    assert row.status == ApprovalStatus.APPROVED.value


async def test_sla_sweeper_expires_and_auto_rejects_high(db):
    # Past-deadline rows (negative SLA window) plus one still-live row.
    await enqueue_approval(
        approval_id="high", run_id="rh", action="x", risk=RiskLevel.HIGH, sla_seconds=-10
    )
    await enqueue_approval(
        approval_id="low", run_id="rl", action="x", risk=RiskLevel.LOW, sla_seconds=-10
    )
    await enqueue_approval(
        approval_id="fresh", run_id="rf", action="x", risk=RiskLevel.HIGH, sla_seconds=3600
    )

    actions = await sweep_expired()
    by_id = {a.id: a.status for a in actions}
    assert by_id["high"] is ApprovalStatus.REJECTED   # HIGH auto-rejected (D5, fail-safe)
    assert by_id["low"] is ApprovalStatus.EXPIRED     # others simply expire
    assert "fresh" not in by_id                        # still within its SLA window

    remaining = {r.id for r in await list_pending()}
    assert remaining == {"fresh"}


async def test_the_sweepers_automated_decision_leaves_an_audit_trail(db):
    """The one decision-maker here that is not a person must still be accountable.

    A human decision writes ``approval.decision`` and a name. The sweeper wrote
    nothing at all, so an auto-REJECT of a HIGH-risk action showed up in the inbox as
    decided, by nobody, for no stated reason — and across a whole deployment's
    audit_log there were zero sweeper rows to reconstruct it from.
    """
    from sqlalchemy import select

    from app.data.models import AuditLog
    from app.data.session import get_sessionmaker

    await enqueue_approval(
        approval_id="high", run_id="rh", action="issue_credit",
        risk=RiskLevel.HIGH, sla_seconds=-10,
    )
    await enqueue_approval(
        approval_id="low", run_id="rl", action="lookup",
        risk=RiskLevel.LOW, sla_seconds=-10,
    )

    await sweep_expired()

    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "approval.sla_expired")
            )
        ).scalars().all()

    by_approval = {r.payload["approval_id"]: r for r in rows}
    assert set(by_approval) == {"high", "low"}, "every transition is recorded, not just one"
    assert all(r.actor == "sla-sweeper" for r in rows)

    # The record must carry WHY, and the two outcomes must not read alike.
    assert by_approval["high"].payload["status"] == ApprovalStatus.REJECTED.value
    assert by_approval["low"].payload["status"] == ApprovalStatus.EXPIRED.value
    assert "auto-rejected" in by_approval["high"].payload["reason"]
    assert by_approval["high"].payload["action_requested"] == "issue_credit"


async def test_sla_sweeper_is_noop_when_nothing_expired(db):
    await enqueue_approval(approval_id="ok", run_id="r", action="x", sla_seconds=3600)
    assert await sweep_expired() == []
