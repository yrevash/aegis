"""The instrument the ``RLS_FAIL_CLOSED`` flip depends on (§9.5).

Flipping the posture turns every path nobody enumerated into a silent zero-row result,
which is worse than the fail-open it replaces because an empty screen gets blamed on the
data. So the enumeration has to come first, and the enumeration is produced by
:func:`aegis.governance.rls.install_scope_auditor` rather than by reading the code — the
whole point being that reading the code is what missed these paths in the first place.

An instrument that reports nothing is indistinguishable from a clean codebase, so what is
pinned here is not "it can log" but the two ways it could quietly stop meaning anything:

* it must report a read that bound **nothing** and stay quiet for one that bound a scope
  — the two halves are asserted against the same table and the same statement, so a
  detector that simply never fires fails the first and a detector that always fires fails
  the second;
* it must **forget** a binding at the end of its transaction. ``set_tenant_scope`` writes
  the GUC with ``is_local => true``, so Postgres discards it on commit; a tracker that
  outlived the GUC would report a connection as scoped while the database considers it
  unscoped, and the enumeration would read empty for exactly the paths that matter — the
  long-running ones that commit and keep working.

Run against a real PostgreSQL over the suite's ``NOSUPERUSER NOBYPASSRLS`` role, because
the auditor is only installed on PostgreSQL engines and a fake would prove nothing about
which events SQLAlchemy actually emits.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from aegis.governance.rls import (
    UnscopedReadError,
    install_scope_auditor,
    reset_scope_audit,
    scope_audit_findings,
    set_tenant_scope,
)

#: A registered tenant-scoped table, read directly so the assertion is about the auditor
#: rather than about any ORM mapping.
_TABLE = "audit_log"


@pytest.fixture(autouse=True)
def _clean_findings():
    """Clear the process-wide finding registry around every test in this module."""
    reset_scope_audit()
    yield
    reset_scope_audit()


async def test_an_unscoped_read_is_named_with_its_table_and_its_caller(
    pg_engine: AsyncEngine,
):
    """The positive half: a read that bound nothing is reported, with the line to fix.

    The caller matters as much as the table. A warning that says "something read
    audit_log unscoped" is a puzzle; one that says "from tests/.../test_scope_auditor.py:
    NN" is a task, and the difference decides whether an enumeration gets finished.
    """
    install_scope_auditor(pg_engine)
    async with pg_engine.connect() as conn:
        await conn.execute(text(f'SELECT count(*) FROM "{_TABLE}"'))

    findings = scope_audit_findings()
    assert [f.table for f in findings] == [_TABLE], findings
    assert "test_scope_auditor.py" in findings[0].caller
    assert _TABLE in findings[0].statement


async def test_a_bound_scope_is_not_reported(pg_engine: AsyncEngine):
    """The negative half: the identical read, with a scope bound, is silent.

    Paired with the test above deliberately. Together they are the mutation: a detector
    that never fires fails the first, a detector that always fires fails this one, and no
    single change to the predicate can satisfy both by accident.
    """
    install_scope_auditor(pg_engine)
    maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with maker() as session:
        await set_tenant_scope(session, 4242)
        await session.execute(text(f'SELECT count(*) FROM "{_TABLE}"'))
        await session.rollback()

    assert scope_audit_findings() == ()


async def test_the_tracker_forgets_the_binding_when_the_transaction_ends(
    pg_engine: AsyncEngine,
):
    """A committed scope is gone in the database, and must be gone in the auditor too.

    This is the failure that would make the enumeration lie in the most damaging place.
    ``set_config(..., is_local => true)`` is discarded at commit, so an activity that
    commits mid-run and keeps working is unscoped from that point on — the exact case
    ``aegis.jobs.scope`` re-binds on every transaction to survive. If the auditor's own
    tracker did not expire with it, that reader would be recorded as scoped and would
    never appear in the list of things to fix.
    """
    install_scope_auditor(pg_engine)
    maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with maker() as session:
        await set_tenant_scope(session, 4242)
        await session.execute(text(f'SELECT count(*) FROM "{_TABLE}"'))
        assert scope_audit_findings() == ()

        await session.commit()
        await session.execute(text(f'SELECT count(*) FROM "{_TABLE}"'))
        await session.rollback()

    assert [f.table for f in scope_audit_findings()] == [_TABLE], (
        "the read after the commit was recorded as scoped, but the GUC it relied on was "
        "discarded by that commit — the auditor is reporting a scope the database no "
        "longer has"
    )


async def test_strict_mode_raises_so_a_suite_can_assert_the_enumeration_is_empty(
    pg_engine: AsyncEngine,
):
    """A warning nobody greps is how an enumeration gets declared finished too early."""
    install_scope_auditor(pg_engine, strict=True)
    with pytest.raises(UnscopedReadError) as caught:
        async with pg_engine.connect() as conn:
            await conn.execute(text(f'SELECT count(*) FROM "{_TABLE}"'))
    assert _TABLE in str(caught.value)
    assert "RLS_FAIL_CLOSED" in str(caught.value)
