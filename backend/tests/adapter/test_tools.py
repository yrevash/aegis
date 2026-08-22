"""Tool tests — idempotency, reversibility and audit, with fakes only."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.adapter.schema import (
    Category,
    Channel,
    Priority,
    Region,
    RequestStatus,
    ServiceRequest,
)
from app.adapter.tools import (
    FIND_REQUESTS_MAX_LIMIT,
    TOOL_REGISTRY,
    InMemoryRecordStore,
    ToolContext,
    add_case_note,
    assign_request,
    find_requests,
    update_request_status,
)
from app.api.schemas import RiskLevel


def _seed_request(**overrides) -> ServiceRequest:
    fields = {
        "id": "req-1",
        "title": "t",
        "description": "d",
        "category": Category.TECHNICAL,
        "priority": Priority.HIGH,
        "channel": Channel.CHAT,
        "region": Region.NA,
        "status": RequestStatus.NEW,
        "customer_id": "cust-1",
        "assigned_agent_id": "agent-1",
        "created_at": datetime(2024, 1, 1),
        "updated_at": datetime(2024, 1, 1),
        "queue_depth_at_open": 1,
        "sla_hours": 24.0,
    }
    fields.update(overrides)
    return ServiceRequest(**fields)


@pytest.fixture
def ctx(audit_sink):
    store = InMemoryRecordStore([_seed_request()])
    return ToolContext(store=store, actor="operations_lead", audit=audit_sink)


async def test_update_status_is_idempotent(ctx):
    args = {"request_id": "req-1", "status": "in_progress"}
    first = await update_request_status(args, ctx)
    second = await update_request_status(args, ctx)

    assert first.changed is True
    assert second.changed is False
    assert ctx.store.get_request("req-1").status is RequestStatus.IN_PROGRESS


async def test_update_status_is_reversible(ctx):
    result = await update_request_status(
        {"request_id": "req-1", "status": "resolved"}, ctx
    )
    assert result.inverse is not None
    # Applying the inverse restores the original status.
    await update_request_status(result.inverse.args, ctx)
    assert ctx.store.get_request("req-1").status is RequestStatus.NEW


async def test_assign_is_reversible(ctx):
    result = await assign_request({"request_id": "req-1", "agent_id": "agent-9"}, ctx)
    assert ctx.store.get_request("req-1").assigned_agent_id == "agent-9"
    await assign_request(result.inverse.args, ctx)
    assert ctx.store.get_request("req-1").assigned_agent_id == "agent-1"


async def test_add_note_idempotent_and_self_reversible(ctx):
    args = {"request_id": "req-1", "body": "checked logs", "author": "ops"}
    first = await add_case_note(args, ctx)
    second = await add_case_note(args, ctx)

    assert first.changed is True
    assert second.changed is False  # deterministic note id dedupes
    assert len(ctx.store.get_request("req-1").notes) == 1

    # The inverse retracts the note.
    await add_case_note(first.inverse.args, ctx)
    assert ctx.store.get_request("req-1").notes == []


async def test_tools_record_audit(ctx, audit_sink):
    await update_request_status({"request_id": "req-1", "status": "triaged"}, ctx)
    await assign_request({"request_id": "req-1", "agent_id": "agent-2"}, ctx)
    actions = [e["action"] for e in audit_sink.entries]
    assert "update_request_status" in actions
    assert "assign_request" in actions
    assert all(e["actor"] == "operations_lead" for e in audit_sink.entries)


async def test_missing_request_is_safe(ctx):
    result = await update_request_status({"request_id": "nope", "status": "closed"}, ctx)
    assert result.ok is False
    assert result.changed is False


# ── find_requests — the read tool that makes the HIGH-risk write reachable ───────


@pytest.fixture
def desk(audit_sink):
    """A small multi-record desk, so filtering and ordering have something to do."""
    store = InMemoryRecordStore(
        [
            _seed_request(
                id="req-old-billing",
                title="Duplicate charge",
                category=Category.BILLING,
                status=RequestStatus.RESOLVED,
                created_at=datetime(2024, 1, 1),
            ),
            _seed_request(
                id="req-new-billing",
                title="Refund not received",
                category=Category.BILLING,
                status=RequestStatus.RESOLVED,
                created_at=datetime(2024, 6, 1),
            ),
            _seed_request(
                id="req-tech",
                title="Login loop",
                category=Category.TECHNICAL,
                status=RequestStatus.NEW,
                created_at=datetime(2024, 3, 1),
            ),
        ]
    )
    return ToolContext(store=store, actor="operations_lead", audit=audit_sink)


async def test_find_requests_returns_real_ids_the_write_tool_accepts(desk):
    """The whole point: the lookup hands back an id ``update_request_status`` takes."""
    result = await find_requests({"category": "billing", "status": "resolved"}, desk)

    assert result.ok is True
    assert result.changed is False
    assert "req-old-billing" in result.summary
    assert "req-new-billing" in result.summary
    assert "req-tech" not in result.summary

    # An id lifted straight out of the summary drives the gated write.
    written = await update_request_status(
        {"request_id": "req-old-billing", "status": "closed"}, desk
    )
    assert written.ok is True
    assert written.changed is True


async def test_find_requests_text_matches_the_id_not_only_the_prose(desk):
    """A planner holding an id types it into ``text``; that must find the request."""
    result = await find_requests({"text": "req-tech"}, desk)
    assert result.ok is True
    assert "req-tech" in result.summary
    assert "req-old-billing" not in result.summary


async def test_find_requests_orders_oldest_first_and_can_reverse(desk):
    oldest = await find_requests({"category": "billing"}, desk)
    newest = await find_requests({"category": "billing", "oldest_first": False}, desk)

    assert oldest.summary.index("req-old-billing") < oldest.summary.index(
        "req-new-billing"
    )
    assert newest.summary.index("req-new-billing") < newest.summary.index(
        "req-old-billing"
    )


async def test_find_requests_never_mutates(desk):
    before = {r.id: r.model_dump() for r in desk.store.list_requests()}
    await find_requests({}, desk)
    after = {r.id: r.model_dump() for r in desk.store.list_requests()}
    assert before == after


async def test_find_requests_is_bounded(desk):
    """The default caps rows, and a limit above the hard ceiling is refused."""
    capped = await find_requests({"limit": 1}, desk)
    assert capped.summary.count("\n") == 1  # header + exactly one row

    with pytest.raises(ValueError, match="less than or equal"):
        await find_requests({"limit": FIND_REQUESTS_MAX_LIMIT + 1}, desk)


async def test_find_requests_reports_an_empty_shortlist_as_not_done(desk):
    """No match is ``ok=False`` so the self-repair loop re-plans with a wider filter."""
    result = await find_requests({"category": "shipping"}, desk)
    assert result.ok is False
    assert result.changed is False
    assert result.inverse is None


async def test_find_requests_is_audited_with_the_ids_it_disclosed(desk, audit_sink):
    await find_requests({"category": "technical"}, desk)
    entry = next(e for e in audit_sink.entries if e["action"] == "find_requests")
    assert entry["payload"]["returned"] == ["req-tech"]
    assert entry["payload"]["filter"]["category"] == "technical"


def test_find_requests_is_low_risk_and_declared_read_only():
    """It must never gate — the gate this tool exists to reach is on the HIGH write."""
    spec = TOOL_REGISTRY["find_requests"]
    assert spec.risk is RiskLevel.LOW
    assert spec.read_only is True
    assert spec.destructive is False
    assert TOOL_REGISTRY["update_request_status"].risk is RiskLevel.HIGH
