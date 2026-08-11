"""The audit-log writer — the system's first-class accountability trail.

Every autonomous or approved action passes through :func:`record_audit`, which
persists who did what, with which model, under which trace, and who approved it.
It opens its own short-lived session (from the injected factory) so callers (agent
nodes, tool executors) can log without threading a session through their signatures.

The session factory and ``set_tenant_scope`` are injected via :func:`configure_audit`
(the host wires them at startup, mirroring :mod:`aegis.governance.enforcement`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aegis.governance.context import get_governance_context
from aegis.governance.models import AuditLog
from aegis.governance.rls import set_tenant_scope as _default_set_tenant_scope
from aegis.governance.types import AuditLogRow

__all__ = ["configure_audit", "list_recent_audit", "record_audit"]

_SessionFactory = Callable[[], AsyncSession]
_SetTenantScope = Callable[[AsyncSession, int | None], Awaitable[None]]

_session_factory: _SessionFactory | None = None
_set_tenant_scope: _SetTenantScope = _default_set_tenant_scope


def configure_audit(
    *,
    session_factory: _SessionFactory | None = None,
    set_tenant_scope: _SetTenantScope | None = None,
) -> None:
    """Wire the injected session factory and (optionally) ``set_tenant_scope``.

    Args:
        session_factory: A zero-arg callable returning an :class:`AsyncSession`.
        set_tenant_scope: The RLS scope binder; defaults to the package's own.
    """
    global _session_factory, _set_tenant_scope
    if session_factory is not None:
        _session_factory = session_factory
    if set_tenant_scope is not None:
        _set_tenant_scope = set_tenant_scope


def _session() -> AsyncSession:
    """Return a fresh :class:`AsyncSession` from the injected factory."""
    if _session_factory is None:
        raise RuntimeError(
            "aegis.governance audit is not configured; call "
            "configure_audit(session_factory=...) at startup."
        )
    return _session_factory()


async def record_audit(
    *,
    action: str,
    actor: str | None,
    model: str | None,
    trace_id: str | None,
    payload: dict[str, Any],
    approved_by: str | None = None,
    tenant_id: int | None = None,
) -> None:
    """Persist one audit record, attributed to the acting tenant when known (H2).

    Args:
        action: The action performed (e.g. ``"tool:create_ticket"``).
        actor: The principal that initiated the action, if known.
        model: The model deployment id involved, if any.
        trace_id: The OTel trace id (hex) correlating this action to its spans.
        payload: Structured details of the action (arguments, result summary).
        approved_by: The human who approved the action at the HITL gate, if any.
        tenant_id: The owning tenant; when omitted it is taken from the per-request
            governance context (``None`` for platform-scoped/ungoverned actions).
    """
    if tenant_id is None:
        gov = get_governance_context()
        tenant_id = gov.tenant_id if gov is not None else None
    async with _session() as session:
        await _set_tenant_scope(session, tenant_id)
        session.add(
            AuditLog(
                tenant_id=tenant_id,
                action=action,
                actor=actor,
                model=model,
                trace_id=trace_id,
                payload=payload,
                approved_by=approved_by,
            )
        )
        await session.commit()


def _iso_utc(ts: datetime) -> str:
    """Render a (possibly naive) timestamp as an ISO 8601 UTC string.

    Timestamps stored via ``func.now()`` may come back naive; they are treated as
    UTC so the wire format is unambiguous for the admin audit view.
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).isoformat()


async def list_recent_audit(
    limit: int = 50, *, tenant_id: int | None = None
) -> list[AuditLogRow]:
    """Return the most recent audit rows, newest first, tenant-scoped (H2).

    Opens its own short-lived session (like :func:`record_audit`) so read-only
    callers need not thread a session through their signatures.

    Args:
        limit: Maximum number of rows to return (caller should clamp to a sane max).
        tenant_id: App-scope the trail to one tenant (belt-and-suspenders over RLS);
            ``None`` returns every tenant's rows (the platform-admin view).

    Returns:
        Up to ``limit`` :class:`~aegis.governance.types.AuditLogRow` records ordered by
        timestamp descending (ties broken by id descending), with ``ts`` serialised
        as an ISO 8601 UTC string.
    """
    async with _session() as session:
        stmt = (
            select(AuditLog)
            .order_by(AuditLog.ts.desc(), AuditLog.id.desc())
            .limit(limit)
        )
        if tenant_id is not None:
            stmt = stmt.where(AuditLog.tenant_id == tenant_id)
        rows = (await session.execute(stmt)).scalars().all()
    return [
        AuditLogRow(
            id=row.id,
            ts=_iso_utc(row.ts),
            action=row.action,
            actor=row.actor,
            model=row.model,
            trace_id=row.trace_id,
            approved_by=row.approved_by,
        )
        for row in rows
    ]
