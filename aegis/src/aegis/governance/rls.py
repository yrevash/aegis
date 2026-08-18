"""Postgres Row-Level Security — per-tenant isolation for the governed tables.

Four pieces, all **Postgres-only** (a clean no-op on SQLite, so the unit tests run
without a database):

- :func:`set_tenant_scope` — bind ``app.tenant_id`` for a session's connection so the
  bootstrapped RLS policies engage for the request.
- :data:`_TENANT_SCOPED_TABLES` — the **one** registry of tables that carry a
  ``tenant_id`` column and must therefore be governed by a policy. A new tenant-scoped
  table is registered by adding one line there and nowhere else. A *partition* of a
  registered table is the single exception: it is governed through its parent, because
  its name is a function of the calendar rather than of the schema.
- :func:`tenant_policy_statements` — the policy DDL as data, so the bootstrap and the
  monthly ``run_events`` partitions install the identical thing.
- :func:`bootstrap_rls` — enable **and force** RLS, install the ``tenant_isolation``
  policy on every registered table the live schema actually has, and then read the
  catalog back to *report* every tenant-scoped table it could not protect. The policy
  restricts every request that binds a tenant scope; a request that binds none (the
  login lookup, the platform-admin listings) is not restricted — the reasoning and the
  follow-up that would close it are documented on :data:`_TENANT_ISOLATION_PREDICATE`.
- :func:`audit_rls_enforcement` / :func:`report_rls_enforcement` — ask the database
  whether the role serving requests is *subject to* those policies at all, and say so
  loudly when it is not. :func:`grant_serving_role` is the other half: the owner
  connection hands that role exactly the DML it needs, and nothing else.

The catalog read-back exists because the failure mode of this module is *silence*: a
table that grows a ``tenant_id`` column without a matching registry line looks exactly
like a protected one from the outside, and the leak only shows up when a tenant reads
another tenant's rows. Coverage is therefore never assumed — it is measured against
``pg_class``/``pg_policy`` on every boot and logged when it falls short.

The serving-role audit exists for the sharper version of the same failure. Postgres
skips row security **entirely** for a superuser and for any role with ``BYPASSRLS``;
``FORCE ROW LEVEL SECURITY`` removes the table *owner's* exemption, not that one. A
platform that connects as ``postgres`` therefore installs thirteen perfect policies and
is filtered by none of them, while every catalog check above still reads healthy. That
was true of this codebase until the split into an owner/DDL connection and a
``NOSUPERUSER NOBYPASSRLS`` serving connection (see :func:`audit_rls_enforcement`), and
it is why bypass is now a property of the *connection* rather than something
application code is trusted to avoid.

App-level ``WHERE tenant_id = :ctx`` scoping (in :mod:`aegis.governance.enforcement`) is
the belt-and-suspenders layer over these DB-enforced policies — and the only layer on
SQLite.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "SERVING_ROLE",
    "RlsBypassError",
    "RlsEnforcement",
    "audit_rls_enforcement",
    "bootstrap_rls",
    "grant_serving_role",
    "report_rls_enforcement",
    "set_tenant_scope",
    "tenant_policy_statements",
]

logger = logging.getLogger(__name__)

#: Name of the per-tenant discriminator column. A table that has it is tenant-scoped;
#: a table that does not (``tenants``, keyed by ``id``; ``chunks``, whose isolation is
#: the per-tenant vector-store namespace plus its ``meta`` payload) is not, and gets no
#: policy — a policy referencing a column that does not exist would not compile.
_TENANT_COLUMN = "tenant_id"

#: The single policy name this module owns, on every table it governs. Fixed (rather
#: than per-table) so ``DROP POLICY IF EXISTS`` can make the bootstrap idempotent and so
#: the catalog read-back can recognise its own work.
_POLICY_NAME = "tenant_isolation"

#: ``tenant_id`` column types the predicate can compare against. The predicate casts the
#: bound GUC with ``::int``, so a ``text``/``uuid`` discriminator would make
#: ``CREATE POLICY`` fail its type check at bootstrap and take the process down. Such a
#: table is reported as uncovered instead — a loud gap beats a crash loop, and beats
#: quietly pretending the table is governed.
_INTEGER_TENANT_TYPES = frozenset({"smallint", "integer", "bigint"})

#: Every table that carries a ``tenant_id`` column and therefore gets a Postgres
#: Row-Level Security policy filtering on the ``app.tenant_id`` GUC. **This is the one
#: place to register a new tenant-scoped table** — add its name here and
#: :func:`bootstrap_rls` covers it on the next boot. Forgetting to is not silent:
#: the boot-time catalog read-back logs any live table with a ``tenant_id`` column that
#: is missing from this tuple (see :func:`_plan_rls`).
#:
#: Grouped by the module whose declarative metadata owns the table. ``approvals`` is
#: host-owned (``app.data.models``, the agent HITL inbox) rather than an ``aegis``
#: table, but RLS is bootstrapped once for the whole database against a single engine,
#: so it is registered here alongside the rest rather than in a second, scattered list.
#:
#: A registered table that the live database does not have is skipped, not an error: a
#: host may install only some of the aegis modules, and ``ALTER TABLE`` on a table its
#: ``create_all`` never made would turn "this host does not use memory" into a failed
#: boot.
_TENANT_SCOPED_TABLES: tuple[str, ...] = (
    # aegis.governance.models — tenancy, quota and audit
    "audit_log",
    "budgets",
    "usage_ledger",
    "users",
    # aegis.memory.stores — durable per-subject conversational memory
    "memory_consolidation_job",
    "memory_fact",
    "memory_message",
    "memory_profile",
    "memory_session",
    "memory_write_log",
    # aegis.ops.models — the LLM-Ops eval results and prompt registry
    "eval_results",
    "prompt_versions",
    # aegis.jobs.models — the durable job substrate's system of record
    "documents",
    "job_runs",
    # aegis.runs.models — the durable, replayable per-run record. ``run_events`` is
    # PARTITIONED BY RANGE (ts); its monthly partitions are not registered here (their
    # names are a function of the calendar) and are covered by the partition rule in
    # :func:`_plan_rls` instead.
    "run_events",
    "runs",
    # aegis.settings.models — the per-tenant settings catalogue. Listed in
    # :data:`_PLATFORM_BASELINE_TABLES` as well: a NULL-tenant row here is the platform
    # baseline every tenant is entitled to *read*, and none may write.
    "settings",
    # host-owned (app.data.models) — the durable agent approvals inbox
    "approvals",
)

#: Tables where a row with a NULL ``tenant_id`` is a **platform baseline every tenant
#: must be able to read**, rather than a platform-private record.
#:
#: The distinction is real and it decides a policy predicate. For ``job_runs`` a
#: NULL-tenant row is a platform-level job that no tenant should see, so the standard
#: predicate — under which ``NULL = <scope>`` is NULL, i.e. not visible — is exactly
#: right. For ``settings`` the same predicate would be a correctness bug: the platform
#: default *is* a NULL-tenant row, and a resolver that could not see it would compute a
#: value **weaker than the platform's own choice** while looking healthy. That is the
#: precise failure mode :data:`aegis.settings.spec.MergeRule.TIGHTEN_ONLY` exists to
#: make impossible, so it may not be reintroduced by the visibility layer.
#:
#: The read is widened; the write is **not**. A table listed here gets a policy with an
#: explicit ``WITH CHECK`` carrying the *unwidened* predicate, so a tenant-scoped
#: request can read the baseline row and is refused when it tries to write one — without
#: the explicit check, Postgres would reuse the widened ``USING`` clause for writes and
#: any tenant could forge a platform default by inserting a NULL-tenant row.
_PLATFORM_BASELINE_TABLES: frozenset[str] = frozenset({"settings"})

#: A relation name this module is willing to interpolate into DDL. Names reach the DDL
#: builders from :data:`_TENANT_SCOPED_TABLES` and from the live catalog, never from a
#: request — but ``CREATE POLICY`` takes no bind parameter for its target, so the name is
#: interpolated, and anything interpolated is validated rather than trusted. Deliberately
#: narrower than Postgres allows: a name that would need escaping is refused, not escaped.
_SAFE_RELATION_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")


async def set_tenant_scope(session: AsyncSession, tenant_id: int | None) -> None:
    """Bind ``app.tenant_id`` for the session's connection so RLS policies apply.

    Applied inside the governed data-layer calls — the usage ledger, budget reads,
    user/usage listings, the memory read/write paths, and the approvals inbox — so the
    bootstrapped per-tenant RLS policies engage on Postgres for every governed request.
    The app-level ``WHERE tenant_id = :ctx`` scoping remains the belt-and-suspenders
    layer over these DB-enforced policies (and the only layer on SQLite).

    RLS is **Postgres-only**: this calls ``set_config('app.tenant_id', <id>, true)`` on
    PostgreSQL, writing the **empty string** when the request is unscoped; on SQLite (the
    test database) it does nothing, since RLS and session GUCs are Postgres-only. See the
    numbered comment in the body for why ``set_config`` rather than ``SET``/``RESET``, and
    note that the empty string is not inert: it makes the policy predicate's ``substring``
    yield NULL, which is the deliberate fail-open branch documented on
    :data:`_TENANT_ISOLATION_PREDICATE`.

    Args:
        session: The async session whose connection to pin.
        tenant_id: The tenant to scope to, or ``None`` to clear the scope.
    """
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # ``set_config(name, value, is_local)`` rather than ``SET``/``RESET``, for two
    # independent reasons:
    #
    # 1. CORRECTNESS. ``SET app.tenant_id = :tid`` is not executable: Postgres' ``SET``
    #    takes a literal, so sending it with a bind parameter over the extended query
    #    protocol raises ``PostgresSyntaxError: syntax error at or near "$1"``. Every
    #    tenant-scoped call path on Postgres therefore failed. It went unnoticed because
    #    the suite runs SQLite, which returns at the dialect check three lines above.
    #    ``set_config`` is a normal function call, so it parameterises correctly — and
    #    parameterising matters: the value must never be interpolated into SQL.
    #
    # 2. ISOLATION. ``is_local=true`` scopes the GUC to the current transaction, so it
    #    is discarded on commit/rollback. Session-level ``SET`` persists for the life of
    #    the *connection*, which a pool then hands to the next request — leaking one
    #    tenant's scope into another tenant's query.
    if tenant_id is None:
        await session.execute(text("SELECT set_config('app.tenant_id', '', true)"))
    else:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )


#: The row-visibility predicate installed as the ``tenant_isolation`` policy.
#:
#: ``substring(<guc> from '^[0-9]+$')`` yields the bound tenant id, or SQL NULL when
#: the GUC is unset, empty, or anything other than digits. That shape is chosen so the
#: expression can never raise: a bare ``''::int`` cast would error, and Postgres gives
#: no evaluation-order guarantee that would let an ``OR`` guard protect it.
#:
#: Semantics:
#:   * a **numeric** scope is bound → a row is visible only when ``tenant_id`` equals
#:     it. This is the isolation the policy exists for, and it now genuinely applies
#:     (see the FORCE note in :func:`bootstrap_rls`).
#:   * **no** numeric scope is bound (unset GUC, or the empty string that
#:     :func:`set_tenant_scope` writes for an unscoped/platform-admin request) → the
#:     policy does not restrict.
#:
#: That second branch is a deliberate, documented choice, not an oversight. The host's
#: authentication path reads ``users`` by username *before* any tenant is known, and
#: the platform-admin surfaces list across every tenant; under a fail-closed unset
#: branch, FORCE would make both return zero rows — login included. Closing it
#: requires the host to bind a scope on those paths (a ``SECURITY DEFINER`` login
#: lookup), which is a change outside this module. Note this is strictly *more*
#: enforcement than before, not less: without FORCE the policy was inert for the owning
#: role in every case.
_TENANT_ISOLATION_PREDICATE = (
    "(substring(current_setting('app.tenant_id', true) from '^[0-9]+$') IS NULL "
    "OR tenant_id = substring(current_setting('app.tenant_id', true) "
    "from '^[0-9]+$')::int)"
)

#: The read predicate for a :data:`_PLATFORM_BASELINE_TABLES` table: the standard one,
#: widened by the NULL-tenant baseline row. Used for ``USING`` only — see that registry
#: for why the ``WITH CHECK`` half stays unwidened.
_PLATFORM_BASELINE_PREDICATE = (
    f"({_TENANT_ISOLATION_PREDICATE} OR {_TENANT_COLUMN} IS NULL)"
)


def tenant_policy_statements(table: str, *, policy_for: str | None = None) -> tuple[str, ...]:
    """Return the DDL that puts one table under the ``tenant_isolation`` policy.

    Pure and synchronous **so there is one definition of "governed" rather than two**.
    :func:`bootstrap_rls` executes these against every registered table; the monthly
    partitions of ``run_events`` are created outside a bootstrap (at ``create_all``
    time, and again whenever the retention job rolls the next month forward) and execute
    the same statements for themselves — see :mod:`aegis.runs.partitions`. A partition
    needs its own copy because Postgres applies the *parent's* policies only to rows
    reached *through* the parent: a query naming ``run_events_2026_08`` directly is
    filtered by that partition's own policies and by nothing else, which was verified
    against a live cluster before this function existed.

    The statements are idempotent — ``ENABLE``/``FORCE`` on an already-enabled table are
    no-ops and the policy is dropped with ``IF EXISTS`` before being recreated — because
    both callers run on every boot.

    Args:
        table: The relation to govern — for a partition, the partition's own name.
            Validated against :data:`_SAFE_RELATION_NAME` rather than escaped, because it
            is interpolated into DDL that takes no bind parameters.
        policy_for: The table whose *flavour* of policy to install, when that is not
            ``table`` itself. A partition inherits its parent's flavour: which predicate
            a relation gets is a property of what its rows mean, and a partition's rows
            mean whatever its parent's do.

    Returns:
        The ordered ``ALTER TABLE``/``CREATE POLICY`` statements, ready to execute on a
        PostgreSQL connection.

    Raises:
        ValueError: If ``table`` is not a plain, unquoted SQL identifier.
    """
    if not _SAFE_RELATION_NAME.match(table):
        raise ValueError(
            f"refusing to build RLS DDL for {table!r}: not a plain SQL identifier"
        )
    if (policy_for or table) in _PLATFORM_BASELINE_TABLES:
        # Read widened to the platform baseline row, write deliberately not — a tenant
        # that could write a NULL-tenant row could forge a platform default.
        policy = (
            f"CREATE POLICY {_POLICY_NAME} ON \"{table}\" "
            f"USING {_PLATFORM_BASELINE_PREDICATE} "
            f"WITH CHECK {_TENANT_ISOLATION_PREDICATE}"
        )
    else:
        # No explicit WITH CHECK: Postgres reuses USING for writes, so an INSERT/UPDATE
        # stamping another tenant is rejected rather than merely hidden afterwards.
        policy = (
            f"CREATE POLICY {_POLICY_NAME} ON \"{table}\" "
            f"USING {_TENANT_ISOLATION_PREDICATE}"
        )
    return (
        f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY',
        # Without this the owner — i.e. the application's own role whenever no
        # owner/serving split is configured — bypasses every policy below.
        f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY',
        f'DROP POLICY IF EXISTS {_POLICY_NAME} ON "{table}"',
        policy,
    )


#: Reads every base table in the connection's current schema, together with the three
#: facts that decide whether it is governed: the type of its ``tenant_id`` column (NULL
#: when it has none), whether RLS is enabled *and forced*, and whether this module's
#: policy is installed. One catalog round-trip, used both to plan the DDL and to verify
#: it afterwards.
#:
#: ``pg_catalog`` rather than ``information_schema`` because only the former exposes
#: ``relrowsecurity``/``relforcerowsecurity`` — and FORCE is the load-bearing half (see
#: :func:`bootstrap_rls`), so a check that could not see it would pass on a table whose
#: policy is enforced against nobody. ``relkind IN ('r', 'p')`` keeps views, sequences
#: and materialised views out: ``ALTER TABLE ... ENABLE ROW LEVEL SECURITY`` is invalid
#: on them.
_LIVE_TABLES_SQL = """
SELECT c.relname AS table_name,
       (SELECT format_type(a.atttypid, a.atttypmod)
          FROM pg_attribute a
         WHERE a.attrelid = c.oid
           AND a.attname = :tenant_column
           AND a.attnum > 0
           AND NOT a.attisdropped) AS tenant_type,
       c.relrowsecurity AS row_security,
       c.relforcerowsecurity AS force_row_security,
       EXISTS (
           SELECT 1 FROM pg_policy p
            WHERE p.polrelid = c.oid AND p.polname = :policy
       ) AS has_policy,
       CASE WHEN c.relispartition
            THEN pg_partition_root(c.oid)::regclass::text
       END AS partition_root
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = current_schema()
   AND c.relkind IN ('r', 'p')
 ORDER BY c.relname
