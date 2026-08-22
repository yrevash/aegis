"""The tenant scope of a memory path must survive that path's own commits.

Against a **real PostgreSQL** with the tenant policies installed and the connection made
as a ``NOSUPERUSER NOBYPASSRLS`` role (the ``db`` fixture), because every claim here is a
claim about what Postgres does with ``set_config(..., is_local => true)`` — SQLite has
neither the GUC nor the policy and would agree with any implementation.

Each test installs the **fail-closed** predicate on its own scratch database, since that
is the posture the defect is dangerous under: the fail-open predicate stops restricting
when no scope is bound, so an unscoped statement and a correctly scoped one return the
same rows and no test could tell them apart.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from aegis.governance.rls import bootstrap_rls, configure_rls, set_tenant_scope
from aegis.memory.config import MemoryConfig
from aegis.memory.consolidate import enqueue_consolidation, sweep_pending
from aegis.memory.scope import bind_memory_scope, bound_memory_scope
from aegis.memory.stores import (
    ConsolidationStatus,
    MemoryConsolidationJob,
    MemoryFact,
    MemoryMessage,
    MemoryOrigin,
    MemorySession,
)
from aegis.memory.working import assemble_working_memory

from .._seed import add_in_fk_order
from .test_consolidate import FakeComplete, FakeEmbed

pytestmark = pytest.mark.asyncio

_TENANT = 1
_OTHER = 2


@pytest_asyncio.fixture
async def fail_closed(pg_owner_engine: AsyncEngine) -> AsyncIterator[None]:
    """Re-install this scratch database's policies in the **fail-closed** flavour.

    ``configure_rls`` is process state, so it is restored afterwards; ``bootstrap_rls``
    drops and recreates each policy, so re-running it over the template's fail-open ones
    is the supported way to switch flavour.
    """
    configure_rls(fail_closed=True)
    try:
        await bootstrap_rls(pg_owner_engine)
        yield
    finally:
        configure_rls(fail_closed=False)


async def _seed_turns(session, *, tenant_id: int, subject: str, session_id: str) -> None:
    """One memory session, three turns and one durable fact for ``tenant_id``.

    Scoped before writing: under the fail-closed predicate the policy's ``WITH CHECK``
    refuses an INSERT that no bound scope owns, which is the policy working.
    """
    await set_tenant_scope(session, tenant_id)
    await add_in_fk_order(
        session,
        MemorySession(id=session_id, subject_id=subject, tenant_id=tenant_id),
        *[
            MemoryMessage(
                subject_id=subject,
                session_id=session_id,
                turn_index=i,
                role="user",
                origin=MemoryOrigin.USER,
                content=f"turn {i} for {subject}",
                tenant_id=tenant_id,
            )
            for i in range(3)
        ],
        MemoryFact(
            subject_id=subject,
            tenant_id=tenant_id,
            fact_type="preference",
            subject="customer",
            predicate="prefers",
            object="teal",
            text=f"{subject} prefers teal",
            confidence=0.9,
            importance=6,
            source_turn_ids=[],
        ),
    )
    await session.commit()


async def _guc(session) -> tuple[str, str]:
    """Read both scope GUCs off this session's current connection."""
    row = (
        await session.execute(
            text(
                "SELECT current_setting('app.tenant_id', true), "
                "current_setting('app.tenant_all', true)"
            )
        )
    ).one()
    return (row[0] or ""), (row[1] or "")


async def test_the_bound_scope_outlives_the_commit_and_can_be_retargeted(db) -> None:
    """The property the whole fix rests on: BEGIN re-applies the scope, every time."""
    async with db() as s:
        await bind_memory_scope(s, _TENANT)
        assert await _guc(s) == ("1", "")

        await s.commit()  # the transaction-local GUC is discarded here
        assert await _guc(s) == ("1", ""), "the scope did not survive the commit"
        assert bound_memory_scope(s) == (True, _TENANT)

        # Retargeting replaces the scope rather than stacking a second binding on top of
        # it — this is what lets one sweeper move between tenants on one session.
        await bind_memory_scope(s, None)
        await s.commit()
        assert await _guc(s) == ("", "on")
        await bind_memory_scope(s, _OTHER)
        await s.commit()
        assert await _guc(s) == ("2", "")


async def test_no_scope_rides_the_connection_back_into_the_pool(
    db: async_sessionmaker,
) -> None:
    """A pooled connection must never carry a scope to whoever borrows it next.

    This is the property that rules out ``SET SESSION``: the GUC stays
    transaction-local, so the database forgets it at commit whether or not any reset ran.
    The check re-uses the same small pool immediately, so the connection under test is
    the one just released.
    """
    async with db() as s:
        await bind_memory_scope(s, _TENANT)
        await s.commit()
        assert await _guc(s) == ("1", "")

    for _ in range(3):  # whichever pooled connection comes back, none may be scoped
        async with db() as fresh:
            assert await _guc(fresh) == ("", "")


