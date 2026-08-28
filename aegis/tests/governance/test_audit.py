"""The audit writer persists rows and pulls the tenant from context when omitted.

``audit_log.tenant_id`` is a real foreign key, so the tenant an entry is attributed to
has to exist before the entry can be written. That is not ceremony: an audit row nobody
can attribute is not evidence, and it is precisely what the SQLite fixture used to let
these tests create.
"""

from __future__ import annotations

from aegis.governance import (
    GovernanceContext,
    list_recent_audit,
    record_audit,
    reset_governance_context,
    set_governance_context,
)

from .._seed import ensure_tenants


async def test_record_and_list_recent_audit(db):
    await ensure_tenants(db, 1)
    await record_audit(
        action="tool:update_request_status",
        actor="alice",
        model="gpt",
        trace_id="t-1",
        payload={"request_id": "R1"},
        approved_by="bob",
        tenant_id=1,
    )
    rows = await list_recent_audit(tenant_id=1)
    assert len(rows) == 1
    assert rows[0].action == "tool:update_request_status"
    assert rows[0].actor == "alice"
    assert rows[0].approved_by == "bob"
    assert rows[0].ts  # serialised as a non-empty ISO 8601 UTC string


async def test_record_audit_pulls_tenant_from_context(db):
    # Tenant 4 exists too, so the negative read below is about *this row's* attribution
    # rather than about a tenant the database has never heard of.
    await ensure_tenants(db, 3, 4)
    token = set_governance_context(GovernanceContext(tenant_id=3, user_id=9))
    try:
        # tenant_id omitted → taken from the bound governance context.
        await record_audit(
            action="autonomous:act",
            actor="agent",
            model="gpt",
            trace_id=None,
            payload={},
        )
    finally:
        reset_governance_context(token)
    # The row is attributed to tenant 3, so a tenant-3-scoped read finds it…
    assert len(await list_recent_audit(tenant_id=3)) == 1
    # …and a different tenant's scoped read does not.
    assert await list_recent_audit(tenant_id=4) == []


# ------------------------------------------------------- the tamper-evident chain


async def test_the_chain_verifies_and_a_tampered_row_is_caught(db):
    """Append-only by privilege is not the same as verifiable.

    Aegis's own architecture doc conceded that the owner role can still rewrite the
    trail — which means "append-only" was a statement about who holds a grant, not a
    property anyone outside the database could check. Chaining makes it checkable by
    somebody who does not trust the database at all.

    Two assertions, and the second is the one with teeth: a clean trail verifies, and a
    single edited field is located by row id. A verifier that only ever says "fine" has
    not been shown to be a verifier.
    """
    from sqlalchemy import select, update

    from aegis.governance.audit import verify_audit_chain
    from aegis.governance.models import AuditLog

    await ensure_tenants(db, 1)
    for i in range(3):
        await record_audit(
            action=f"tool:step_{i}", actor="alice", model="m", trace_id=f"t-{i}",
            payload={"n": i}, tenant_id=1,
        )

    clean = await verify_audit_chain(1)
    assert clean.intact, clean.detail
    assert clean.checked == 3
    assert clean.unchained == 0

    # Edit one field on the middle row, leaving its hash alone — exactly what an
    # attacker with UPDATE would do, and exactly what a per-row hash without chaining
    # would still catch but a bare "append-only" grant would not.
    async with db() as s:
        target = (
            await s.execute(select(AuditLog).where(AuditLog.action == "tool:step_1"))
        ).scalar_one()
        target_id = target.id
        await s.execute(
            update(AuditLog).where(AuditLog.id == target_id).values(actor="mallory")
        )
        await s.commit()

    broken = await verify_audit_chain(1)
    assert not broken.intact, "an edited row verified clean"
    assert broken.broken_at == target_id
    assert "edited" in broken.detail


async def test_a_deleted_row_breaks_every_row_after_it(db):
    """What chaining buys over per-row hashes.

    Row hashes alone prove no row was *edited*. They say nothing about a row being
    removed, because the survivors still hash correctly on their own — which is the
    quieter and more useful attack. Seeding each hash with its predecessor's means a
    deletion orphans everything downstream.
    """
    from sqlalchemy import delete, select

    from aegis.governance.audit import verify_audit_chain
    from aegis.governance.models import AuditLog

    await ensure_tenants(db, 1)
    for i in range(3):
        await record_audit(
            action=f"tool:step_{i}", actor="alice", model=None, trace_id=None,
            payload={"n": i}, tenant_id=1,
        )

    async with db() as s:
        victim = (
            await s.execute(select(AuditLog).where(AuditLog.action == "tool:step_1"))
        ).scalar_one()
        await s.execute(delete(AuditLog).where(AuditLog.id == victim.id))
        await s.commit()

    result = await verify_audit_chain(1)
    assert not result.intact, "a deleted row left the chain looking intact"
    assert "removed" in result.detail or "spliced" in result.detail


async def test_pre_chain_rows_are_reported_not_counted_as_verified(db):
    """We cannot prove anything about history nobody hashed, and must not imply we can.

    Folding un-hashed rows into a pass would be precisely the overclaim this feature
    exists to end — a green tick over rows the chain never covered.
    """
    from aegis.governance.audit import verify_audit_chain
    from aegis.governance.models import AuditLog

    await ensure_tenants(db, 1)
    async with db() as s:
        s.add(AuditLog(tenant_id=1, action="tool:ancient", actor="alice", payload={}))
        await s.commit()

    await record_audit(
        action="tool:modern", actor="alice", model=None, trace_id=None,
        payload={}, tenant_id=1,
    )

    result = await verify_audit_chain(1)
    assert result.intact
    assert result.unchained == 1, "the pre-chain row was not reported"
    assert result.checked == 1
    assert "predate" in result.detail
