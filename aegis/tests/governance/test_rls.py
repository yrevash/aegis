"""RLS helpers are Postgres-only, cover every tenant-scoped table, and say when they don't.

Three things are pinned here:

1. Both helpers are clean no-ops on a bind whose dialect is not PostgreSQL — not merely
   "they do not raise", but "they emit no SQL at all", which is the stronger claim the
   source's ``if bind.dialect.name != "postgresql": return`` guards actually make.
2. The Postgres DDL ``bootstrap_rls`` emits — the whole security control — recorded
   against a fake engine that also answers the catalog query the bootstrap now plans
   from, so the fake exercises the real planning path rather than a shortcut.
3. The coverage diagnostics. ``_plan_rls``/``_unprotected`` are pure, so the branch that
   reports an unprotected tenant-scoped table is tested by *making it fire*, log record
   and all — a diagnostic nobody has watched fire is a diagnostic that does not work.

A live-Postgres test of the policies' runtime behaviour is a separate, database-backed
concern (phase 1.4); what is unit-testable without a server is pinned here.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from aegis.governance import bootstrap_rls, set_tenant_scope
from aegis.governance.rls import (
    _TENANT_SCOPED_TABLES,
    _LiveTable,
    _plan_rls,
    _report_plan,
    _unprotected,
    tenant_policy_statements,
)

#: The dialect these two tests stand a non-Postgres bind up as. Any non-``postgresql``
#: name exercises the same guard; ``mysql`` is used because it is a real SQLAlchemy
#: dialect name and because the suite no longer has — and must never regrow — a SQLite
#: dependency just to prove that a guard returns early.
_NON_POSTGRES_DIALECT = "mysql"


class _ExplodingSession:
    """A session whose bind is not PostgreSQL and which refuses to execute anything.

    The old version of these tests opened a real aiosqlite engine and asserted only that
    the call did not raise — which a helper that quietly issued a ``SET`` would also have
    satisfied. Making ``execute`` itself the failure is both stricter and cheaper.
    """

    def __init__(self, dialect: str = _NON_POSTGRES_DIALECT) -> None:
        self._bind = SimpleNamespace(dialect=SimpleNamespace(name=dialect))

    def get_bind(self) -> SimpleNamespace:
        return self._bind

    async def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        raise AssertionError("no SQL may be emitted on a non-Postgres bind")


async def test_set_tenant_scope_emits_nothing_on_a_non_postgres_bind():
    session = _ExplodingSession()
    # Neither a scoped nor an unscoped call touches the connection.
    await set_tenant_scope(session, 7)
    await set_tenant_scope(session, None)


async def test_bootstrap_rls_is_a_noop_on_a_non_postgres_engine():
    class _ExplodingEngine:
        dialect = SimpleNamespace(name=_NON_POSTGRES_DIALECT)

        def begin(self):  # noqa: ANN201
            raise AssertionError("no connection may be opened on a non-Postgres engine")

    # No policy DDL is emitted on a non-Postgres dialect; it simply returns.
    assert await bootstrap_rls(_ExplodingEngine()) == []


# ── the Postgres DDL itself (recorded against a fake postgresql engine) ──
#
# The unit suite has no Postgres, but the *statements* bootstrap_rls emits are the
# whole security control, so they are pinned here directly. Without FORCE the policy
# is inert for the table owner — which is the role this application connects as, since
# ``app.data.session.bootstrap`` creates the tables and then serves every request on
# the very same engine.


def _catalog_row(
    name: str,
    *,
    tenant_type: str | None = "integer",
    row_security: bool = False,
    force: bool = False,
    has_policy: bool = False,
    partition_root: str | None = None,
) -> tuple:
    """One row shaped like ``_LIVE_TABLES_SQL`` returns it.

    The trailing ``partition_root`` column arrived with ``run_events``: a partition is
    governed through its parent, so the planner has to be able to tell one from a table
    nobody registered. ``None`` means "not a partition", which is what the catalog
    returns for every relation that is not one.
    """
    return (name, tenant_type, row_security, force, has_policy, partition_root)


class _RecordingConn:
    """Captures executed SQL and answers the catalog query from a canned schema.

    The catalog answer flips to "fully protected" once the DDL has run, so the
    bootstrap's own read-back verification passes exactly when the DDL really was
    emitted — the fake cannot make a broken bootstrap look healthy.
    """

    def __init__(self, statements: list[str], schema: list[tuple]) -> None:
        self._statements = statements
        self._schema = schema

    async def execute(self, clause, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        sql = str(clause)
        if "pg_class" in sql:
            done = {
                s.split('"')[1]
                for s in self._statements
                if s.startswith("CREATE POLICY tenant_isolation")
            }
            return [
                _catalog_row(
                    name,
                    tenant_type=tenant_type,
                    row_security=row_security or name in done,
                    force=force or name in done,
                    has_policy=has_policy or name in done,
                    partition_root=partition_root,
                )
                for (
                    name,
                    tenant_type,
                    row_security,
                    force,
                    has_policy,
                    partition_root,
                ) in self._schema
            ]
        self._statements.append(sql)
        return None


class _FakeBegin:
    def __init__(self, statements: list[str], schema: list[tuple]) -> None:
        self._statements = statements
        self._schema = schema

    async def __aenter__(self) -> _RecordingConn:
        return _RecordingConn(self._statements, self._schema)

    async def __aexit__(self, *exc) -> bool:  # noqa: ANN002
        return False


class _FakePostgresEngine:
    """Just enough of an AsyncEngine for ``bootstrap_rls``: a dialect and ``begin()``."""

    def __init__(self, schema: list[tuple] | None = None) -> None:
        self.statements: list[str] = []
        self.schema = schema if schema is not None else [
            _catalog_row(name) for name in _TENANT_SCOPED_TABLES
        ]
        self.dialect = SimpleNamespace(name="postgresql")

    def begin(self) -> _FakeBegin:
        return _FakeBegin(self.statements, self.schema)


async def _bootstrap_statements() -> list[str]:
    engine = _FakePostgresEngine()
    await bootstrap_rls(engine)
    return engine.statements


async def test_every_registered_tenant_scoped_table_is_covered():
    """The registry is the contract: each of its tables must get all four statements."""
    engine = _FakePostgresEngine()
    protected = await bootstrap_rls(engine)
    assert sorted(protected) == sorted(_TENANT_SCOPED_TABLES)
    for table in _TENANT_SCOPED_TABLES:
        assert f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY' in engine.statements
        assert f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY' in engine.statements
        assert f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"' in engine.statements


async def test_registry_covers_the_governance_memory_ops_and_host_tables():
    """A rename or an accidental deletion in the registry is a silent isolation loss."""
    assert set(_TENANT_SCOPED_TABLES) == {
        # aegis.governance.models
        "audit_log",
        "budgets",
        "usage_ledger",
        "users",
        # aegis.memory.stores
        "memory_consolidation_job",
        "memory_fact",
        "memory_message",
        "memory_profile",
        "memory_session",
        "memory_write_log",
        # aegis.ops.models
        "eval_results",
        "prompt_versions",
        # aegis.jobs.models
        "documents",
        "job_runs",
        # aegis.runs.models
        "run_events",
        "runs",
        # aegis.settings.models
        "settings",
        # host-owned
        "approvals",
    }
    # ``tenants`` is keyed by ``id`` and ``chunks`` carries no tenant column, so a
    # policy on either would not compile — they are correctly absent.
    assert "tenants" not in _TENANT_SCOPED_TABLES
    assert "chunks" not in _TENANT_SCOPED_TABLES


async def test_bootstrap_forces_rls_on_every_tenant_scoped_table():
    """ENABLE alone leaves the owner exempt; FORCE is what makes the policy real."""
    statements = await _bootstrap_statements()
    for table in _TENANT_SCOPED_TABLES:
        assert f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY' in statements
        assert f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY' in statements


async def test_force_is_issued_after_enable_for_each_table():
    statements = await _bootstrap_statements()
    for table in _TENANT_SCOPED_TABLES:
        enable = statements.index(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        force = statements.index(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        assert enable < force


async def test_policy_is_recreated_idempotently_with_a_safe_predicate():
    statements = await _bootstrap_statements()
    policies = [s for s in statements if s.startswith("CREATE POLICY tenant_isolation")]
    assert len(policies) == len(_TENANT_SCOPED_TABLES)
    for policy in policies:
        # A bound numeric scope restricts rows to that tenant …
        assert "tenant_id = substring(current_setting('app.tenant_id', true)" in policy
        # … and the predicate can never raise on a non-numeric GUC (no bare ''::int).
        assert "NULLIF(current_setting('app.tenant_id', true), '')::int" not in policy
        assert "IS NULL" in policy
    drops = sum(s.startswith("DROP POLICY IF EXISTS tenant_isolation") for s in statements)
    assert drops == len(_TENANT_SCOPED_TABLES)


async def test_rerunning_the_bootstrap_emits_the_identical_statements():
    """It runs on every startup, so the second run must be the same no-op DDL."""
    first = await _bootstrap_statements()
    second = await _bootstrap_statements()
    assert first == second


# ── the coverage diagnostics ──
#
# The point of planning from the catalog is to notice a tenant-scoped table nobody
# registered. That branch is only worth having if it fires, so each case below drives it.


def test_plan_protects_only_registered_integer_tenant_tables():
    live = [
        _LiveTable("users", "integer", False, False, False),
        _LiveTable("tenants", None, False, False, False),
    ]
    plan = _plan_rls(live, registered=("users",))
    assert plan.protect == ("users",)
    assert plan.unregistered == ()
    assert plan.unsupported == ()
    assert plan.stale == ()
    assert plan.absent == ()


def test_plan_reports_a_tenant_scoped_table_nobody_registered():
    live = [
        _LiveTable("users", "integer", False, False, False),
        _LiveTable("shadow_notes", "bigint", False, False, False),
    ]
    plan = _plan_rls(live, registered=("users",))
    # It is reported, and deliberately NOT quietly protected — see _plan_rls.
    assert plan.unregistered == ("shadow_notes",)
    assert plan.protect == ("users",)


def test_plan_reports_a_non_integer_tenant_column_instead_of_emitting_bad_ddl():
    live = [_LiveTable("legacy_docs", "uuid", False, False, False)]
    plan = _plan_rls(live, registered=("legacy_docs",))
    assert plan.unsupported == (("legacy_docs", "uuid"),)
    assert plan.protect == ()


def test_plan_reports_a_registry_entry_whose_tenant_column_is_gone():
    live = [_LiveTable("budgets", None, False, False, False)]
    plan = _plan_rls(live, registered=("budgets",))
    assert plan.stale == ("budgets",)
    assert plan.protect == ()


def test_plan_skips_registered_tables_this_database_does_not_have():
    """A host that installs only some aegis modules must still boot."""
    live = [_LiveTable("users", "integer", False, False, False)]
    plan = _plan_rls(live, registered=("users", "memory_fact"))
    assert plan.protect == ("users",)
    assert plan.absent == ("memory_fact",)


# ── partitions, and the tables whose NULL-tenant row is a baseline ────────────
#
# Two shapes the registry cannot express by name. A partition's name is a function of
# the calendar, so it is governed through its parent; and ``settings`` holds the platform
# baseline as a NULL-tenant row that every tenant must read and none may write.


def test_a_partition_of_a_registered_table_is_protected_without_its_own_registry_line():
    """Its name is a function of the calendar, so a registry line per month would rot.

    It still needs the DDL: PostgreSQL applies a parent's policies only to rows reached
    *through* the parent, so a partition queried by name is filtered by its own policies
    and by nothing else.
    """
    live = [
        _LiveTable("run_events", "integer", False, False, False),
        _LiveTable("run_events_2026_08", "integer", False, False, False, "run_events"),
    ]
    plan = _plan_rls(live, registered=("run_events",))
    assert plan.protect == ("run_events", "run_events_2026_08")
    assert plan.unregistered == ()


def test_a_partition_of_an_unregistered_table_is_reported_once_through_its_parent():
    """One line naming the fix beats one line per month of history."""
    live = [
        _LiveTable("shadow_events", "integer", False, False, False),
        _LiveTable("shadow_events_2026_08", "integer", False, False, False, "shadow_events"),
    ]
    plan = _plan_rls(live, registered=())
    assert plan.unregistered == ("shadow_events",)
    assert plan.protect == ()


def test_a_baseline_table_widens_the_read_and_pins_the_write():
    """Without the explicit WITH CHECK, any tenant could forge a platform default.

    Postgres reuses ``USING`` for writes when no ``WITH CHECK`` is given, so widening the
    read to the NULL-tenant baseline row would widen the write to it as well.
    """
    statements = tenant_policy_statements("settings")
    policy = statements[-1]
    using, check = policy.split(" WITH CHECK ")
    assert "OR tenant_id IS NULL" in using
    assert "OR tenant_id IS NULL" not in check


def test_an_ordinary_table_gets_no_with_check_so_postgres_reuses_using():
    statements = tenant_policy_statements("job_runs")
    assert "WITH CHECK" not in statements[-1]
    assert statements[-1].endswith("::int)")


def test_a_partition_takes_its_parents_flavour_of_policy():
    """What a relation's rows *mean* is a property of its parent, not of its name."""
    partition = tenant_policy_statements("settings_2026_08", policy_for="settings")
    assert "WITH CHECK" in partition[-1]
    orphan = tenant_policy_statements("settings_2026_08")
    assert "WITH CHECK" not in orphan[-1]


