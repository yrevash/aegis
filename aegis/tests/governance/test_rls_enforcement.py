"""The serving role is subject to RLS — and the check that says so when it is not.

Why this file exists. Every policy pinned in ``test_rls.py`` was, until now, enforced
against nobody: the platform connected as ``postgres``, and PostgreSQL skips row
security **entirely** for a superuser. ``FORCE ROW LEVEL SECURITY`` does not help — it
removes the table *owner's* exemption, not the superuser one. Verified on a scratch
database: a superuser scoped to tenant 1 saw 2 of 2 rows; a non-superuser saw 1.

So the control is only real if two things hold, and both are tested here rather than
assumed:

1. The **diagnostic fires.** A previous check in this area was wrapped in a broad
   ``except`` and could never report the condition it existed to catch, so each branch
   of :func:`report_rls_enforcement` is driven here with the log record asserted —
   including the fatal branch, which must stop a process rather than warn it.
2. The **grants exist.** A serving role with no privileges is not more secure, it is
   broken; :func:`grant_serving_role` is pinned to the exact DML it issues, and to the
   two cases where it must refuse to issue any.

The live-Postgres proof that a ``NOBYPASSRLS`` role really is filtered belongs to the
database-backed suite (phase 1.4); what is checkable without a server is checked here.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from aegis.governance.rls import (
    _APPEND_ONLY_TABLES as APPEND_ONLY_TABLES,  # noqa: PLC2701 - the registry under test
)
from aegis.governance.rls import (
    SERVING_ROLE,
    RlsBypassError,
    RlsEnforcement,
    audit_rls_enforcement,
    grant_serving_role,
    report_rls_enforcement,
)

#: Stands in for "any bind that is not PostgreSQL" in the off-Postgres branches below.
#: A real dialect name rather than an invented one, and deliberately not ``sqlite``: the
#: suite carries no SQLite dependency any more and must not regrow one, not even as a
#: string that reads like an endorsement of a fixture that is gone.
_NON_POSTGRES_DIALECT = "mysql"

#: What the catalog read behind the append-only revoke answers with by default: the
#: ledgers plus one ``run_events`` partition. The partition is in the default, not an
#: opt-in, because it is the half that is easy to lose — a revoke on the parent alone
#: leaves ``DELETE FROM run_events_2026_08`` working, and that name is a published
#: function of the calendar rather than a secret.
_APPEND_ONLY_RELATIONS: tuple[str, ...] = (*APPEND_ONLY_TABLES, "run_events_2026_08")


class _FakeResult:
    """The subset of SQLAlchemy's ``Result`` these functions use."""

    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def first(self) -> tuple | None:
        return self._rows[0] if self._rows else None

    def scalar_one(self):  # noqa: ANN201 - mirrors SQLAlchemy's loose return
        return self._rows[0][0]

    def __iter__(self):  # noqa: ANN204 - a real Result yields row tuples
        return iter(self._rows)


class _FakeConn:
    """Records statements and answers the two catalog reads with canned rows."""

    def __init__(self, engine: _FakePostgresEngine) -> None:
        self._engine = engine

    async def execute(self, clause, params=None):  # noqa: ANN001, ANN201
        sql = str(clause)
        if "pg_has_role" in sql:
            return _FakeResult(self._engine.role_rows)
        if "FROM pg_roles WHERE rolname = :role" in sql:
            return _FakeResult([(1,)] if self._engine.role_exists else [])
        # Before the bare ``current_schema()`` branch: the append-only relation lookup
        # also mentions it, and answering it with the schema name would silently make
        # the revokes disappear from the grant.
        if "pg_inherits" in sql:
            return _FakeResult([(name,) for name in self._engine.append_only_relations])
        if "current_schema()" in sql:
            return _FakeResult([(self._engine.schema,)])
        self._engine.statements.append(sql)
        return _FakeResult([])

    async def __aenter__(self) -> _FakeConn:
        return self

    async def __aexit__(self, *exc) -> bool:  # noqa: ANN002
        return False


