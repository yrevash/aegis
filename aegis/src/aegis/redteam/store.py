"""Reading and writing the red-team run record.

Four functions, each taking an :class:`~sqlalchemy.ext.asyncio.AsyncSession` the
caller has already bound a tenant scope onto (see
:func:`aegis.governance.rls.set_tenant_scope`), so the Postgres policy and the
app-level predicate are both in force on every call rather than one of them being
whichever the caller remembered.

Every read takes ``tenant_id`` as an explicit :class:`TenantFilter` — ``None`` means
*unrestricted*, and it is reachable only from a caller that resolved a platform-wide
authority. The belt-and-suspenders shape is deliberate and is the same one
:mod:`aegis.settings.resolver` uses: the RLS policy is the boundary, and the ``WHERE``
clause is what makes a missing policy fail a test instead of leaking silently.

:func:`previous_run` is the one that makes a report evidence rather than a snapshot.
A block rate on its own says nothing — 82% is good or catastrophic depending on what
it was last week — so the run before this one, *of the same suite and the same mode*,
is fetched with it. Comparing an offline run against a live one would manufacture a
regression out of a configuration difference, which is why ``mode`` is part of the
match and not a display detail.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aegis.redteam.models import RedTeamRun

__all__ = ["RunSummary", "list_runs", "load_run", "previous_run", "record_run"]

#: How many history rows a single read may return. Clamped here rather than trusted
#: from a query string: an unbounded scan is a denial-of-service knob handed to
#: whoever holds a token.
_HISTORY_LIMIT_MAX = 100


@dataclass(frozen=True)
class RunSummary:
    """One history row — the scalars, without the report body.

    History is a list of a hundred runs and each report is tens of kilobytes of
    per-probe detail; sending them all so a table can render six numbers per row is
    how a history screen becomes the slowest page in the product.
    """

    run_id: str
    tenant_id: int | None
    suite: str
    mode: str
    started_at: datetime | None
    duration_ms: int
    initiated_by: str
    attacks_total: int
    attacks_blocked: int
    block_rate: float
    controls_total: int
    false_positives: int
    false_positive_rate: float
    min_block_rate: float
    max_false_positive_rate: float
    passed: bool
    estimated_cost_usd: float

    @classmethod
    def of(cls, row: RedTeamRun) -> RunSummary:
        """Project a stored run onto its summary."""
        return cls(
            run_id=row.run_id,
            tenant_id=row.tenant_id,
            suite=row.suite,
            mode=row.mode,
            started_at=row.started_at,
            duration_ms=row.duration_ms,
            initiated_by=row.initiated_by,
            attacks_total=row.attacks_total,
            attacks_blocked=row.attacks_blocked,
            block_rate=row.block_rate,
            controls_total=row.controls_total,
            false_positives=row.false_positives,
            false_positive_rate=row.false_positive_rate,
            min_block_rate=row.min_block_rate,
            max_false_positive_rate=row.max_false_positive_rate,
            passed=row.passed,
            estimated_cost_usd=row.estimated_cost_usd,
        )


async def record_run(
    session: AsyncSession,
    *,
    run_id: str,
    tenant_id: int | None,
    suite: str,
    mode: str,
    duration_ms: int,
    initiated_by: str,
    initiated_role: str,
    report: dict[str, Any],
    estimated_cost_usd: float = 0.0,
) -> RedTeamRun:
    """Persist one finished run and return the stored row.

    The scalar columns are read **out of** ``report`` rather than passed alongside it,
    so the number on the history row and the number in the stored evidence are the
    same number by construction. A caller cannot record a 90% block rate over a report
    that says 60%.

    Args:
        session: A session with the tenant scope already bound.
        run_id: The public identifier for this run.
        tenant_id: The owning tenant, or ``None`` for a platform-scoped run.
        suite: The suite id that was run.
        mode: ``"offline"`` or ``"live"``.
        duration_ms: Wall-clock time the run took.
        initiated_by: Username of the operator who started it.
        initiated_role: That operator's fine role.
        report: The lossless ``RedTeamReport.as_dict()`` projection.
        estimated_cost_usd: What the run was estimated to cost before it started.

    Returns:
        The flushed :class:`~aegis.redteam.models.RedTeamRun`.
    """
    overall: dict[str, Any] = dict(report.get("overall") or {})
    thresholds: dict[str, Any] = dict(report.get("thresholds") or {})
    row = RedTeamRun(
        run_id=run_id,
        tenant_id=tenant_id,
        suite=suite,
        mode=mode,
        duration_ms=duration_ms,
        initiated_by=initiated_by,
        initiated_role=initiated_role,
        attacks_total=int(overall.get("attacksTotal", 0)),
        attacks_blocked=int(overall.get("attacksBlocked", 0)),
        controls_total=int(overall.get("controlsTotal", 0)),
        false_positives=int(overall.get("falsePositives", 0)),
        block_rate=float(overall.get("blockRate", 0.0)),
        false_positive_rate=float(overall.get("falsePositiveRate", 0.0)),
        min_block_rate=float(thresholds.get("minBlockRate", 0.0)),
        max_false_positive_rate=float(thresholds.get("maxFalsePositiveRate", 0.0)),
        passed=bool(report.get("passed", False)),
        estimated_cost_usd=estimated_cost_usd,
        report=report,
    )
    session.add(row)
    await session.flush()
    return row


async def list_runs(
    session: AsyncSession,
    *,
    tenant_id: int | None,
    suite: str | None = None,
    limit: int = 25,
) -> tuple[RunSummary, ...]:
    """Return the newest runs visible to ``tenant_id``, newest first.

    Args:
        session: A session with the tenant scope already bound.
        tenant_id: The tenant to restrict to, or ``None`` for the unrestricted
            platform-wide read. ``None`` must only ever come from a resolved
            platform authority, never from a principal that merely lacks a tenant.
        suite: Restrict to one suite; ``None`` returns every suite.
        limit: How many rows to return, clamped to :data:`_HISTORY_LIMIT_MAX`.

    Returns:
        A tuple of :class:`RunSummary`, newest first.
    """
    stmt = select(RedTeamRun).order_by(RedTeamRun.started_at.desc(), RedTeamRun.id.desc())
    if tenant_id is not None:
        stmt = stmt.where(RedTeamRun.tenant_id == tenant_id)
    if suite is not None:
        stmt = stmt.where(RedTeamRun.suite == suite)
    stmt = stmt.limit(max(1, min(limit, _HISTORY_LIMIT_MAX)))
    rows = (await session.execute(stmt)).scalars().all()
    return tuple(RunSummary.of(row) for row in rows)


async def load_run(
    session: AsyncSession, run_id: str, *, tenant_id: int | None
) -> RedTeamRun | None:
    """Return one stored run, or ``None`` when it does not exist in this scope.

    ``None`` covers both "no such run" and "not yours" on purpose: distinguishing them
    tells a caller which run ids exist in tenants they cannot read.
    """
    stmt = select(RedTeamRun).where(RedTeamRun.run_id == run_id)
    if tenant_id is not None:
        stmt = stmt.where(RedTeamRun.tenant_id == tenant_id)
    return (await session.execute(stmt)).scalars().first()


async def previous_run(
    session: AsyncSession, row: RedTeamRun, *, tenant_id: int | None
) -> RunSummary | None:
    """Return the run before ``row`` — same suite, same mode, same tenant.

    The comparison a report needs to be evidence. Matching on ``mode`` as well as
    ``suite`` is what stops a live run being diffed against an offline one and
    reported as a 20-point improvement that is really just the model layer being
    switched on.

    Returns:
        The immediately preceding :class:`RunSummary`, or ``None`` when this is the
        first run of that suite in that mode — which is an honest answer, not an
        empty state to be filled in with zeros.
    """
    stmt = (
        select(RedTeamRun)
        .where(
            RedTeamRun.suite == row.suite,
            RedTeamRun.mode == row.mode,
            RedTeamRun.id < row.id,
        )
        .order_by(RedTeamRun.id.desc())
        .limit(1)
    )
    # Always the same tenant as the run itself, whatever the caller may read. A
    # platform operator reading unrestricted would otherwise get "the previous run"
    # from whichever tenant happened to go last, and the delta would be nonsense.
    owner = row.tenant_id if tenant_id is None else tenant_id
    stmt = stmt.where(
        RedTeamRun.tenant_id.is_(None) if owner is None else RedTeamRun.tenant_id == owner
    )
    found = (await session.execute(stmt)).scalars().first()
    return RunSummary.of(found) if found is not None else None