"""


@dataclass(frozen=True, slots=True)
class _LiveTable:
    """One base table as the catalog reports it — the input to every decision here.

    Attributes:
        name: The relation name in the current schema.
        tenant_type: The SQL type of its ``tenant_id`` column, or ``None`` when the
            table has no such column (i.e. it is not tenant-scoped).
        row_security: Whether ``ENABLE ROW LEVEL SECURITY`` is in effect.
        force_row_security: Whether ``FORCE ROW LEVEL SECURITY`` is in effect. Tracked
            separately because ENABLE alone exempts the table's owner — which is the
            role the application serves as whenever no owner/serving split is
            configured, and remains the DDL role's exemption even when one is.
        has_policy: Whether this module's :data:`_POLICY_NAME` policy exists on it.
        partition_root: The name of the top-level partitioned table this relation is a
            partition of, or ``None`` when it is not a partition. Tracked because a
            partition is **not** a separately registerable table — its name is a
            function of the calendar — and yet it needs a policy of its own; see
            :func:`_plan_rls`.
    """

    name: str
    tenant_type: str | None
    row_security: bool
    force_row_security: bool
    has_policy: bool
    partition_root: str | None = None

    @property
    def is_partition(self) -> bool:
        """Whether this relation is a partition of another table."""
        return self.partition_root is not None

    @property
    def is_tenant_scoped(self) -> bool:
        """Whether the table carries a ``tenant_id`` column at all."""
        return self.tenant_type is not None

    @property
    def is_protectable(self) -> bool:
        """Whether :data:`_TENANT_ISOLATION_PREDICATE` can legally reference it."""
        return self.tenant_type in _INTEGER_TENANT_TYPES

    @property
    def is_protected(self) -> bool:
        """Whether the policy is installed *and* actually enforced against the owner."""
        return self.row_security and self.force_row_security and self.has_policy


@dataclass(frozen=True, slots=True)
class _RlsPlan:
    """What :func:`bootstrap_rls` will do, and every way coverage falls short.

    Attributes:
        protect: Registered tables, present in the live schema with an integer
            ``tenant_id`` — the tables that get the DDL — plus every live partition of
            one of them, which is registered through its parent (see the body).
        unregistered: Live tables with a usable ``tenant_id`` column that no one added
            to :data:`_TENANT_SCOPED_TABLES`. **Uncovered**, and the reason this plan
            is computed rather than assumed.
        unsupported: ``(table, type)`` for live tables whose ``tenant_id`` is not an
            integer type. **Uncovered** — the predicate cannot compare against them.
        stale: Registered tables that exist but have *lost* their ``tenant_id`` column,
            so the registry entry is now wrong.
        absent: Registered tables this database does not have. Expected for a host
            that installs only some aegis modules; reported, not treated as a fault.
    """

    protect: tuple[str, ...]
    unregistered: tuple[str, ...]
    unsupported: tuple[tuple[str, str], ...]
    stale: tuple[str, ...]
    absent: tuple[str, ...]


def _plan_rls(
    live: Iterable[_LiveTable],
    registered: Iterable[str] = _TENANT_SCOPED_TABLES,
) -> _RlsPlan:
    """Classify the live schema against the registry — pure, so it is testable offline.

    Deliberately does **not** auto-protect an unregistered tenant-scoped table. A policy
    quietly conjured for a table nobody declared would repair the symptom and hide the
    omission: the warning would be ignored because nothing appeared to be wrong, and the
    registry — the thing a reader consults to learn what is governed — would drift
    further from reality every release. The gap is reported instead, with the exact
    one-line fix, so it is closed by a human in the place that documents it.

    Args:
        live: Every base table in the current schema, from :func:`_live_tables`.
        registered: The tenant-scoped table registry; overridable for tests.

    Returns:
        The :class:`_RlsPlan` describing the DDL to emit and the gaps to report.
    """
    wanted = set(registered)
    by_name = {table.name: table for table in live}
    protect: list[str] = []
    unregistered: list[str] = []
    unsupported: list[tuple[str, str]] = []
    stale: list[str] = []
    for name in sorted(by_name):
        table = by_name[name]
        if not table.is_tenant_scoped:
            # Only interesting when the registry claims otherwise — a renamed or
            # dropped column would otherwise leave a dead entry nobody notices.
            if name in wanted and not table.is_partition:
                stale.append(name)
            continue
        if not table.is_protectable:
            unsupported.append((name, str(table.tenant_type)))
            continue
        if table.is_partition:
            # A partition is governed by its parent's registration, never its own: its
            # name is a function of the calendar, so requiring a registry line per month
            # would guarantee the registry falls behind the schema. It still gets the
            # DDL, because Postgres applies the parent's policies only to rows reached
            # *through* the parent — a query naming the partition directly is filtered
            # by the partition's own policies and by nothing else. A partition of an
            # *unregistered* parent is not reported here: the parent is, once, with the
            # one-line fix, instead of once per month of history.
            if table.partition_root in wanted:
                protect.append(name)
            continue
        if name not in wanted:
            unregistered.append(name)
            continue
        protect.append(name)
    return _RlsPlan(
        protect=tuple(protect),
        unregistered=tuple(unregistered),
        unsupported=tuple(unsupported),
        stale=tuple(stale),
        absent=tuple(sorted(wanted - by_name.keys())),
    )


def _unprotected(live: Iterable[_LiveTable], expected: Iterable[str]) -> tuple[str, ...]:
    """Return the tables that were meant to be protected but read back as not.

    The post-condition of :func:`bootstrap_rls`, kept pure so the *reporting* path can
    be tested for real rather than assumed to work. Both halves matter: a missing policy
    means no filtering at all, and a policy without FORCE means no filtering *for the
    owning role* — the role the application serves as when no owner/serving split is
    configured, and the DDL role's standing exemption when one is.

    Args:
        live: The catalog re-read after the DDL ran.
        expected: The table names the DDL claimed to have protected.

    Returns:
        The subset that is still not fully protected, sorted. Empty is the healthy case.
    """
    wanted = set(expected)
    return tuple(
        sorted(
            table.name
            for table in live
            if table.name in wanted and not table.is_protected
        )
    )


async def _live_tables(conn: Any) -> list[_LiveTable]:  # noqa: ANN401 - AsyncConnection
    """Read every base table in the current schema plus its RLS state.

    Args:
        conn: An open async connection (PostgreSQL).

    Returns:
        One :class:`_LiveTable` per base table, ordered by name.
    """
    result = await conn.execute(
        text(_LIVE_TABLES_SQL),
        {"tenant_column": _TENANT_COLUMN, "policy": _POLICY_NAME},
    )
    return [
        _LiveTable(
            name=row[0],
            tenant_type=row[1],
            row_security=bool(row[2]),
            force_row_security=bool(row[3]),
            has_policy=bool(row[4]),
            partition_root=row[5],
        )
        for row in result
    ]


def _report_plan(plan: _RlsPlan) -> None:
    """Log every way the live schema's tenant coverage falls short of the registry.

    Each branch names the tables *and* the consequence, because a warning that only
    says "not covered" gets triaged as noise. Nothing here is wrapped in a ``try`` and
    nothing is conditional on a debug flag: this is the only signal that an unprotected
    tenant-scoped table exists, and a diagnostic that cannot fire is worse than none.

    Args:
        plan: The plan produced by :func:`_plan_rls`.
    """
    if plan.unregistered:
        logger.warning(
            "RLS coverage gap: live table(s) %s carry a '%s' column but are not in "
            "aegis.governance.rls._TENANT_SCOPED_TABLES, so no '%s' policy was "
            "installed — a request that binds a tenant scope can read and write every "
            "tenant's rows there. Fix: add each name to that tuple.",
            ", ".join(plan.unregistered),
            _TENANT_COLUMN,
            _POLICY_NAME,
        )
    if plan.unsupported:
        logger.warning(
            "RLS coverage gap: %s — the '%s' column is not an integer type, and the "
            "tenant_isolation predicate compares against an integer, so no policy "
            "could be installed. These tables are NOT isolated by the database; only "
            "the app-level WHERE tenant_id scoping protects them.",
            ", ".join(f"{name} ({sql_type})" for name, sql_type in plan.unsupported),
            _TENANT_COLUMN,
        )
    if plan.stale:
        logger.warning(
            "RLS registry is stale: %s registered as tenant-scoped but the live "
            "table has no '%s' column, so no policy was installed. Either the column "
            "was dropped or the registry entry is wrong.",
            ", ".join(plan.stale),
            _TENANT_COLUMN,
        )
    if plan.absent:
        logger.info(
            "RLS: registered table(s) %s are not in this database, so nothing was "
            "installed on them (expected when the host does not create that module's "
            "tables).",
            ", ".join(plan.absent),
        )


async def bootstrap_rls(engine: AsyncEngine) -> list[str]:
    """Enable + **force** Row-Level Security and install the per-tenant policy.

    Postgres-only and idempotent — it is called on every startup, and a second run
    re-issues exactly the same statements to a database that is already in the target
    state (``ENABLE``/``FORCE`` on an already-enabled table are no-ops, and the policy
    is dropped with ``IF EXISTS`` before being recreated).

    The tables come from :data:`_TENANT_SCOPED_TABLES`, intersected with what the live
    catalog actually has (see the registry's own note on why an absent table is skipped
    rather than fatal). Each one gets:

    1. ``ENABLE ROW LEVEL SECURITY`` — turns policies on for non-owning roles.
    2. ``FORCE ROW LEVEL SECURITY`` — Postgres exempts a table's *owner* from its own
       RLS policies unless FORCE is issued. That mattered absolutely when one engine
       created the tables and then served every request as their owner (the shape this
       codebase had), and it still matters in two live cases: a host running without the
       owner/serving DSN split, and any deployment whose DDL role is a non-superuser
       owner. It is necessary and **not sufficient** — FORCE cannot touch the superuser
       and ``BYPASSRLS`` exemptions, which is what :func:`audit_rls_enforcement` exists
       to catch and what the two-connection split in ``app.data.session`` removes.
    3. The ``tenant_isolation`` policy itself
       (:data:`_TENANT_ISOLATION_PREDICATE`), matching a row's ``tenant_id`` against
       the ``app.tenant_id`` GUC bound per request by :func:`set_tenant_scope`.

    The DDL is built by :func:`tenant_policy_statements`, which the partition machinery
    in :mod:`aegis.runs.partitions` also calls — one definition of "governed", not two.

    For all but the :data:`_PLATFORM_BASELINE_TABLES` the policy is created without an
    explicit ``WITH CHECK``, so Postgres reuses the ``USING`` predicate for writes: under
    a bound tenant scope an INSERT/UPDATE that would stamp a *different* tenant is
    rejected by the database, not merely hidden. A baseline table widens the read to the
    NULL-tenant row and pins the write with an explicit, unwidened ``WITH CHECK``.

    Live **partitions** of a registered table are protected too, under the parent's
    flavour of the policy. They are deliberately not registry entries: their names are a
    function of the calendar. See :func:`_plan_rls`.

    Coverage is then **measured, not assumed**. The catalog is read once before the DDL
    to plan it and report the tables that cannot be covered (:func:`_report_plan`), and
    once after to confirm that every table this call claimed to protect really does
    carry an enabled, forced policy. Both reads happen inside the same transaction as
    the DDL, so the second sees this call's own uncommitted work. A shortfall is logged
    at ERROR rather than raised: the tables already protected should stay protected, and
    refusing to boot would take the whole host down over a partial security control that
    the operator can still see and fix.

    A no-op on other dialects (SQLite has neither RLS nor session GUCs).

    Args:
        engine: The async engine to configure.

    Returns:
        The table names this call installed the policy on, sorted — empty on a
        non-PostgreSQL dialect.
    """
    if engine.dialect.name != "postgresql":
        return []
    async with engine.begin() as conn:
        live = await _live_tables(conn)
        plan = _plan_rls(live)
        _report_plan(plan)
        # A partition takes its parent's flavour of policy; every other table is its own
        # flavour. Built from the same catalog read that produced the plan, so the two
        # cannot disagree about what a relation is.
        roots = {table.name: table.partition_root or table.name for table in live}
        for table in plan.protect:
            # Identifiers come from our own registry, filtered through the catalog —
            # never from user input, and validated again inside the DDL builder.
            for statement in tenant_policy_statements(
                table, policy_for=roots.get(table, table)
            ):
                await conn.execute(text(statement))
        shortfall = _unprotected(await _live_tables(conn), plan.protect)
    if shortfall:
        logger.error(
            "RLS bootstrap did not take effect on %s: the '%s' policy is missing, or "
            "RLS is enabled but not FORCEd (which exempts the owning role — the role "
            "requests are served as when no owner/serving split is configured). These "
            "tables are NOT isolated by the database.",
            ", ".join(shortfall),
            _POLICY_NAME,
        )
    return list(plan.protect)


# ─────────────────────────────────────────────────────────────────────────────
# The serving role — whether the policies above apply to anybody at all
# ─────────────────────────────────────────────────────────────────────────────

#: Conventional name of the non-superuser role the application *serves requests* as.
#: Provisioned by ``scripts/sql/aegis-app-role.sql`` (run by ``scripts/db-roles.sh`` /
#: ``scripts/db-roles.ps1``) as ``LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE``,
#: owning no objects and holding only the DML :func:`grant_serving_role` gives it. The
#: host may point ``POSTGRES_DSN`` at a differently-named role; this is the default the
#: provisioning path creates and the documentation refers to.
SERVING_ROLE = "aegis_app"

#: A SQL identifier this module is willing to interpolate into DDL. Role names reach
#: :func:`grant_serving_role` from a DSN, i.e. from configuration rather than from a
#: request, but ``GRANT`` takes no bind parameter for its grantee, so the name is
#: interpolated — and anything interpolated is validated first rather than trusted.
#: Deliberately narrower than Postgres allows (no quoted identifiers, no unicode): a
#: role name that needs escaping is refused, not escaped.
_SAFE_ROLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")


class RlsBypassError(RuntimeError):
    """The role serving requests is exempt from Row-Level Security.

    Raised by :func:`report_rls_enforcement` when the caller asked for the check to be
    fatal (the recommended posture outside development — see that function). Not a
    configuration *warning*: while it holds, every ``tenant_isolation`` policy this
    module installs is inert and cross-tenant reads are limited only by whatever
    ``WHERE tenant_id = :ctx`` the application code remembered to write.
    """


#: The serving role's own exemption status, plus the roles it could ``SET ROLE`` into to
#: acquire one. ``pg_roles`` is readable by every role, so this needs no privilege of its
#: own. Role *attributes* (SUPERUSER, BYPASSRLS, …) are never inherited through role
#: membership in Postgres — a member has to ``SET ROLE`` explicitly — which is why
#: membership is reported separately, and at a lower severity, than a direct attribute.
_SERVING_ROLE_SQL = """
SELECT r.rolname,
       r.rolsuper,
       r.rolbypassrls,
       (SELECT coalesce(array_agg(g.rolname ORDER BY g.rolname), '{}')
          FROM pg_roles g
         WHERE (g.rolsuper OR g.rolbypassrls)
           AND g.rolname <> r.rolname
           AND pg_has_role(r.rolname, g.oid, 'MEMBER')) AS escalations
  FROM pg_roles r
 WHERE r.rolname = current_user