class _FakePostgresEngine:
    """Just enough of an ``AsyncEngine``: a dialect, ``connect()`` and ``begin()``."""

    def __init__(
        self,
        *,
        role: str = SERVING_ROLE,
        superuser: bool = False,
        bypassrls: bool = False,
        escalations: tuple[str, ...] = (),
        role_exists: bool = True,
        schema: str = "public",
        dialect: str = "postgresql",
        append_only_relations: tuple[str, ...] = _APPEND_ONLY_RELATIONS,
    ) -> None:
        self.statements: list[str] = []
        self.role_rows = [(role, superuser, bypassrls, list(escalations))]
        self.role_exists = role_exists
        self.schema = schema
        self.dialect = SimpleNamespace(name=dialect)
        self.append_only_relations = append_only_relations

    def connect(self) -> _FakeConn:
        return _FakeConn(self)

    def begin(self) -> _FakeConn:
        return _FakeConn(self)


# ── the audit: what the database is asked, and how the answer is read ──


async def test_audit_is_inapplicable_and_silent_off_postgres():
    """Only PostgreSQL has row security, so the question is answered without a query."""
    engine = _FakePostgresEngine(dialect=_NON_POSTGRES_DIALECT)
    enforcement = await audit_rls_enforcement(engine)
    assert not enforcement.applicable
    assert not enforcement.bypassed
    assert engine.statements == []


async def test_audit_reads_superuser_and_bypassrls_off_the_serving_connection():
    engine = _FakePostgresEngine(role="postgres", superuser=True)
    enforcement = await audit_rls_enforcement(engine)
    assert enforcement.role == "postgres"
    assert enforcement.is_superuser
    assert enforcement.bypassed
    assert enforcement.cause == "SUPERUSER"


async def test_bypassrls_alone_is_just_as_disqualifying_as_superuser():
    """A narrower grant, an identical outcome: the policies do not apply."""
    engine = _FakePostgresEngine(role="reporting", bypassrls=True)
    enforcement = await audit_rls_enforcement(engine)
    assert not enforcement.is_superuser
    assert enforcement.bypassed
    assert enforcement.cause == "BYPASSRLS"


async def test_a_plain_login_role_is_reported_as_enforced():
    enforcement = await audit_rls_enforcement(_FakePostgresEngine())
    assert enforcement.role == SERVING_ROLE
    assert not enforcement.bypassed
    assert enforcement.cause == "none"


# ── the report: the branch that must be able to fire ──


def test_a_bypassing_role_is_logged_at_error_naming_the_consequence(caplog):
    """The line has to read like an incident, not like an operational detail."""
    enforcement = RlsEnforcement(
        dialect="postgresql", role="postgres", is_superuser=True
    )
    with caplog.at_level(logging.ERROR, logger="aegis.governance.rls"):
        report_rls_enforcement(enforcement)
    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "Row-level security is INERT" in message
    assert "'postgres'" in message and "SUPERUSER" in message
    # It must say what follows from it, and how to fix it.
    assert "read another tenant's rows" in message
    assert SERVING_ROLE in message


def test_a_bypassing_role_stops_the_process_when_the_check_is_fatal(caplog):
    """The non-dev posture: refuse to serve rather than serve with isolation off."""
    enforcement = RlsEnforcement(
        dialect="postgresql", role="postgres", bypasses_rls=True
    )
    with (
        caplog.at_level(logging.CRITICAL, logger="aegis.governance.rls"),
        pytest.raises(RlsBypassError, match="Row-level security is INERT"),
    ):
        report_rls_enforcement(enforcement, fatal=True)
    assert any(r.levelno == logging.CRITICAL for r in caplog.records)


