"""Additive schema reconciliation — the guard on a silently unwritable usage ledger.

The regression this pins is a SECURITY one, not a tidiness one. ``audio_seconds`` and
``images`` were added to :class:`aegis.governance.models.UsageLedger` with the
``ALTER TABLE`` written only in a docstring, and there is no Alembic here:
``create_all`` never alters an existing table. So on every database created before
that commit the ledger INSERT raised ``UndefinedColumn`` — which
``aegis.gateway.llm._record_usage`` swallows **by design**, because usage recording
must never break a live call. The row vanished, per-tenant spend attribution stopped,
and the USD caps that are computed by summing those rows stopped binding, with nothing
anywhere saying so.

Three halves, pinned separately because they fail in different ways: the *plan* (pure,
database-free), the *DDL actually emitted* for a database that has drifted (recorded
against a fake PostgreSQL connection, since manufacturing drift means lying about the
catalog), and the *no-op* against the real, undrifted cluster the suite runs on — which
is the case every startup takes and the one a fake cannot vouch for.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import Column, Float, Integer, MetaData, String, Table

import aegis.governance.models  # noqa: F401 - registers the governance tables
from aegis.data import AegisBase
from aegis.governance import (
    SchemaDriftError,
    declared_enum_labels,
    plan_additive_columns,
    plan_enum_values,
    reconcile_additive_columns,
    reconcile_enum_values,
)

LEDGER = AegisBase.metadata.tables["usage_ledger"]


def _live_columns_missing(*missing: str) -> set[tuple[str, str]]:
    """The ledger as it exists in a database that predates ``missing``."""
    return {
        ("usage_ledger", column.name)
        for column in LEDGER.columns
        if column.name not in missing
    }


# ── the plan (pure) ─────────────────────────────────────────────────────────


def test_the_columns_the_live_ledger_lacks_are_planned_for_addition():
    """The exact live-Postgres drift: a ledger with neither non-token unit column."""
    existing = _live_columns_missing("audio_seconds", "images")

    addable, unsafe = plan_additive_columns(existing, [AegisBase.metadata])

    assert unsafe == []
    assert {f"{c.table.name}.{c.name}" for c in addable} == {
        "usage_ledger.audio_seconds",
        "usage_ledger.images",
    }


def test_a_database_already_in_step_plans_nothing():
    """Idempotent: a second run over a reconciled database finds no work."""
    existing = _live_columns_missing()

    addable, unsafe = plan_additive_columns(existing, [AegisBase.metadata])

    assert addable == [] and unsafe == []


def test_a_table_that_does_not_exist_yet_is_left_to_create_all():
    """A brand-new table is ``create_all``'s job, not a column-by-column ALTER."""
    addable, unsafe = plan_additive_columns(set(), [AegisBase.metadata])

    assert addable == [] and unsafe == []


def test_a_not_null_column_with_no_server_default_is_never_added_silently():
    """No correct value exists for the rows already there — a human must decide."""
    metadata = MetaData()
    table = Table(
        "widgets",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("owner", String(32), nullable=False),  # no server_default
    )
    existing = {("widgets", "id")}

    addable, unsafe = plan_additive_columns(existing, [metadata])

    assert addable == []
    assert [c.name for c in unsafe] == ["owner"]
    assert table.name == "widgets"


def test_a_nullable_column_is_addable_even_without_a_default():
    metadata = MetaData()
    Table(
        "widgets",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("note", String(32), nullable=True),
        Column("score", Float, nullable=False, server_default="0"),
    )

    addable, unsafe = plan_additive_columns({("widgets", "id")}, [metadata])

    assert unsafe == []
    assert [c.name for c in addable] == ["note", "score"]


# ── the DDL, recorded against a fake PostgreSQL connection ──────────────────


class _FakeResult(list):
    """A minimal stand-in for a SQLAlchemy result: iterable rows."""


