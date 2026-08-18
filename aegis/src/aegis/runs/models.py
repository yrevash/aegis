"""SQLAlchemy ORM for the durable run record — ``run_events`` and the ``runs`` header.

**This is the tracking mechanism that survives a restart, and deliberately the only
one.** Three already exist and none of them persists: OTel spans go to a collector,
Phoenix is an ephemeral deep-dive, and the SSE stream is gone the moment the socket
closes. Five later features need the same durable, tenant-scoped, replayable record —
the harness, replay, per-agent inspection, audit depth and per-tenant LLMOps evidence —
so it is built once, here, rather than five times.

The events stored are **the same ordered stream** :func:`aegis.agent.run_agent` already
emits (:mod:`aegis.agent.events` builds the payloads; the orchestrator stamps ``run_id``
and a monotonic ``seq``). Nothing is re-derived and no second vocabulary is invented, so
what a tenant reads back cannot disagree with what streamed to their browser. ``trace_id``
and ``span_id`` are carried as columns rather than buried in the payload precisely so a
row here can be reconciled with the Phoenix trace of the same run.

Two tables, and the second one has to earn its place against the one-mechanism rule:

``run_events``
    The log. Append-only, partitioned by month, the system of record.

``runs``
    A **regenerable projection** of that log — the header a console lists runs from
    without reading a million event rows. It is maintained incrementally as events are
    recorded *and* can be rebuilt from the events alone by folding the identical
    function over them (:mod:`aegis.runs.record`). If the two ever disagree, **events
    win**, and :func:`aegis.runs.record.rebuild_run_header` is what makes that
    executable rather than aspirational.

**``run_events`` is PARTITIONED BY RANGE (ts), and that is decided at creation because
it cannot be decided later.** There is no Alembic in this project by choice, and turning
a large heap table into a partitioned one is a migration, not a DDL statement. The
partitions themselves are created by :mod:`aegis.runs.partitions` — including from a
``create_all`` hook, so a schema built by any of this repo's three schema-building sites
has somewhere to put a row. Retention drops whole partitions; it never ``DELETE``s rows.

Both tables carry ``tenant_id`` and are registered in
:data:`aegis.governance.rls._TENANT_SCOPED_TABLES`. The partitions are **not** registered
individually — their names are a function of the calendar — and are covered by the
partition rule in :func:`aegis.governance.rls._plan_rls` plus the policy DDL the
partition module issues as it creates them.

Like every other module's models these register on the shared
:class:`aegis.data.AegisBase` metadata, and this module imports
:mod:`aegis.governance.models` and :mod:`aegis.jobs.models` for the same load-bearing
reason :mod:`aegis.jobs.models` imports the former: the foreign keys below name
``tenants.id``, ``users.id`` and ``job_runs.id``, and SQLAlchemy resolves those by name
against the shared metadata at mapper-configuration time. Importing this module must
therefore be sufficient for ``create_all``, rather than leaving a host to discover the
ordering by hitting ``NoReferencedTableError``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Identity, Index, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

# Registration side-effects, and deliberately not lazy imports: the foreign keys below
# reference ``tenants.id`` / ``users.id`` / ``job_runs.id``. See the module docstring.
import aegis.governance.models  # noqa: F401
import aegis.jobs.models  # noqa: F401
from aegis.core.types import RunStatus
from aegis.data import AegisBase, JsonB

__all__ = ["RUN_EVENTS_TABLE", "RUNS_TABLE", "Run", "RunEvent"]

#: The partitioned parent's name, as one constant rather than a string repeated across
#: the models, the partition DDL and the RLS registry.
RUN_EVENTS_TABLE = "run_events"

#: The header projection's table name, for the same reason.
RUNS_TABLE = "runs"

#: The one Postgres ``run_status`` enum type, shared by the header column below and any
#: later table that records a run's terminal state. Declared once and named explicitly,
#: matching :data:`aegis.jobs.models._JOB_STATUS`: two ``SAEnum`` instances carrying one
#: type name leave it to ``create_all``'s memoisation whether ``CREATE TYPE`` is emitted
#: once or twice, and ``runstatus`` — SQLAlchemy's derived default — is not a name a
#: migration could be written against with confidence.
_RUN_STATUS = SAEnum(RunStatus, name="run_status")


class RunEvent(AegisBase):
    """One event from one run, exactly as it streamed.

    The primary key is ``(id, ts)`` rather than ``id`` alone because PostgreSQL requires
    every unique constraint on a partitioned table to contain the partition key. That is
    not a compromise: ``ts`` is what routes the row to a partition, so a key without it
    could not identify a row without scanning every partition either.

    ``seq`` is the orchestrator's monotonic per-run counter and is what the header fold
    and any replay order by — **not** ``ts``, which is a wall clock and can tie or go
    backwards. It is stored rather than recomputed so an event's position in its run
    survives a partial read.

    Where the specification was silent, the choices made here and why:

    * ``event_type`` is ``text``, not an enum. The vocabulary is
      :mod:`aegis.agent.events`, which Phase 5 extends with multi-agent events; a
      Postgres enum would turn "the agent emits a new event type" into a ``CREATE TYPE``
      migration in a project that deliberately has no migration tool, and an unknown
      type must be *stored* and shown rather than rejected — an event the record layer
      does not recognise is exactly the one an operator needs to see.
    * ``tenant_id`` is nullable, matching every other governed record: a platform-level
      run (an eval sweep, an operator probe) has no owning tenant. Under the
      ``tenant_isolation`` policy ``NULL = <scope>`` is NULL rather than true, so such a
      row is invisible to every tenant, which is the intended reading.
    * ``job_id`` is a real FK to ``job_runs`` because a run triggered by a durable job
      must be joinable to it; ``agent_id`` is a plain string because the agent roster is
      code, not a table.
    """

    __tablename__ = RUN_EVENTS_TABLE

    # ``Identity()`` rather than ``autoincrement=True``: on PostgreSQL the two are
    # the same generated key (``GENERATED BY DEFAULT AS IDENTITY`` is the standard
    # spelling of ``bigserial``), but SQLAlchemy refuses to compile an explicit
    # autoincrement on a **composite** primary key for SQLite, and this table's key
    # has to be composite because Postgres requires the partition key in it. Identity
    # keeps the portable unit schema materialisable — as an ordinary, unpartitioned
    # table, which is the honest rendering of this design on a database that has
    # neither partitions nor row security.
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    # Part of the primary key *and* the partition key. The server default is what makes
    # "now" mean the database's clock rather than any one worker's.
    ts: Mapped[datetime] = mapped_column(primary_key=True, server_default=func.now())
    run_id: Mapped[str] = mapped_column(String(64))
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"))
    seq: Mapped[int]
    event_type: Mapped[str] = mapped_column(String(64))
    agent_id: Mapped[str | None] = mapped_column(String(64), default=None)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("job_runs.id"), default=None)
    trace_id: Mapped[str | None] = mapped_column(String(64), default=None)
    span_id: Mapped[str | None] = mapped_column(String(64), default=None)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)

    __table_args__ = (
        # The one query this table exists to serve — "replay run X in order" — and the
        # one a tenant's run list drives off. Declared explicitly rather than as
        # ``index=True`` per column because a composite index is what serves it; on a
        # partitioned parent PostgreSQL creates the matching index on every partition,
        # including ones created later.
        Index("ix_run_events_run_seq", "run_id", "seq"),
        Index("ix_run_events_tenant_ts", "tenant_id", "ts"),
        Index("ix_run_events_trace", "trace_id"),
        # The irreversible decision, made at creation because it cannot be made later.
        # Ignored on every other dialect, so the SQLite unit schema is a plain table.
        {"postgresql_partition_by": "RANGE (ts)"},
    )


class Run(AegisBase):
    """The regenerable header of one run — a projection, never a second source of truth.

    Every column here is a fold over that run's events and nothing else, which is what
    lets :func:`aegis.runs.record.rebuild_run_header` recompute the whole row and what
    makes "events win" a testable statement rather than a policy. Nothing writes a field
    here that it did not first write as an event.

    ``run_id`` is the primary key because it is the orchestrator-independent identity of
    a run and the only key any other surface (the SSE stream, Phoenix, the harness
    record) knows it by; a surrogate id would add a second name for the same thing.

    Where the specification was silent:

    * ``status`` is nullable and carries **no default**, because a run in flight has no
      terminal status and ``NULL`` says exactly that where any default would claim an
      outcome the run has not reached.
    * ``duration_ms`` is the sum of the ``node_finished`` durations, matching
      :func:`aegis.agent.harness.run_summary`, not wall-clock ``finished_at -
      started_at`` — the two differ whenever a run parks at a human approval gate, and
      the node sum is the one that means "work done".
    * ``cost_usd`` is a plain float for the same reason :class:`aegis.jobs.models.JobRun`
      uses one: it is an attribution figure reconciled against ``usage_ledger``, not a
      billed amount of money.
    """

    __tablename__ = RUNS_TABLE

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    agent_id: Mapped[str | None] = mapped_column(String(64), default=None)
    trace_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    status: Mapped[RunStatus | None] = mapped_column(_RUN_STATUS, default=None, index=True)
    started_at: Mapped[datetime | None] = mapped_column(default=None, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    duration_ms: Mapped[int] = mapped_column(default=0)
    prompt_tokens: Mapped[int] = mapped_column(default=0)
    completion_tokens: Mapped[int] = mapped_column(default=0)
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    cache_hit: Mapped[bool] = mapped_column(default=False)
    event_count: Mapped[int] = mapped_column(default=0)
    last_seq: Mapped[int] = mapped_column(default=-1)
    node_count: Mapped[int] = mapped_column(default=0)
    tool_call_count: Mapped[int] = mapped_column(default=0)
    approval_count: Mapped[int] = mapped_column(default=0)
    guardrail_block_count: Mapped[int] = mapped_column(default=0)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