def test_membership_in_a_superuser_role_is_a_warning_not_a_pass(caplog):
    """Role attributes are not inherited — but SET ROLE is one statement away."""
    enforcement = RlsEnforcement(
        dialect="postgresql", role=SERVING_ROLE, escalatable_via=("postgres",)
    )
    with caplog.at_level(logging.DEBUG, logger="aegis.governance.rls"):
        report_rls_enforcement(enforcement, fatal=True)  # fatal, yet no raise
    levels = {r.levelno for r in caplog.records}
    assert logging.WARNING in levels
    assert logging.ERROR not in levels


def test_an_enforced_role_says_so_and_a_non_postgres_dialect_says_nothing(caplog):
    with caplog.at_level(logging.INFO, logger="aegis.governance.rls"):
        report_rls_enforcement(RlsEnforcement(dialect="postgresql", role=SERVING_ROLE))
    assert "RLS is enforced" in caplog.records[-1].getMessage()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="aegis.governance.rls"):
        report_rls_enforcement(RlsEnforcement(dialect=_NON_POSTGRES_DIALECT))
    assert caplog.records == []


# ── the grants: usable, and no more than that ──


async def test_grants_are_exactly_the_dml_the_serving_role_needs():
    engine = _FakePostgresEngine()
    statements = await grant_serving_role(engine, SERVING_ROLE)
    assert list(statements) == engine.statements
    joined = "\n".join(statements)
    assert 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "public"' in joined
    assert 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "public"' in joined
    # Future tables (a later create_all) must be covered too, or the next model added
    # becomes a permission-denied 500 inside a request.
    assert joined.count("ALTER DEFAULT PRIVILEGES") == 2
    # Schema CREATE is granted on purpose — LightRAG's Postgres KV/doc-status stores
    # create their tables as the connecting role — and it is not a way out of RLS:
    # DROP POLICY / DISABLE ROW LEVEL SECURITY need ownership of the *governed* tables,
    # which stay owned by the DDL role.
    assert f'GRANT USAGE, CREATE ON SCHEMA "public" TO "{SERVING_ROLE}"' in joined
    # Nothing that would let the serving role remove the isolation it is subject to.
    for forbidden in ("TRUNCATE", "TRIGGER", "REFERENCES", "ALL PRIVILEGES"):
        assert forbidden not in joined


async def test_the_append_only_ledgers_end_at_select_insert_not_full_dml():
    """The claim: the serving role cannot rewrite the audit trail.

    The measured symptom this replaces is ``\\dp audit_log`` reading
    ``aegis_app=arwd/postgres`` — the connection every request arrives on held UPDATE
    and DELETE on the evidence of what it did. The grant is deliberately still
    ``ON ALL TABLES``, so what makes the ledgers append-only is the revoke *after* it;
    losing that revoke is the regression this pins.
    """
    engine = _FakePostgresEngine()
    joined = "\n".join(await grant_serving_role(engine, SERVING_ROLE))
    for table in APPEND_ONLY_TABLES:
        assert (
            f'REVOKE UPDATE, DELETE ON "public"."{table}" FROM "{SERVING_ROLE}"' in joined
        )
    # Order matters and is not incidental: the bulk grant hands out UPDATE/DELETE on
    # every table in the schema, so a revoke issued before it would be undone by it.
    assert joined.index("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES") < joined.index(
        "REVOKE"
    )


async def test_the_revoke_reaches_run_events_partitions_not_only_the_parent():
    """The failure mode: revoking on the parent and calling ``run_events`` protected.

    PostgreSQL checks privileges on the relation **named in the statement**. ``run_events``
    is ``PARTITIONED BY RANGE (ts)`` and each month is a separately-addressable table that
    ``GRANT ... ON ALL TABLES`` and ``ALTER DEFAULT PRIVILEGES`` both reach, so a parent-only
    revoke leaves ``DELETE FROM run_events_2026_08`` working — with a name anyone can derive
    from a date.
    """
    engine = _FakePostgresEngine()
    joined = "\n".join(await grant_serving_role(engine, SERVING_ROLE))
    assert (
        f'REVOKE UPDATE, DELETE ON "public"."run_events_2026_08" FROM "{SERVING_ROLE}"'
        in joined
    )