class _FakePgConn:
    """Just enough of an AsyncConnection: a dialect, ``execute``, and canned rows."""

    def __init__(self, existing: set[tuple[str, str]]) -> None:
        from sqlalchemy.dialects import postgresql

        self.dialect = postgresql.dialect()
        self.statements: list[str] = []
        self._existing = existing

    async def execute(self, clause, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        text = str(clause)
        if "information_schema.columns" in text:
            return _FakeResult(sorted(self._existing))
        self.statements.append(text)
        return None


async def test_the_missing_ledger_columns_are_actually_added():
    """The emitted DDL installs both columns, defaulted, and is re-run-safe."""
    conn = _FakePgConn(_live_columns_missing("audio_seconds", "images"))

    added = await reconcile_additive_columns(conn, [AegisBase.metadata])

    assert added == ["usage_ledger.audio_seconds", "usage_ledger.images"]
    assert len(conn.statements) == 2
    joined = " | ".join(conn.statements)
    assert 'ALTER TABLE "usage_ledger" ADD COLUMN IF NOT EXISTS audio_seconds' in joined
    assert 'ALTER TABLE "usage_ledger" ADD COLUMN IF NOT EXISTS images' in joined
    # Defaulted, so the rows already in the table get a value rather than NULL.
    assert joined.count("DEFAULT") == 2
    assert joined.count("NOT NULL") == 2


async def test_reconciling_an_up_to_date_database_emits_no_ddl():
    conn = _FakePgConn(_live_columns_missing())

    assert await reconcile_additive_columns(conn, [AegisBase.metadata]) == []
    assert conn.statements == []


async def test_irreconcilable_drift_raises_instead_of_being_skipped():
    """SECURITY: a ledger that cannot be made writable must be loud, not swallowed.

    The alternative to failing here is a running system whose spend caps silently do
    not bind, which is precisely the defect this module exists to end.
    """
    metadata = MetaData()
    Table(
        "usage_ledger",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("mandatory", String(8), nullable=False),
    )
    conn = _FakePgConn({("usage_ledger", "id")})

    with pytest.raises(SchemaDriftError) as excinfo:
        await reconcile_additive_columns(conn, [metadata])

    assert "usage_ledger.mandatory" in str(excinfo.value)
    # Nothing was applied — the refusal happens before any DDL is emitted.
    assert conn.statements == []


async def test_an_index_declared_on_a_newly_added_column_is_created_with_it():
    """An added column is never left half-installed (its own index comes too)."""
    metadata = MetaData()
    Table(
        "usage_ledger",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("trace_id", String(64), nullable=True, index=True),
    )
    conn = _FakePgConn({("usage_ledger", "id")})

    added = await reconcile_additive_columns(conn, [metadata])

    assert added == ["usage_ledger.trace_id"]
    assert any("CREATE INDEX" in s for s in conn.statements)


# ── an undrifted database is left alone ─────────────────────────────────────


async def test_reconcile_is_a_clean_noop_on_a_freshly_created_schema(pg_owner_engine):
    """Startup's own path: a database whose ``create_all`` just ran needs no ALTER.

    Run against the real cluster (over the owning role, since reconciliation is DDL),
    so the ``information_schema`` query, the type comparison and the emptiness of the
    plan are all the genuine article. The previous version of this test asked the same
    question of a throwaway SQLite file, where ``information_schema`` does not exist and
    the function returned at the dialect check without looking at anything — it could not
    have caught a reconciler that mis-read the catalog, because it never let one read it.
    """
    async with pg_owner_engine.begin() as conn:
        assert await reconcile_additive_columns(conn, [AegisBase.metadata]) == []


async def test_reconcile_returns_immediately_on_a_non_postgres_dialect():
    """The dialect check happens before any information_schema query is attempted."""

    class _Exploding:
        dialect = SimpleNamespace(name="mysql")

        async def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise AssertionError("no SQL may be emitted on a non-Postgres dialect")

    assert await reconcile_additive_columns(_Exploding(), [AegisBase.metadata]) == []


# ─────────────────────────────────────────────────────────────────────────────
# Native enum labels — the same failure, one type system further along
# ─────────────────────────────────────────────────────────────────────────────
#
# ``create_all`` emits ``CREATE TYPE`` exactly once, on the boot that first creates it,
# and never again. So ``RunStatus.REJECTED`` — added so a run a human REFUSED stops being
# indistinguishable from one that was approved and did the work — existed in Python and
# in no database built before it, and the first write of it fails with ``invalid input
# value for enum``. For ``run_status`` that write is the ``runs`` header, i.e. the row a
# console lists runs from and the row analytics reconcile against.


def test_the_declared_labels_are_the_names_postgres_actually_stores():
    """``run_status`` holds ``'REJECTED'``, not ``'rejected'``, and the plan must agree.

    SQLAlchemy stores a Python enum's *names* by default, which is why the live type's
    labels are upper-case. A reconciler that planned against ``.value`` would emit
    ``ADD VALUE 'rejected'`` and leave the database with five labels, one of them
    unreachable and the real one still missing.
    """
    labels = declared_enum_labels([AegisBase.metadata])["run_status"]
    assert labels == ("COMPLETED", "BLOCKED", "AWAITING_APPROVAL", "REJECTED", "ERROR")


def test_a_missing_label_is_planned_into_its_declared_position():
    """``REJECTED`` goes *before* ``ERROR``, not on the end.

    ``ALTER TYPE … ADD VALUE`` appends by default, so a database upgraded into the new
    member would sort it last while a database built fresh sorts it fourth — two
    databases from one source disagreeing about ``ORDER BY status``.
    """
    live = {"run_status": ("COMPLETED", "BLOCKED", "AWAITING_APPROVAL", "ERROR")}
    declared = declared_enum_labels([AegisBase.metadata])
    additions, extraneous = plan_enum_values(live, {"run_status": declared["run_status"]})
    assert additions == [("run_status", "REJECTED", "ERROR")]
    assert extraneous == []


def test_a_label_only_the_database_has_is_reported_and_never_dropped():
    """PostgreSQL cannot drop an enum label, and rows may still carry it.

    Reporting rather than removing is the only honest option; planning a removal would
    be planning a statement that does not exist.
    """
    live = {"run_status": ("COMPLETED", "BLOCKED", "AWAITING_APPROVAL", "ERROR", "LEGACY")}
    declared = declared_enum_labels([AegisBase.metadata])
    additions, extraneous = plan_enum_values(live, {"run_status": declared["run_status"]})
    assert ("run_status", "LEGACY") in extraneous
    assert all(label != "LEGACY" for _type, label, _before in additions)


def test_a_type_the_database_does_not_have_is_left_to_create_all():
    """No type, no drift: ``create_all`` is about to emit it in full."""
    assert plan_enum_values({}, {"run_status": ("COMPLETED", "REJECTED")}) == ([], [])


def test_an_already_current_type_plans_nothing():
    """Idempotence, which is what makes this safe to run on every boot."""
    declared = declared_enum_labels([AegisBase.metadata])
    assert plan_enum_values(declared, declared) == ([], [])


async def test_enum_reconcile_returns_immediately_on_a_non_postgres_dialect():
    """SQLite renders these as VARCHAR + CHECK, so there is no type to reconcile."""

    class _Exploding:
        dialect = SimpleNamespace(name="sqlite")

        async def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise AssertionError("no SQL may be emitted on a non-Postgres dialect")

    assert await reconcile_enum_values(_Exploding(), [AegisBase.metadata]) == []


async def test_enum_reconcile_is_a_no_op_against_the_real_cluster(pg_owner_engine):
    """The case every startup after the first one takes, asked of a real PostgreSQL."""
    async with pg_owner_engine.connect() as conn:
        engaged = await conn.execution_options(isolation_level="AUTOCOMMIT")
        assert await reconcile_enum_values(engaged, [AegisBase.metadata]) == []