def test_the_policy_builder_refuses_a_name_it_would_have_to_escape():
    """Names reach the DDL from the registry and the catalog, and are still validated."""
    with pytest.raises(ValueError, match="not a plain SQL identifier"):
        tenant_policy_statements('run_events"; DROP TABLE users; --')


def test_unprotected_flags_a_policy_that_exists_but_is_not_forced():
    """ENABLE + policy without FORCE is the exact shape that fooled everyone before."""
    live = [
        _LiveTable("users", "integer", True, True, True),
        _LiveTable("approvals", "integer", True, False, True),
        _LiveTable("budgets", "integer", True, True, False),
    ]
    assert _unprotected(live, ("users", "approvals", "budgets")) == (
        "approvals",
        "budgets",
    )


def test_unprotected_is_empty_when_every_table_is_fully_covered():
    live = [_LiveTable("users", "integer", True, True, True)]
    assert _unprotected(live, ("users",)) == ()


@pytest.mark.parametrize(
    ("plan_kwargs", "expected_level", "expected_fragment"),
    [
        ({"unregistered": ("shadow_notes",)}, logging.WARNING, "not in"),
        ({"unsupported": (("legacy_docs", "uuid"),)}, logging.WARNING, "legacy_docs (uuid)"),
        ({"stale": ("budgets",)}, logging.WARNING, "stale"),
        ({"absent": ("memory_fact",)}, logging.INFO, "not in this database"),
    ],
)
def test_report_plan_actually_logs_each_gap(
    caplog, plan_kwargs, expected_level, expected_fragment
):
    """Drive every reporting branch — a warning path that cannot fire is not a warning."""
    from aegis.governance.rls import _RlsPlan

    empty = {
        "protect": (),
        "unregistered": (),
        "unsupported": (),
        "stale": (),
        "absent": (),
    }
    with caplog.at_level(logging.INFO, logger="aegis.governance.rls"):
        _report_plan(_RlsPlan(**{**empty, **plan_kwargs}))
    records = [r for r in caplog.records if r.name == "aegis.governance.rls"]
    assert len(records) == 1
    assert records[0].levelno == expected_level
    assert expected_fragment in records[0].getMessage()