async def test_tables_something_legitimately_rewrites_keep_their_dml():
    """A revoke on a mutated table is a ``permission denied`` 500 inside a request.

    Named individually rather than checked as "not in the list" because each is a
    concrete writer: the run header moves through its states, an approval decision writes
    the verdict onto the pending row, and LangGraph's ``PostgresSaver`` upserts the three
    checkpoint tables on every step under ``AGENT_CHECKPOINTER=postgres`` — where the
    failure would be a durable-execution outage rather than a visible error.
    """
    engine = _FakePostgresEngine()
    joined = "\n".join(await grant_serving_role(engine, SERVING_ROLE))
    for kept in (
        "runs",
        "approvals",
        "job_runs",
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
    ):
        assert f'"public"."{kept}"' not in joined
        assert kept not in APPEND_ONLY_TABLES


def test_memory_write_log_keeps_delete_because_erasure_is_served_from_a_request():
    """The exclusion that was found by breaking it, not by reasoning about it.

    ``memory_write_log`` reads append-only everywhere — retention documents it as the
    one memory table never swept, and consolidation supersedes rather than removes — so
    it was in the tuple. The revoke broke ``POST /v1/memory/forget``, the DPDP/GDPR
    right-to-erasure route, with ``permission denied for table memory_write_log``
    surfacing to the caller as a 503 "erasure not performed".

    It keeps ``DELETE`` because the alternative is worse: the table is keyed by
    ``subject_id``, a lawful erasure has to reach it, and the only way to keep it
    append-only would be to serve that request handler on the **owner** connection —
    putting an RLS-bypassing connection in the request path to buy a tamper control.
    Tamper-evidence for memory writes rests on ``audit_log`` instead, which is
    append-only here and records every erasure with its row counts.
    """
    assert "memory_write_log" not in APPEND_ONLY_TABLES


async def test_no_revoke_is_issued_for_a_ledger_the_schema_does_not_have_yet():
    """``REVOKE`` on a missing relation is an error, and this runs on the first boot too.

    So the statements come from a catalog read, not from :data:`_APPEND_ONLY_TABLES`
    assumed to exist. An empty answer must produce a clean grant, not a boot that dies
    on ``relation "audit_log" does not exist`` before the platform ever serves.
    """
    engine = _FakePostgresEngine(append_only_relations=())
    statements = await grant_serving_role(engine, SERVING_ROLE)
    assert statements
    assert not any(s.startswith("REVOKE") for s in statements)


async def test_grants_are_skipped_and_reported_when_the_role_is_missing(caplog):
    """A missing role is a provisioning gap, not a crash — and never a silent one."""
    engine = _FakePostgresEngine(role_exists=False)
    with caplog.at_level(logging.ERROR, logger="aegis.governance.rls"):
        assert await grant_serving_role(engine, SERVING_ROLE) == ()
    assert "does not exist" in caplog.records[-1].getMessage()
    assert engine.statements == []


async def test_a_role_name_that_is_not_an_identifier_is_refused_not_escaped(caplog):
    """GRANT takes no bind parameter for its grantee, so the name is validated first."""
    engine = _FakePostgresEngine()
    with caplog.at_level(logging.ERROR, logger="aegis.governance.rls"):
        assert await grant_serving_role(engine, 'app"; DROP TABLE users; --') == ()
    assert "not a plain SQL identifier" in caplog.records[-1].getMessage()
    assert engine.statements == []


async def test_grants_are_a_noop_off_postgres():
    engine = _FakePostgresEngine(dialect=_NON_POSTGRES_DIALECT)
    assert await grant_serving_role(engine, SERVING_ROLE) == ()
    assert engine.statements == []