@pytest.mark.usefixtures("fail_closed")
async def test_recall_still_reads_after_its_own_commit_under_fail_closed(db) -> None:
    """The read-path regression: assembly re-reads the raw window *after* recall commits.

    Before the fix that second read ran with no scope bound, which under the fail-closed
    predicate is zero rows — half the assembled context silently missing.
    """
    async with db() as s:
        await _seed_turns(s, tenant_id=_TENANT, subject="user:1", session_id="sess-1")

    async with db() as s:
        await set_tenant_scope(s, _TENANT)  # exactly what a request path does, once
        assembled = await assemble_working_memory(
            s,
            subject_id="user:1",
            session_id="sess-1",
            persona=None,
            query="what do you know?",
            query_vec=None,
            config=MemoryConfig(),
            tenant_id=_TENANT,
        )
    assert assembled.recalled_fact_ids, "facts vanished under the fail-closed predicate"
    assert "turn 2 for user:1" in assembled.text, (
        "the raw window read AFTER recall's commit came back empty — the scope did not "
        "survive the commit"
    )


@pytest.mark.usefixtures("fail_closed")
async def test_sweep_marks_jobs_done_under_fail_closed(db) -> None:
    """The write-path regression: the sweeper commits per job and must stay scoped.

    Before the fix every statement after the first per-job commit was unscoped, so
    fail-closed consolidation claimed nothing, wrote nothing and marked nothing DONE
    while still returning success.
    """
    async with db() as s:
        await _seed_turns(s, tenant_id=_TENANT, subject="user:1", session_id="sess-1")
        await set_tenant_scope(s, _TENANT)
        job = await enqueue_consolidation(
            s, subject_id="user:1", session_id="sess-1", tenant_id=_TENANT
        )
        job_id = job.id

    async with db() as s:
        processed = await sweep_pending(
            s,
            config=MemoryConfig(),
            complete=FakeComplete(extractions=[{"facts": []}], decisions=[]),
            embed=FakeEmbed(),
        )
    assert processed == 1

    async with db() as s:
        await set_tenant_scope(s, _TENANT)
        status = (
            await s.execute(
                select(MemoryConsolidationJob.status).where(
                    MemoryConsolidationJob.id == job_id
                )
            )
        ).scalar_one()
    assert status is ConsolidationStatus.DONE


@pytest.mark.usefixtures("fail_closed")
async def test_a_failing_job_is_recorded_and_the_sweep_carries_on(db) -> None:
    """A poisoned job must land as ERROR, under its own scope, and not stop the batch.

    Two things meet on this path. The rollback in the handler discards the GUCs, so the
    ERROR update only reaches the row because the scope is bound to the *session*; and
    the rollback also expires every ORM instance, so reading ``job.id`` there was a lazy
    refresh from inside an ``except`` block — it raised ``MissingGreenlet``, escaped the
    handler, and took down the sweep and every background task with it.
    """
    async with db() as s:
        await _seed_turns(s, tenant_id=_TENANT, subject="user:1", session_id="sess-1")
        await set_tenant_scope(s, _TENANT)
        bad = await enqueue_consolidation(
            s, subject_id="user:1", session_id="sess-1", tenant_id=_TENANT
        )
        good = await enqueue_consolidation(
            s, subject_id="user:1", session_id="sess-1", tenant_id=_TENANT
        )
        bad_id, good_id = bad.id, good.id

    class _OneBadCall(FakeComplete):
        """Fails the first job's extraction, then behaves for the second."""

        def __init__(self) -> None:
            super().__init__(extractions=[{"facts": []}], decisions=[])
            self.seen = 0

        async def __call__(self, role, messages, *, response_format=None):
            self.seen += 1
            if self.seen == 1:
                raise RuntimeError("poisoned job")
            return await super().__call__(role, messages, response_format=response_format)

    async with db() as s:
        processed = await sweep_pending(
            s, config=MemoryConfig(), complete=_OneBadCall(), embed=FakeEmbed()
        )
    assert processed == 1, "the second job never ran — one bad job stopped the batch"

    async with db() as s:
        await set_tenant_scope(s, _TENANT)
        rows = dict(
            (
                await s.execute(
                    select(
                        MemoryConsolidationJob.id, MemoryConsolidationJob.status
                    ).where(MemoryConsolidationJob.id.in_([bad_id, good_id]))
                )
            ).all()
        )
    assert rows[bad_id] is ConsolidationStatus.ERROR
    assert rows[good_id] is ConsolidationStatus.DONE


@pytest.mark.usefixtures("fail_closed")
async def test_a_tenants_memory_stays_invisible_to_another_tenant(db) -> None:
    """The guard the fix must not weaken, asserted through the policy itself.

    No app-level ``tenant_id`` predicate is used here — the statement asks for every row
    and lets Row-Level Security answer, which is the only way to see whether the policy
    is doing anything.
    """
    async with db() as s:
        await _seed_turns(s, tenant_id=_TENANT, subject="user:1", session_id="sess-1")
        await _seed_turns(s, tenant_id=_OTHER, subject="user:2", session_id="sess-2")

    async with db() as s:
        await bind_memory_scope(s, _OTHER)
        await s.commit()  # and still scoped afterwards, which is the point
        rows = (await s.execute(select(MemoryMessage))).scalars().all()
    assert rows, "tenant 2 should see its own turns"
    assert {r.tenant_id for r in rows} == {_OTHER}
