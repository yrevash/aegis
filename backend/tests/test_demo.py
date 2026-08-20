"""``python -m app.demo`` — the demo corpus, and the kill switch that removes it.

Everything here runs on the scratch PostgreSQL bound by the ``db`` fixture, served by
the ``NOSUPERUSER NOBYPASSRLS`` role, so the rows are written through the same
``tenant_isolation`` policies a request is.

Three claims are load-bearing, and only those three are tested:

* **the ledger clears the forecast's history floor.** This is the whole reason the
  module exists: ``/forecast/*`` refused with *"2 of 71 observations needed"*, and 71 is
  :func:`aegis.forecast.minimum_history` for the shipped ``horizon=14, freq='D'``. The
  test measures the same series the endpoint reads, through the same reader, for every
  scope — including the NULL-tenant platform slice, which is the one a per-tenant test
  would miss.
* **``--wipe`` removes exactly the demo rows.** Exactly, in both directions: every
  tagged row goes, and a planted *untagged* row in each of the seven tables — including
  one whose ``trace_id`` is NULL, which a ``NOT LIKE`` predicate would silently keep or
  drop depending on how it was written — is still there afterwards.
* **a second run is a no-op**, exactly as ``app.seed`` is.

The window is deliberately the shipped ninety days only in the first test. The other two
make claims that are independent of how long the window is, and paying eight seconds to
restate that would be paying for nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from aegis.forecast import minimum_history
from aegis.governance.models import AuditLog, UsageLedger
from aegis.jobs.models import JobRun, JobStatus
from aegis.redteam.models import RedTeamRun
from aegis.runs.models import Run, RunEvent
from sqlalchemy import func, select

from app.data import Approval, ApprovalStatus, get_sessionmaker, set_tenant_scope
from app.demo import (
    DEMO_ENV,
    DEMO_HISTORY_DAYS,
    DEMO_PREFIX,
    _run,
    resolve_scopes,
    seed_demo,
    wipe_demo,
)
from app.forecast.ledger import ledger_series
from app.seed import seed

pytestmark = pytest.mark.asyncio

#: Every table the corpus writes, paired with the column carrying the tag. Restated
#: here rather than imported from ``app.demo`` on purpose: a test that reads the
#: module's own registry cannot notice a table being dropped out of it.
_TAGGED: tuple[tuple[str, object, object], ...] = (
    ("usage_ledger", UsageLedger, UsageLedger.trace_id),
    ("audit_log", AuditLog, AuditLog.trace_id),
    ("runs", Run, Run.run_id),
    ("run_events", RunEvent, RunEvent.run_id),
    ("job_runs", JobRun, JobRun.workflow_id),
    ("approvals", Approval, Approval.id),
    ("redteam_runs", RedTeamRun, RedTeamRun.run_id),
)


@pytest.fixture(autouse=True)
async def _wipe_after(db):
    """Remove the corpus after every test in this module.

    Not politeness — necessity. ``run_events`` is partitioned by month and the suite's
    per-test ``TRUNCATE`` names the partitions that existed when the session's schema
    was built; the ninety-day corpus creates three or four *older* ones, whose rows
    would therefore survive into every later test in the session.
    """
    yield
    await wipe_demo()


async def _platform_slice_rows() -> int:
    """Return how many demo ledger rows carry no tenant — the platform's own spend."""
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, None)
        return (
            await session.execute(
                select(func.count())
                .select_from(UsageLedger)
                .where(
                    UsageLedger.tenant_id.is_(None),
                    UsageLedger.trace_id.like(f"{DEMO_PREFIX}%"),
                )
            )
        ).scalar_one()


async def _count(model, column=None) -> int:  # noqa: ANN001 - any mapped class/column
    """Return the row count for ``model``, optionally only the demo-tagged rows."""
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, None)
        stmt = select(func.count()).select_from(model)
        if column is not None:
            stmt = stmt.where(column.like(f"{DEMO_PREFIX}%"))
        return (await session.execute(stmt)).scalar_one()