def test_report_plan_is_silent_when_coverage_is_complete(caplog):
    """The healthy boot must not log, or the gap warnings become background noise."""
    from aegis.governance.rls import _RlsPlan

    plan = _RlsPlan(
        protect=("users",), unregistered=(), unsupported=(), stale=(), absent=()
    )
    with caplog.at_level(logging.DEBUG, logger="aegis.governance.rls"):
        _report_plan(plan)
    assert [r for r in caplog.records if r.name == "aegis.governance.rls"] == []


async def test_bootstrap_reports_a_shortfall_it_reads_back_from_the_catalog(caplog):
    """If the DDL does not take, the read-back says so instead of returning quietly."""

    class _DeafConn(_RecordingConn):
        """Records the DDL but reports the catalog as permanently unprotected."""

        async def execute(self, clause, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            sql = str(clause)
            if "pg_class" in sql:
                return [_catalog_row(name) for name, *_ in self._schema]
            self._statements.append(sql)
            return None

    class _DeafEngine(_FakePostgresEngine):
        def begin(self):  # noqa: ANN201
            engine = self

            class _Begin:
                async def __aenter__(self) -> _DeafConn:
                    return _DeafConn(engine.statements, engine.schema)

                async def __aexit__(self, *exc) -> bool:  # noqa: ANN002
                    return False

            return _Begin()

    engine = _DeafEngine(schema=[_catalog_row("users")])
    with caplog.at_level(logging.ERROR, logger="aegis.governance.rls"):
        assert await bootstrap_rls(engine) == ["users"]
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "users" in errors[0].getMessage()
    assert "FORCE" in errors[0].getMessage()
