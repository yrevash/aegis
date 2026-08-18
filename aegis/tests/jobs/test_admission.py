"""Admission control against a real PostgreSQL: both gates, and the arithmetic behind them.

Run live rather than against a fake store because every claim here is a *database* claim.
The concurrency cap counts rows the ``tenant_isolation`` policy governs; the budget gate
sums the same ``usage_ledger`` the gateway enforces against, over a naive-UTC window a
SQLite double would compare differently; and the load-bearing property — that a tenant
cannot widen a cap the platform set — lives in the resolver's fold over the settings
table, not in a Python conditional anyone could read.

The strongest test in this file is
:func:`test_a_tenant_row_asking_for_a_wider_cap_cannot_widen_it`. It writes the tenant's
row **straight into ``settings``**, bypassing :func:`aegis.settings.write_setting`
entirely, so what it proves is not "the write guard refused" but that even a row already
in the database cannot loosen the cap: ``TIGHTEN_ONLY`` is arithmetic over a chain that
always contains the platform value, and that is why it is the right merge rule for a cap.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from aegis.governance.models import Budget, BudgetScope, BudgetWindow, UsageLedger
from aegis.governance.rls import set_tenant_scope
from aegis.jobs import (
    AdmissionDeniedError,
    BudgetExceededError,
    Document,
    JobRun,
    JobStatus,
    admit,
)
from aegis.settings.models import Setting, SettingScope
from aegis.settings.spec import UnknownSettingError, spec_for

from .._seed import ensure_tenants

_TENANT = 70301
_OTHER_TENANT = 70302

#: The platform default for ``jobs.max_inflight.ingest``, read from the catalogue rather
#: than restated: a test that hard-codes 4 would pass for the wrong reason the day the
#: platform default changes.
_CAP: int = spec_for("jobs.max_inflight.ingest").default

#: The platform default USD cap, likewise read from the catalogue.
_USD_CAP: float = spec_for("budget.usd_cap").default


@pytest_asyncio.fixture
async def db(pg_sessionmaker: async_sessionmaker) -> async_sessionmaker:
    """The unprivileged sessionmaker with the tenants the job foreign keys need."""
    await ensure_tenants(pg_sessionmaker, _TENANT, _OTHER_TENANT)
    return pg_sessionmaker


async def _add_jobs(
    db: async_sessionmaker,
    tenant_id: int,
    count: int,
    *,
    status: JobStatus = JobStatus.RUNNING,
    job_type: str = "ingest",
    prefix: str = "wf",
) -> list[int]:
    """Insert ``count`` job rows for a tenant and return their ids."""
    async with db() as session:
        rows = [
            JobRun(
                tenant_id=tenant_id,
                job_type=job_type,
                workflow_id=f"{prefix}-{tenant_id}-{status.value}-{index}",
                status=status,
            )
            for index in range(count)
        ]
        session.add_all(rows)
        await session.commit()
        return [row.id for row in rows]


async def _spend(db: async_sessionmaker, tenant_id: int, cost_usd: float) -> None:
    """Write one committed ledger row for a tenant."""
    async with db() as session:
        session.add(UsageLedger(tenant_id=tenant_id, model="fake", cost_usd=cost_usd))
        await session.commit()


async def _admit(
    db: async_sessionmaker,
    *,
    tenant_id: int | None,
    job_type: str = "ingest",
    estimated_cost_usd: float = 0.0,
) -> None:
    """Call ``admit`` the way a request would: tenant scope bound, then rolled back."""
    async with db() as session:
        await set_tenant_scope(session, tenant_id)
        try:
            await admit(
                session,
                tenant_id=tenant_id,
                job_type=job_type,
                estimated_cost_usd=estimated_cost_usd,
            )
        finally:
            await session.rollback()


# ── The concurrency gate ─────────────────────────────────────────────────────


async def test_the_nth_job_is_admitted_and_the_nth_plus_one_is_refused(db) -> None:
    """The cap binds at exactly the declared number, and says so in the reason."""
    await _add_jobs(db, _TENANT, _CAP - 1)
    # One short of the cap: admitted, with no exception to catch.
    await _admit(db, tenant_id=_TENANT)

    await _add_jobs(db, _TENANT, 1, prefix="wf-last")
    with pytest.raises(AdmissionDeniedError) as caught:
        await _admit(db, tenant_id=_TENANT)

    error = caught.value
    assert error.in_flight == _CAP
    assert error.cap == _CAP
    assert error.job_type == "ingest"
    # The reason is the whole point of raising rather than queueing: it has to name the
    # cap that bound, or a user is told "no" with nothing actionable in it.
    assert "jobs.max_inflight.ingest" in error.reason
    assert str(_CAP) in error.reason


async def test_terminal_jobs_do_not_count_against_the_cap(db) -> None:
    """A finished job holds no worker slot, so it must not hold a slot in the count."""
    for status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED):
        await _add_jobs(db, _TENANT, _CAP, status=status, prefix=f"wf-{status.value}")
    # 3 × cap rows exist, every one of them terminal.
    await _admit(db, tenant_id=_TENANT)


async def test_a_reconciling_job_still_counts(db) -> None:
    """``RECONCILING`` means "we do not yet know", and the safe reading of that is "busy"."""
    await _add_jobs(db, _TENANT, _CAP, status=JobStatus.RECONCILING)
    with pytest.raises(AdmissionDeniedError):
        await _admit(db, tenant_id=_TENANT)


async def test_another_tenants_jobs_do_not_consume_this_tenants_cap(db) -> None:
    """The cap is per tenant; a busy neighbour must not lock this tenant out."""
    await _add_jobs(db, _OTHER_TENANT, _CAP * 2, prefix="wf-other")
    await _admit(db, tenant_id=_TENANT)


async def test_a_tenant_row_asking_for_a_wider_cap_cannot_widen_it(db) -> None:
    """A stored tenant value above the platform default changes nothing.

    Written directly into ``settings``, so the refusal cannot be the write guard doing the
    work: this is ``TIGHTEN_ONLY`` resolving as a minimum over a chain that always
    contains the platform default. That property is why a cap is ``TIGHTEN_ONLY`` and not
    ``OVERRIDE`` — under ``OVERRIDE`` the row below would have handed this tenant every
    worker slot on the box.
    """
    async with db() as session:
        await set_tenant_scope(session, _TENANT)
        session.add(
            Setting(
                scope=SettingScope.TENANT,
                tenant_id=_TENANT,
                key="jobs.max_inflight.ingest",
                value=64,
                updated_by="a-tenant-admin",
            )
        )
        await session.commit()

    await _add_jobs(db, _TENANT, _CAP)
    with pytest.raises(AdmissionDeniedError) as caught:
        await _admit(db, tenant_id=_TENANT)
    assert caught.value.cap == _CAP


async def test_a_tenant_row_asking_for_a_tighter_cap_binds(db) -> None:
    """The direction that *is* allowed still works — a cap can only ever go down."""
    async with db() as session:
        await set_tenant_scope(session, _TENANT)
        session.add(
            Setting(
                scope=SettingScope.TENANT,
                tenant_id=_TENANT,
                key="jobs.max_inflight.ingest",
                value=1,
                updated_by="a-tenant-admin",
            )
        )
        await session.commit()

    await _add_jobs(db, _TENANT, 1)
    with pytest.raises(AdmissionDeniedError) as caught:
        await _admit(db, tenant_id=_TENANT)
    assert caught.value.cap == 1


async def test_an_undeclared_job_type_fails_closed(db) -> None:
    """A job type with no declared cap raises rather than resolving to "unlimited"."""
    with pytest.raises(UnknownSettingError):
        await _admit(db, tenant_id=_TENANT, job_type="not-a-declared-job-type")


# ── The budget gate ──────────────────────────────────────────────────────────


async def test_a_job_that_fits_the_remaining_budget_is_admitted(db) -> None:
    """Spend well inside the cap leaves room, and the gate says nothing."""
    await _spend(db, _TENANT, _USD_CAP / 2)
    await _admit(db, tenant_id=_TENANT, estimated_cost_usd=1.0)


async def test_a_job_beyond_the_remaining_budget_is_refused(db) -> None:
    """Committed spend plus the estimate over the cap is a refusal carrying the numbers."""
    await _spend(db, _TENANT, _USD_CAP - 1.0)
    with pytest.raises(BudgetExceededError) as caught:
        await _admit(db, tenant_id=_TENANT, estimated_cost_usd=2.0)

    error = caught.value
    assert error.cap_usd == pytest.approx(_USD_CAP)
    assert error.spent_usd == pytest.approx(_USD_CAP - 1.0)
    assert error.estimated_cost_usd == pytest.approx(2.0)
    assert error.window is BudgetWindow.DAY
    assert "cap" in error.reason


async def test_another_tenants_spend_does_not_consume_this_tenants_budget(db) -> None:
    """Spend is attributed, not pooled — the ledger is per tenant and so is the gate."""
    await _spend(db, _OTHER_TENANT, _USD_CAP)
    await _admit(db, tenant_id=_TENANT, estimated_cost_usd=1.0)


async def test_a_budgets_row_binds_even_when_it_is_tighter_than_the_catalogue(db) -> None:
    """An administrator's $1 cap must bind, or the budgets screen is decoration.

    The catalogue cap is far higher here, so a gate that read only the catalogue would
    admit this job — which is the failure a tenant admin would report as "I set a cap and
    it did nothing".
    """
    async with db() as session:
        session.add(
            Budget(
                tenant_id=_TENANT,
                scope_type=BudgetScope.TENANT,
                scope_id=_TENANT,
                window=BudgetWindow.DAY,
                usd_cap=1.0,
            )
        )
        await session.commit()

    with pytest.raises(BudgetExceededError) as caught:
        await _admit(db, tenant_id=_TENANT, estimated_cost_usd=2.0)
    assert caught.value.cap_usd == pytest.approx(1.0)


async def test_a_negative_estimate_is_refused(db) -> None:
    """A negative pre-authorisation would silently *raise* the remaining budget."""
    with pytest.raises(ValueError, match="negative"):
        await _admit(db, tenant_id=_TENANT, estimated_cost_usd=-1.0)


# ── The two gates are independent ────────────────────────────────────────────


async def test_the_gates_refuse_separately_and_say_which(db) -> None:
    """At the cap but rich, and under the cap but broke, produce different types.

    The distinction is not cosmetic: a concurrency refusal clears by waiting and a budget
    refusal needs an administrator, so a caller that cannot tell them apart cannot tell a
    user what to do.
    """
    await _add_jobs(db, _TENANT, _CAP)
    with pytest.raises(AdmissionDeniedError):
        await _admit(db, tenant_id=_TENANT, estimated_cost_usd=0.001)

    await _spend(db, _OTHER_TENANT, _USD_CAP)
    with pytest.raises(BudgetExceededError):
        await _admit(db, tenant_id=_OTHER_TENANT, estimated_cost_usd=1.0)


async def test_documents_and_jobs_of_one_tenant_are_the_only_ones_counted(db) -> None:
    """The count is scoped by tenant *and* job type, proved against real neighbouring rows."""
    async with db() as session:
        session.add(
            Document(
                tenant_id=_TENANT,
                filename="filing.pdf",
                content_sha256="c" * 64,
                mime_type="application/pdf",
                size_bytes=1024,
                status=JobStatus.RUNNING,
            )
        )
        await session.commit()
    await _add_jobs(db, _TENANT, _CAP, job_type="reindex", prefix="wf-reindex")
    # Every one of those rows is running, for this tenant — but of another type.
    await _admit(db, tenant_id=_TENANT)