async def test_the_ledger_clears_the_forecasts_history_floor(db):
    """Every scope's spend series is long enough for the forecaster to accept it.

    Measured through :func:`app.forecast.ledger.ledger_series` — the reader the
    ``/forecast`` handlers call — so this asserts on the same buckets the endpoint
    would, gap-filling and lookback included, rather than on a raw row count that could
    be ninety days of rows crammed into two calendar days.
    """
    await seed()
    await seed_demo()

    need = minimum_history(14, 7)
    assert need == 71  # the refusal the corpus exists to answer, spelled out

    scopes = await resolve_scopes()
    assert {s.tenant_id for s in scopes} == {1, 2, None}
    # Platform spend is ledgered under no tenant by design; a corpus without that slice
    # leaves every platform-admin surface reading as though the platform never ran.
    assert await _platform_slice_rows() > 0

    for scope in scopes:
        points = await ledger_series(tenant_id=scope.tenant_id, metric="spend")
        assert len(points) >= need, f"{scope.label} has {len(points)} of {need} buckets"
        assert len(points) == DEMO_HISTORY_DAYS

        # A flat series fits a straight line and tells an operator nothing. The
        # weekday/weekend rhythm and the two spikes are the reason it is not flat.
        values = [p.value for p in points]
        assert min(values) > 0.0, "a gap-filled zero day would break the seasonality"
        assert max(values) > 2.0 * (sum(values) / len(values)), "no spike survived"

        weekdays = [p.value for p in points if p.ts.weekday() < 5]
        weekends = [p.value for p in points if p.ts.weekday() >= 5]
        assert sum(weekends) / len(weekends) < 0.5 * (sum(weekdays) / len(weekdays))


async def test_a_second_run_writes_nothing(db):
    """Running the seeder twice creates nothing, exactly as ``app.seed`` behaves."""
    await seed()
    first = await seed_demo(days=14)
    assert first.total_created > 0
    assert first.existing == {}

    before = {name: await _count(model) for name, model, _ in _TAGGED}
    second = await seed_demo(days=14)

    assert second.total_created == 0
    assert set(second.existing) == set(before)
    assert {name: await _count(model) for name, model, _ in _TAGGED} == before


async def test_wipe_removes_exactly_the_demo_rows(db):
    """``--wipe`` deletes every tagged row and not one untagged row.

    The planted rows are the mutation trap. A predicate that is too broad (dropping the
    ``LIKE``, matching ``%demo%``, or deleting by tenant) takes one of them with it; a
    predicate that is too narrow leaves demo rows behind. The NULL-``trace_id`` ledger
    row is the specific shape a hand-written ``NOT LIKE`` gets wrong, because in SQL
    ``NULL LIKE 'demo-%'`` is NULL rather than false.
    """
    await seed()
    await seed_demo(days=14)
    planted = await _plant_untagged_rows()

    tagged_before = {
        name: await _count(model, column) for name, model, column in _TAGGED
    }
    total_before = {name: await _count(model) for name, model, _ in _TAGGED}
    assert all(count > 0 for count in tagged_before.values()), tagged_before

    summary = await wipe_demo()

    assert summary.deleted == tagged_before
    assert summary.total == sum(tagged_before.values())
    for name, model, column in _TAGGED:
        assert await _count(model, column) == 0, f"{name} kept demo rows"
        assert await _count(model) == total_before[name] - tagged_before[name]

    assert await _surviving_untagged_rows() == planted


async def test_seeding_refuses_without_the_environment_gate(db, monkeypatch):
    """Without ``AEGIS_DEMO_DATA=1`` the CLI refuses and writes nothing.

    The gate is half the kill switch: fabricated data must never appear because a
    process happened to run. The wipe is deliberately *not* gated, so the removal path
    cannot fail for want of an exported variable — asserted here, because the two
    behaviours are easy to make symmetric by accident.
    """
    await seed()
    monkeypatch.delenv(DEMO_ENV, raising=False)

    assert await _run([]) == 2
    assert await _count(UsageLedger, UsageLedger.trace_id) == 0

    monkeypatch.setenv(DEMO_ENV, "1")
    assert await _run([]) == 0
    assert await _count(UsageLedger, UsageLedger.trace_id) > 0

    monkeypatch.delenv(DEMO_ENV, raising=False)
    assert await _run(["--wipe"]) == 0
    assert await _count(UsageLedger, UsageLedger.trace_id) == 0


