"""The run record's tables: registered for RLS, partitioned, and importable alone.

The registration check is the one that stops the failure this module's docstring is
about — a table with a ``tenant_id`` column that nobody added to
:data:`aegis.governance.rls._TENANT_SCOPED_TABLES` looks governed from the outside and is
not.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import Table, text

from aegis.data import AegisBase
from aegis.governance.rls import _POLICY_NAME, _TENANT_COLUMN, _TENANT_SCOPED_TABLES
from aegis.runs.models import RUN_EVENTS_TABLE, RUNS_TABLE, Run, RunEvent

_SRC = str(Path(__file__).resolve().parents[2] / "src")


def _runs_tables() -> list[Table]:
    """Return the tables :mod:`aegis.runs.models` maps."""
    return [Run.__table__, RunEvent.__table__]


def test_every_table_this_module_maps_is_tenant_scoped_and_registered():
    """An unregistered tenant table looks governed from outside and is not."""
    tables = _runs_tables()
    assert {table.name for table in tables} == {RUNS_TABLE, RUN_EVENTS_TABLE}
    for table in tables:
        assert _TENANT_COLUMN in table.c, f"{table.name} carries no {_TENANT_COLUMN}"
        assert table.name in _TENANT_SCOPED_TABLES, (
            f"{table.name} has a {_TENANT_COLUMN} column but is not in "
            "_TENANT_SCOPED_TABLES, so bootstrap_rls installs no policy on it"
        )


def test_run_events_declares_the_partitioning_that_cannot_be_added_later():
    """The one irreversible decision, asserted where it is made.

    A heap table does not become a partitioned one without a migration, and this project
    has no migration tool — so if this ever stops being in the DDL, it stops being
    possible.
    """
    assert RunEvent.__table__.dialect_kwargs.get("postgresql_partition_by") == "RANGE (ts)"
    # The partition key has to be in every unique constraint, which is why the primary
    # key is composite rather than the bare ``id`` a log table would otherwise have.
    assert [column.name for column in RunEvent.__table__.primary_key] == ["id", "ts"]


async def test_bootstrap_installed_the_tenant_policy_on_both_run_tables(pg_owner_engine):
    """Read the policy back out of the catalog, not out of the DDL we hoped was emitted."""
    async with pg_owner_engine.connect() as conn:
        rows = {
            name: (enabled, forced)
            for name, enabled, forced in (
                await conn.execute(
                    text(
                        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "JOIN pg_policy p ON p.polrelid = c.oid "
                        "WHERE n.nspname = current_schema() AND p.polname = :policy"
                    ),
                    {"policy": _POLICY_NAME},
                )
            ).all()
        }
    for name in (RUNS_TABLE, RUN_EVENTS_TABLE):
        assert rows.get(name) == (True, True), (
            f"{name} has no enabled, FORCEd {_POLICY_NAME} policy: {rows.get(name)}"
        )
    partitions = [name for name in rows if name.startswith(f"{RUN_EVENTS_TABLE}_")]
    assert partitions, (
        "no partition of run_events carries the policy; a partition queried by name is "
        "filtered by its own policies and by nothing else"
    )


def test_importing_the_run_record_pulls_the_orm_and_no_execution_stack():
    """The module contract: the core owns the record, the host runs the work.

    ``temporalio`` is installed in this environment, so a stray import would work
    perfectly and be noticed by nobody until someone installed ``aegis`` without it.
    Importing the models must also be sufficient for ``create_all`` — the foreign keys
    name ``tenants``, ``users`` and ``job_runs`` — and must register the partition hook,
    without which the table rejects every write.
    """
    code = (
        "import sys; import aegis.runs.models; "
        "assert 'sqlalchemy' in sys.modules; "
        "from aegis.data import AegisBase; "
        "tables = set(AegisBase.metadata.tables); "
        "missing = {'run_events', 'runs', 'tenants', 'users', 'job_runs'} - tables; "
        "assert not missing, missing; "
        "from sqlalchemy import event; "
        "from aegis.runs.models import RunEvent; "
        "assert event.contains(RunEvent.__table__, 'after_create', "
        "  sys.modules['aegis.runs.partitions']._create_initial_partitions); "
        "banned = {'temporalio', 'fastapi', 'litellm', 'torch', 'langgraph', 'neo4j'}; "
        "hit = banned & set(sys.modules); assert not hit, hit"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": _SRC},
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_header_carries_only_fields_a_fold_over_events_can_produce():
    """The projection's contract, as a column list.

    Every column here is derived by :func:`aegis.runs.record.apply_event` except the
    three identities a run has before any event does. A column nobody folds is a field
    that cannot be regenerated, which would quietly end the "events win" guarantee.
    """
    from aegis.runs.record import RunHeader

    columns = {column.name for column in Run.__table__.columns}
    folded = {field for field in RunHeader.__dataclass_fields__}
    assert columns == folded, (
        "the runs table and the folded header have drifted apart: "
        f"{columns ^ folded}"
    )


def test_both_tables_register_on_the_shared_metadata():
    """A second metadata would mean a host's ``create_all`` silently skipped them."""
    for name in (RUNS_TABLE, RUN_EVENTS_TABLE):
        assert name in AegisBase.metadata.tables
