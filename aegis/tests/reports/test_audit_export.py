"""The streaming reader returns the screen's rows — all of them, in the same order.

The export exists to hand over the *whole* trail, so the two things that could go
wrong are silent: a keyset cursor that drops a row at a page boundary, and a predicate
that quietly differs from the one the audit screen reads. Both are asserted against
``list_recent_audit`` itself over a real PostgreSQL served by a NOSUPERUSER
NOBYPASSRLS role, rather than against a hand-written expectation that would drift
with it.
"""

from __future__ import annotations

from aegis.governance import list_recent_audit, record_audit
from aegis.reports import stream_audit_rows

from .._seed import ensure_tenants


async def _write(n: int, *, tenant_id: int, prefix: str = "act") -> None:
    for i in range(n):
        await record_audit(
            action=f"{prefix}.{i}",
            actor="alice",
            model=None,
            trace_id=None,
            payload={"i": i},
            tenant_id=tenant_id,
        )


async def test_paging_returns_every_row_in_the_screen_order(db):
    """Five rows over a two-row page size: no boundary drops one, none repeats."""
    await ensure_tenants(db, 1)
    await _write(5, tenant_id=1)

    async with db() as session:
        streamed = [
            row.action async for row in stream_audit_rows(session, tenant_id=1, batch_size=2)
        ]

    assert streamed == [row.action for row in await list_recent_audit(50, tenant_id=1)]
    assert len(streamed) == 5
    assert len(set(streamed)) == 5


async def test_another_tenants_rows_are_not_in_the_stream(db):
    """The export's predicate is the screen's: a tenant filter, applied."""
    await ensure_tenants(db, 1, 2)
    await _write(3, tenant_id=1, prefix="mine")
    await _write(3, tenant_id=2, prefix="theirs")

    async with db() as session:
        mine = [row.action async for row in stream_audit_rows(session, tenant_id=1, batch_size=2)]

    assert len(mine) == 3
    assert all(action.startswith("mine.") for action in mine)


async def test_the_action_prefix_filter_narrows_the_stream(db):
    await ensure_tenants(db, 1)
    await _write(2, tenant_id=1, prefix="memory")
    await _write(2, tenant_id=1, prefix="report")

    async with db() as session:
        rows = [
            row.action
            async for row in stream_audit_rows(session, tenant_id=1, action_prefix="report.")
        ]

    assert sorted(rows) == ["report.0", "report.1"]