# ─────────────────────────────────────────────────────────────────────────────
# The untagged rows the wipe must leave alone
# ─────────────────────────────────────────────────────────────────────────────

#: One untagged row per table, keyed by table name — what a real deployment holds
#: beside the corpus, and what must still be there when the corpus is gone.
_UNTAGGED = {
    "usage_ledger": "a" * 32,
    "audit_log": "b" * 32,
    "runs": "ingest:1:7",
    "run_events": "ingest:1:7",
    "job_runs": "ingest:1:7",
    "approvals": "real-gate-northwind-1",
    "redteam_runs": "rt-0123456789abcdef",
}


async def _plant_untagged_rows() -> dict[str, str]:
    """Write one real (untagged) row into each tagged table, and return their keys.

    Plus a ``usage_ledger`` row with **no** trace id at all: the gateway writes one
    whenever no OTel span is active, and it is the row a careless predicate loses.
    """
    now = datetime.now(UTC)
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, 1)
        session.add_all(
            [
                UsageLedger(
                    tenant_id=1,
                    user_id=6,
                    ts=now,
                    model="genailab-maas-gpt-4o",
                    prompt_tokens=100,
                    completion_tokens=20,
                    cost_usd=0.00045,
                    trace_id=_UNTAGGED["usage_ledger"],
                ),
                UsageLedger(
                    tenant_id=1,
                    user_id=6,
                    ts=now,
                    model="genailab-maas-gpt-4o",
                    prompt_tokens=90,
                    completion_tokens=10,
                    cost_usd=0.00032,
                    trace_id=None,
                ),
                AuditLog(
                    tenant_id=1,
                    ts=now,
                    action="auth.login",
                    actor="northwind.admin",
                    trace_id=_UNTAGGED["audit_log"],
                    payload={},
                ),
                JobRun(
                    tenant_id=1,
                    user_id=6,
                    job_type="ingest",
                    workflow_id=_UNTAGGED["job_runs"],
                    status=JobStatus.SUCCEEDED,
                    created_at=now,
                ),
                Run(run_id=_UNTAGGED["runs"], tenant_id=1, started_at=now),
                RunEvent(
                    run_id=_UNTAGGED["run_events"],
                    tenant_id=1,
                    seq=0,
                    ts=now,
                    event_type="run_started",
                    payload={},
                ),
                RedTeamRun(
                    run_id=_UNTAGGED["redteam_runs"],
                    tenant_id=1,
                    suite="owasp-full",
                    mode="offline",
                    started_at=now,
                ),
                Approval(
                    id=_UNTAGGED["approvals"],
                    run_id="real-run-northwind-1",
                    thread_id="real-run-northwind-1",
                    tenant_id=1,
                    status=ApprovalStatus.PENDING,
                    action="issue_supplier_credit",
                    args={},
                    created_at=now,
                    sla_deadline=now + timedelta(days=30),
                ),
            ]
        )
        await session.commit()
    return dict(_UNTAGGED)


async def _surviving_untagged_rows() -> dict[str, str]:
    """Return the untagged key still present in each table, for comparison.

    Also asserts the NULL-``trace_id`` ledger row survived, which has no key to return.
    """
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, None)
        found: dict[str, str] = {}
        for name, _model, column in _TAGGED:
            value = (
                await session.execute(
                    select(column).where(column == _UNTAGGED[name])
                )
            ).scalars().first()
            if value is not None:
                found[name] = value
        nulls = (
            await session.execute(
                select(func.count())
                .select_from(UsageLedger)
                .where(UsageLedger.trace_id.is_(None))
            )
        ).scalar_one()
    assert nulls == 1, "the ledger row with no trace id was deleted by the wipe"
    return found