"""


@dataclass(frozen=True, slots=True)
class RlsEnforcement:
    """Whether the connection serving requests is actually subject to RLS.

    Attributes:
        dialect: The serving engine's dialect name. Anything other than
            ``postgresql`` makes this whole question inapplicable (SQLite has no row
            security), which :attr:`applicable` reports rather than faking a pass.
        role: ``current_user`` on the serving connection — the role whose exemptions
            decide whether the policies engage. Empty when not applicable.
        is_superuser: Whether that role has ``SUPERUSER``. A superuser skips row
            security entirely; ``FORCE ROW LEVEL SECURITY`` does not touch this.
        bypasses_rls: Whether that role has the ``BYPASSRLS`` attribute — the same
            outcome as superuser for these policies, by a narrower grant.
        escalatable_via: Superuser/``BYPASSRLS`` roles the serving role is a member
            of. Not an exemption by itself (attributes are not inherited), but a role
            that can ``SET ROLE postgres`` is one statement away from one.
    """

    dialect: str
    role: str = ""
    is_superuser: bool = False
    bypasses_rls: bool = False
    escalatable_via: tuple[str, ...] = ()

    @property
    def applicable(self) -> bool:
        """Whether row security is a thing this database has at all."""
        return self.dialect == "postgresql"

    @property
    def bypassed(self) -> bool:
        """Whether the serving role skips every RLS policy on this database."""
        return self.applicable and (self.is_superuser or self.bypasses_rls)

    @property
    def cause(self) -> str:
        """The attribute(s) granting the exemption, for the log line."""
        causes = []
        if self.is_superuser:
            causes.append("SUPERUSER")
        if self.bypasses_rls:
            causes.append("BYPASSRLS")
        return " + ".join(causes) if causes else "none"


async def audit_rls_enforcement(engine: AsyncEngine) -> RlsEnforcement:
    """Ask the database whether the *serving* role is exempt from row security.

    This is the check that the thirteen policies :func:`bootstrap_rls` installs are
    enforced against somebody. It must be run on the engine that serves **requests**,
    never on the owner/DDL engine — the owner is expected to bypass (that is the point
    of the split), so auditing it would report a healthy answer about the wrong
    connection.

    The query is a plain ``pg_roles`` read available to every role, so it needs no
    privilege the application does not already have, and it deliberately does not
    attempt a functional probe (create a table, insert two tenants' rows, count them):
    a probe would need DDL rights the serving role must not have, and the catalog
    answer is exactly as authoritative.

    Nothing here is wrapped in a ``try``. A connection failure propagates to the caller
    to classify — an unreachable database is a different fact from an exempt role, and
    the one thing this function must never do is turn the second into silence. (The
    module it replaces did exactly that: a broad ``except`` around an earlier
    diagnostic meant it could not fire, so it never reported the condition it existed
    to catch.)

    Args:
        engine: The engine used to serve application requests.

    Returns:
        An :class:`RlsEnforcement` describing the serving role. On a non-PostgreSQL
        dialect it is returned with :attr:`~RlsEnforcement.applicable` false and no
        query is issued.
    """
    if engine.dialect.name != "postgresql":
        return RlsEnforcement(dialect=engine.dialect.name)
    async with engine.connect() as conn:
        result = await conn.execute(text(_SERVING_ROLE_SQL))
        row = result.first()
    if row is None:
        # current_user always has a pg_roles entry, so this is unreachable in practice;
        # returning an "unknown but named" record keeps the caller's contract total
        # rather than raising an AttributeError three frames later.
        return RlsEnforcement(dialect="postgresql")
    return RlsEnforcement(
        dialect="postgresql",
        role=str(row[0]),
        is_superuser=bool(row[1]),
        bypasses_rls=bool(row[2]),
        escalatable_via=tuple(row[3] or ()),
    )


def report_rls_enforcement(
    enforcement: RlsEnforcement, *, fatal: bool = False
) -> None:
    """Log the audit's verdict — and, when asked, refuse to let the process continue.

    Severity is chosen so the line cannot be triaged as noise: an exempt serving role
    is logged at ERROR and names the consequence in the message itself, because
    "connected as a superuser" reads like an operational detail while "row-level
    security is inert; every tenant policy is bypassed" reads like the incident it is.

    **On ``fatal``.** The recommendation, implemented by the host in
    :func:`app.data.session.verify_rls_enforcement`, is: *log* in development and
    *refuse to boot* anywhere else. A developer running as ``postgres`` against a local
    database is a normal, useful state and blocking it would only teach people to
    disable the check; a deployment serving real tenants on an exempt role is a
    silently-off security control, and the platform makes the isolation claim publicly.
    A control that is off but believed on is worse than one that is absent — the
    absent one at least gets compensating care. Booting anyway would leave the
    tenant-isolation guarantee resting entirely on application-level ``WHERE`` clauses,
    which is precisely the layer RLS exists to backstop.

    Args:
        enforcement: The verdict from :func:`audit_rls_enforcement`.
        fatal: Whether an exempt serving role should raise instead of only logging.

    Raises:
        RlsBypassError: If ``fatal`` and the serving role bypasses row security.
    """
    if not enforcement.applicable:
        logger.debug(
            "RLS enforcement check skipped: dialect %r has no row security.",
            enforcement.dialect,
        )
        return
    if enforcement.bypassed:
        message = (
            f"Row-level security is INERT: the serving role {enforcement.role!r} has "
            f"{enforcement.cause}, and Postgres skips row security entirely for such a "
            f"role — every '{_POLICY_NAME}' policy on the {len(_TENANT_SCOPED_TABLES)} "
            "tenant-scoped tables is bypassed, so one tenant's request can read another "
            "tenant's rows unless the application's own WHERE clause happens to stop it. "
            "FORCE ROW LEVEL SECURITY does not help here: it removes the table owner's "
            f"exemption, not this one. Fix: provision the {SERVING_ROLE!r} role "
            "(scripts/db-roles.sh or scripts/db-roles.ps1) and point POSTGRES_DSN at it, "
            "keeping the owner DSN in POSTGRES_ADMIN_DSN for DDL only."
        )
        if fatal:
            logger.critical("%s Refusing to serve.", message)
            raise RlsBypassError(message)
        logger.error(message)
        return
    if enforcement.escalatable_via:
        logger.warning(
            "Serving role %r is not itself exempt from RLS, but it is a member of %s, "
            "which hold SUPERUSER/BYPASSRLS — a single SET ROLE would bypass every "
            "tenant policy. Revoke that membership: the serving role should own nothing "
            "and inherit nothing.",
            enforcement.role,
            ", ".join(enforcement.escalatable_via),
        )
    logger.info(
        "RLS is enforced: serving role %r has neither SUPERUSER nor BYPASSRLS, so the "
        "'%s' policies apply to it.",
        enforcement.role,
        _POLICY_NAME,
    )


#: The DML the serving role is given on the application's tables, and nothing more —
#: no ``TRUNCATE``, no ``REFERENCES``, no ``TRIGGER``. The role owns none of these
#: tables, so it can neither drop a policy nor ``ALTER TABLE ... DISABLE ROW LEVEL
#: SECURITY`` on them: the isolation it is subject to is not its to remove.
_SERVING_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"

#: ``USAGE`` to reach the schema, and ``CREATE`` because LightRAG's ``PGKVStorage`` /
#: ``PGDocStatusStorage`` create their own bookkeeping tables at runtime **as the
#: connecting role** — the serving one. Without it, full-stores retrieval fails on
#: PostgreSQL 15+, where ``PUBLIC`` no longer carries schema ``CREATE``; granting it
#: explicitly also makes the posture identical across server versions instead of
#: silently inheriting a default that changed.
#:
#: This is not an RLS escape. ``DISABLE ROW LEVEL SECURITY`` and ``DROP POLICY`` require
#: ownership of the *target* table, and the governed tables stay owned by the DDL role;
#: a table the serving role creates for itself carries no tenant data and no policy of
#: ours. Compare the alternative — running LightRAG's data path on the owner connection
#: — which would put ingested content through a role that bypasses every policy.
_SERVING_SCHEMA_PRIVILEGES = "USAGE, CREATE"

#: Sequences back every ``id`` column; without ``USAGE`` the role can read but not
#: insert, which surfaces as a runtime ``permission denied for sequence`` on the first
#: audit write rather than at boot.
_SERVING_SEQUENCE_PRIVILEGES = "USAGE, SELECT"


async def grant_serving_role(engine: AsyncEngine, role: str) -> tuple[str, ...]:
    """Grant the serving role exactly the DML it needs, from the owner connection.

    Runs as part of the owner/DDL bootstrap, immediately after ``create_all``, for the
    same reason :func:`aegis.governance.schema.reconcile_additive_columns` runs there:
    ``create_all`` can add a table on any boot, a table the serving role has no
    privileges on, and the resulting ``permission denied`` would arrive as a 500 in the
    middle of a request rather than as a failure at startup. Day-0 provisioning
    (``scripts/sql/aegis-app-role.sql``) creates the role and sets default privileges;
    this keeps the grants in step with a schema that changes afterwards. Both are
    idempotent, and re-granting an existing privilege is a no-op.

    ``ALTER DEFAULT PRIVILEGES`` is issued as well as the bulk ``ON ALL TABLES`` grant:
    the first covers tables a *future* ``create_all`` makes on this connection's role,
    the second covers everything that already exists — including tables created before
    the default privileges were ever set.

    A no-op on a non-PostgreSQL dialect, when the role does not exist, and when the
    role name is not a plain SQL identifier. The first two are logged so a missing
    provisioning step is visible; see :data:`_SAFE_ROLE_NAME` for the third.

    Args:
        engine: An engine connected as the **owner** of the application's tables (the
            DDL connection). Granting requires ownership; the serving role cannot do
            this for itself, which is the property that makes the split meaningful.
        role: The serving role to grant to, e.g. :data:`SERVING_ROLE`.

    Returns:
        The statements executed, in order — empty when nothing was done, so a caller
        (and a test) can tell a skip from a grant.
    """
    if engine.dialect.name != "postgresql":
        return ()
    if not _SAFE_ROLE_NAME.match(role):
        logger.error(
            "Refusing to grant to role %r: not a plain SQL identifier. Rename the "
            "serving role, or grant it by hand — this module will not escape a name "
            "it interpolates into DDL.",
            role,
        )
        return ()
    async with engine.begin() as conn:
        exists = await conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}
        )
        if exists.first() is None:
            logger.error(
                "Serving role %r does not exist, so no privileges were granted to it. "
                "Until it is created (scripts/db-roles.sh / scripts/db-roles.ps1) the "
                "platform can only serve requests as the owner role, which bypasses "
                "every tenant RLS policy.",
                role,
            )
            return ()
        schema = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
        if not _SAFE_ROLE_NAME.match(str(schema)):
            logger.error(
                "Refusing to grant in schema %r: not a plain SQL identifier.", schema
            )
            return ()
        statements = (
            f'GRANT {_SERVING_SCHEMA_PRIVILEGES} ON SCHEMA "{schema}" TO "{role}"',
            f'GRANT {_SERVING_TABLE_PRIVILEGES} ON ALL TABLES IN SCHEMA "{schema}" '
            f'TO "{role}"',
            f'GRANT {_SERVING_SEQUENCE_PRIVILEGES} ON ALL SEQUENCES IN SCHEMA '
            f'"{schema}" TO "{role}"',
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" '
            f'GRANT {_SERVING_TABLE_PRIVILEGES} ON TABLES TO "{role}"',
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" '
            f'GRANT {_SERVING_SEQUENCE_PRIVILEGES} ON SEQUENCES TO "{role}"',
        )
        for statement in statements:
            await conn.execute(text(statement))
    logger.info(
        "Granted %s on the tables in schema %s to the serving role %r (owner-issued).",
        _SERVING_TABLE_PRIVILEGES,
        schema,
        role,
    )
    return statements
