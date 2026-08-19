"""Stream the audit trail out of PostgreSQL a page at a time (§7.12).

The trail is the one export with no natural size limit: it grows with every request
the platform serves. ``list_recent_audit`` — what the audit **screen** reads — takes a
``limit`` and materialises that many rows, which is right for a screen and wrong for an
export: an operator asking for a quarter of evidence would get a list of every row held
in memory at once, in a process that is also serving requests.

So this reader keeps the screen's contract and changes only how the rows arrive. Same
table, same ordering (``ts`` descending, ``id`` descending as the tie-break), same
tenant predicate — asserted against ``list_recent_audit`` itself in
``tests/reports/test_audit_export.py`` rather than promised in this docstring — but the
rows come back in batches through a **keyset** cursor: ``WHERE (ts, id) < (last_ts,
last_id)``. Not ``OFFSET``, which re-scans everything it skips and, worse, silently
drops or repeats rows when the trail is written to mid-export; the keyset cursor is
stable under concurrent inserts because it names a position rather than a count.

The session is the caller's. That is the same arrangement :mod:`aegis.redteam.store`
uses, and it is what lets the HTTP layer bind the RLS scope
(:func:`aegis.governance.rls.set_tenant_scope`) on the very connection these queries
run on: the app-level ``WHERE tenant_id`` here is the belt, and the policy underneath
is the braces.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from aegis.governance.models import AuditLog

__all__ = ["AUDIT_COLUMNS", "audit_cells", "stream_audit_rows"]

#: The exported columns, in order — the audit screen's own columns.
#:
#: ``payload`` is deliberately absent. It is free-form JSON written by every call site
#: in the product, so exporting it would put whatever a payload happens to carry into a
#: file that leaves the platform, and it is the one column the screen never shows. The
#: preamble says so rather than leaving the omission to be discovered.
AUDIT_COLUMNS: tuple[str, ...] = (
    "id",
    "ts_utc",
    "tenant_id",
    "action",
    "actor",
    "model",
    "trace_id",
    "approved_by",
)

#: Rows fetched per round trip. Large enough that a 100k-row export is 200 queries, not
#: 100k; small enough that no single fetch is a memory event.
DEFAULT_BATCH = 500


def _iso_utc(ts: datetime) -> str:
    """Render a possibly naive timestamp as an unambiguous ISO 8601 UTC string.

    Args:
        ts: The stored timestamp. ``server_default=func.now()`` returns naive values,
            which are UTC by construction on this schema.

    Returns:
        The ISO 8601 string, always carrying an offset.
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).isoformat()


def audit_cells(row: AuditLog) -> tuple[object, ...]:
    """Project one audit row onto :data:`AUDIT_COLUMNS`.

    Args:
        row: The ORM row.

    Returns:
        The cells, in column order.
    """
    return (
        row.id,
        _iso_utc(row.ts),
        row.tenant_id,
        row.action,
        row.actor,
        row.model,
        row.trace_id,
        row.approved_by,
    )


async def stream_audit_rows(
    session: AsyncSession,
    *,
    tenant_id: int | None,
    since: datetime | None = None,
    until: datetime | None = None,
    actor: str | None = None,
    action_prefix: str | None = None,
    batch_size: int = DEFAULT_BATCH,
) -> AsyncIterator[AuditLog]:
    """Yield audit rows newest-first, one keyset page at a time.

    Args:
        session: The caller's session, with the RLS tenant scope already bound.
        tenant_id: The tenant filter. ``None`` means unrestricted and must only ever be
            reached from a resolved platform-wide authority — this function trusts its
            caller to have gone through the scope resolver, exactly as every other
            accessor in the codebase does.
        since: Lower bound on ``ts`` (inclusive), or ``None`` for no lower bound.
        until: Upper bound on ``ts`` (inclusive), or ``None`` for no upper bound.
        actor: Restrict to one actor (exact match), or ``None``.
        action_prefix: Restrict to actions starting with this string, or ``None``.
        batch_size: Rows per round trip.

    Yields:
        :class:`~aegis.governance.models.AuditLog` rows, newest first.
    """
    cursor: tuple[datetime, int] | None = None
    while True:
        stmt = (
            select(AuditLog)
            .order_by(AuditLog.ts.desc(), AuditLog.id.desc())
            .limit(batch_size)
        )
        if tenant_id is not None:
            stmt = stmt.where(AuditLog.tenant_id == tenant_id)
        if since is not None:
            stmt = stmt.where(AuditLog.ts >= _naive(since))
        if until is not None:
            stmt = stmt.where(AuditLog.ts <= _naive(until))
        if actor is not None:
            stmt = stmt.where(AuditLog.actor == actor)
        if action_prefix:
            stmt = stmt.where(AuditLog.action.startswith(action_prefix))
        if cursor is not None:
            stmt = stmt.where(tuple_(AuditLog.ts, AuditLog.id) < cursor)

        rows: Sequence[AuditLog] = (await session.execute(stmt)).scalars().all()
        if not rows:
            return
        for row in rows:
            yield row
        if len(rows) < batch_size:
            return
        last = rows[-1]
        cursor = (last.ts, last.id)


def _naive(ts: datetime) -> datetime:
    """Return ``ts`` as a naive UTC timestamp, matching the stored column.

    ``audit_log.ts`` is ``TIMESTAMP WITHOUT TIME ZONE`` holding UTC. Comparing it
    against an aware bound makes PostgreSQL raise rather than answer, so the bound is
    converted here — once, where the column's convention is known.

    Args:
        ts: An aware or naive instant. A naive one is already read as UTC.

    Returns:
        The naive UTC equivalent.
    """
    return ts.astimezone(UTC).replace(tzinfo=None) if ts.tzinfo is not None else ts
