"""The one execution path: a connection that cannot write, and reads that cannot escape.

Everything §7.9 measured against a live PostgreSQL is a control in this module, and each
one is here because the obvious alternative was tested and failed:

1. **A dedicated login role**, ``NOSUPERUSER NOBYPASSRLS``, holding ``SELECT`` and nothing
   else. This is the boundary. :func:`verify_posture` re-reads it from ``pg_roles`` and
   ``information_schema.role_table_grants`` over **the very connection the queries will
   use**, and :meth:`ReadOnlyRunner.run` refuses to execute anything at all when the check
   fails. A DSN pointed at the owner role by mistake is a refusal on the first request
   rather than a silent hole.
2. **Its own engine and pool.** Never ``SET ROLE`` on the application connection: ``RESET
   ROLE`` is one legal statement, and the §7.9 probe used it to walk from ``aegis_ro``
   back to the superuser and read ``pg_authid``. A role assumed inside a session is not a
   boundary; a separate authenticated connection is.
3. **Every statement over the extended protocol.** asyncpg's simple-protocol ``execute()``
   runs multiple commands from one string and returns only the first one's status tag —
   ``"SELECT 1; SET default_transaction_read_only = off; SELECT 2;"`` reports ``SELECT 1``
   and the setting is now off. The extended protocol refuses multi-statement outright, and
   a statement carrying at least one bind parameter always takes it. That invariant is
   enforced in :class:`~aegis.dbadmin.types.ReadQuery`'s constructor, so *how the query is
   sent* is a control this package cannot forget to apply.
4. **A statement timeout and an idle-in-transaction timeout**, set on the role by
   provisioning and re-applied as ``SET LOCAL`` here so a deployment that skipped the
   provisioning step is still bounded.
5. **A read-only transaction** that is never committed. ``default_transaction_read_only``
   is user-settable — the §7.9 probe turned it off from inside the read-only role — so
   this is a guard rail that turns a mistake into a clean error, never the boundary.
6. **Row and byte caps.** The query asks for ``limit + 1`` rows: truncation is then a fact
   observed rather than a guess, and the extra row is dropped before anything is returned.
   Serialised size is accumulated as rows are taken and the read stops at the byte cap.
   Both are *reported* — :class:`~aegis.dbadmin.types.QueryResult` carries the sentence
   naming what was cut, because a silently truncated answer is a wrong answer.
7. **An ``EXPLAIN`` pre-flight**, without ``ANALYZE``, refusing above a cost ceiling. It
   turns "timed out after 10 seconds" into "this would scan 40 million rows, here is why",
   which is a fact an operator can act on.

**What binds the tenant.** :meth:`ReadOnlyRunner.run` writes both GUCs from the sealed
:class:`~aegis.dbadmin.scope.ScopeBinding` with ``set_config(..., is_local => true)``, so
they are scoped to the transaction and cannot survive back into the pool. The RLS policy
underneath then engages on ``app.tenant_id`` as it does for every other connection — and
because that policy is fail-*open* on an unset scope, it is the second layer here, never
the first. The first is :data:`aegis.dbadmin.catalogue.TENANT_PREDICATE`, welded into the
statement text, which returns nothing when nothing is bound.

``set_config`` rather than ``SET``: ``SET app.tenant_id = :tid`` is not executable over the
extended protocol at all (``syntax error at or near "$1"``), and the value must never be
interpolated. This is the same reasoning, and the same fix, as
:func:`aegis.governance.rls.set_tenant_scope`.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from aegis.dbadmin.catalogue import TENANT_COLUMN
from aegis.dbadmin.scope import ALL_TENANTS_GUC, TENANT_GUC, ScopeBinding
from aegis.dbadmin.types import (
    Column,
    ForeignKey,
    PlanTooExpensiveError,
    QueryResult,
    ReadOnlyPosture,
    ReadQuery,
    TableInfo,
    UnsafeRoleError,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.ext.asyncio import AsyncConnection

__all__ = [
    "DEFAULT_MAX_PLAN_COST",
    "MAX_RESULT_BYTES",
    "STATEMENT_TIMEOUT_MS",
    "ReadOnlyRunner",
    "verify_posture",
]

logger = logging.getLogger(__name__)

#: Per-statement wall clock, in milliseconds. §7.9 control 4, verified there against a
#: live cluster: ``pg_sleep(10)`` under this setting raises *canceling statement due to
#: statement timeout* rather than holding a worker.
STATEMENT_TIMEOUT_MS = 10_000

#: How long a transaction may sit idle before PostgreSQL kills it. A console tab left
#: open must not pin a connection or hold back vacuum.
IDLE_IN_TRANSACTION_TIMEOUT_MS = 30_000

#: The serialised-size ceiling for one result, in bytes. Read-only is not the same as
#: harmless: without this, one browse of ``chunks`` returns every document body Aegis has
#: ever ingested in a single HTTP response.
MAX_RESULT_BYTES = 5 * 1024 * 1024

#: The planner total-cost estimate above which a read is refused before it runs. Chosen so
#: an unindexed scan of a large relation is caught while every catalogue inspection over a
#: realistic corpus passes. Raise it deliberately, per deployment, not per query.
DEFAULT_MAX_PLAN_COST = 5_000_000.0

#: The catalog read. Executed **as the console's role**, so it reports exactly the columns
#: that role may select — column-level grants are respected by ``information_schema``
#: (§7.9 finding 5), which is why this package needs no denylist and has nothing to drift.
_COLUMNS_SQL = """
SELECT c.table_name,
       c.column_name,
       c.data_type,
       c.is_nullable = 'YES' AS nullable,
       c.ordinal_position
  FROM information_schema.columns c
  JOIN information_schema.tables tb
    ON tb.table_schema = c.table_schema AND tb.table_name = c.table_name
 WHERE c.table_schema = current_schema()
   AND tb.table_type = 'BASE TABLE'
   AND has_any_column_privilege(format('%I.%I', c.table_schema, c.table_name)::regclass,
                                'SELECT')
 ORDER BY c.table_name, c.ordinal_position
"""

#: Primary keys, for keyset pagination. ``OFFSET`` is not an option — see
#: :func:`aegis.dbadmin.catalogue.browse_query`.
#:
#: Read from ``pg_catalog``, **not** ``information_schema.table_constraints``, and this is
#: not a style preference: the SQL-standard constraint views only show constraints on
#: tables *owned by a currently enabled role*. The console's role owns nothing on purpose,
#: so through ``information_schema`` it sees zero primary keys and zero foreign keys — and
#: a schema browser that believes every table is unkeyed can page none of them. Measured
#: on a live cluster, which is the only way this class of bug is ever found.
_PRIMARY_KEY_SQL = """
SELECT c.relname AS table_name, a.attname AS column_name, k.ord AS position
  FROM pg_constraint con
  JOIN pg_class c ON c.oid = con.conrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
  CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
  JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
 WHERE n.nspname = current_schema()
   AND con.contype = 'p'
   AND has_any_column_privilege(c.oid, 'SELECT')
 ORDER BY c.relname, k.ord
"""

#: Outgoing references, for foreign-key navigation from a row to what it points at. From
#: ``pg_catalog`` for the same ownership reason as :data:`_PRIMARY_KEY_SQL`.
_FOREIGN_KEY_SQL = """
SELECT c.relname  AS from_table,
       a.attname  AS from_column,
       rc.relname AS to_table,
       ra.attname AS to_column
  FROM pg_constraint con
  JOIN pg_class c ON c.oid = con.conrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_class rc ON rc.oid = con.confrelid
  CROSS JOIN LATERAL unnest(con.conkey, con.confkey) WITH ORDINALITY AS k(att, refatt, ord)
  JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.att
  JOIN pg_attribute ra ON ra.attrelid = rc.oid AND ra.attnum = k.refatt
 WHERE n.nspname = current_schema()
   AND con.contype = 'f'
   AND has_any_column_privilege(c.oid, 'SELECT')
 ORDER BY 1, 2, k.ord
"""

#: Planner row estimates. ``reltuples`` is an estimate and is never presented as a count;
#: the exact answer costs a full scan and is offered separately, on request.
_ROW_ESTIMATE_SQL = """
SELECT c.relname, GREATEST(c.reltuples, 0)::bigint AS rows
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = current_schema()
   AND c.relkind IN ('r', 'p')
"""

#: Columns the catalog says exist but this role may not select. The difference between
#: :data:`_COLUMNS_SQL` and this is what the browser reports as *withheld*, so a
#: column-level grant is visible as a deliberate act rather than as a missing column.
_WITHHELD_SQL = """
SELECT a.attrelid::regclass::text AS table_name, a.attname AS column_name
  FROM pg_attribute a
  JOIN pg_class c ON c.oid = a.attrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = current_schema()
   AND c.relkind = 'r'
   AND a.attnum > 0
   AND NOT a.attisdropped
   AND has_any_column_privilege(c.oid, 'SELECT')
   AND NOT has_column_privilege(c.oid, a.attnum, 'SELECT')
 ORDER BY 1, 2
"""

#: The role's own attributes plus the roles it could ``SET ROLE`` into to acquire an
#: exemption. Role *attributes* are never inherited through membership in PostgreSQL — a
#: member has to ``SET ROLE`` explicitly — which is why membership is reported separately.
_POSTURE_SQL = """
SELECT r.rolname,
       r.rolsuper,
       r.rolbypassrls,
       (SELECT coalesce(array_agg(g.rolname ORDER BY g.rolname), '{}')
          FROM pg_roles g
         WHERE (g.rolsuper OR g.rolbypassrls)
           AND g.rolname <> r.rolname
           AND pg_has_role(r.rolname, g.oid, 'MEMBER')) AS escalations,
       current_setting('default_transaction_read_only', true) AS read_only,
       current_setting('statement_timeout', true)             AS statement_timeout
  FROM pg_roles r
 WHERE r.rolname = current_user
"""

#: Every relation in the search schema this role can write. **Must be empty.** This is the
#: read-only boundary, and it is measured rather than configured: §7.9 finding 4 showed the
#: read-only role turning its own ``default_transaction_read_only`` off, and what stopped
#: the write was the absent grant.
_WRITABLE_SQL = """
SELECT c.relname
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = current_schema()
   AND c.relkind IN ('r', 'p', 'v', 'f')
   AND (has_any_column_privilege(c.oid, 'INSERT')
     OR has_any_column_privilege(c.oid, 'UPDATE')
     OR has_table_privilege(c.oid, 'DELETE')
     OR has_table_privilege(c.oid, 'TRUNCATE'))
 ORDER BY 1
"""


async def verify_posture(engine: AsyncEngine) -> ReadOnlyPosture:
    """Measure what the console's connection can actually do.

    Run over the same engine the queries use, so a constructor that quietly handed back a
    privileged connection is caught here rather than never.

    Args:
        engine: The console's own read-only engine.

    Returns:
        The measured :class:`~aegis.dbadmin.types.ReadOnlyPosture`. Callers check
        :attr:`~aegis.dbadmin.types.ReadOnlyPosture.is_safe` and render
        :meth:`~aegis.dbadmin.types.ReadOnlyPosture.refusal` when it is false.
    """
    async with engine.connect() as conn:
        row = (await conn.execute(text(_POSTURE_SQL))).one()
        writable = tuple(str(name) for (name,) in await conn.execute(text(_WRITABLE_SQL)))
        await conn.rollback()
    return ReadOnlyPosture(
        role=str(row[0]),
        is_superuser=bool(row[1]),
        bypasses_rls=bool(row[2]),
        writable_tables=writable,
        default_read_only=str(row[4] or "").lower() == "on",
        statement_timeout=str(row[5] or ""),
        escalations=tuple(str(name) for name in (row[3] or ())),
    )


@dataclass(frozen=True, slots=True)
class ReadOnlyRunner:
    """Executes a :class:`~aegis.dbadmin.types.ReadQuery`, and nothing else.

    It knows nothing about *where* a query came from. That is the property §7.9 asks for —
    "one execution path, two front doors" — and it is what would make a free-form box, if
    one is ever added, inherit every control here without a second implementation.

    Attributes:
        engine: The console's own engine, built from its own DSN. Never the application's.
        max_plan_cost: The ``EXPLAIN`` cost ceiling above which a read is refused.
        statement_timeout_ms: Per-statement wall clock, re-applied per transaction.
        max_result_bytes: Serialised-size ceiling for one result.
    """

    engine: AsyncEngine
    max_plan_cost: float = DEFAULT_MAX_PLAN_COST
    statement_timeout_ms: int = STATEMENT_TIMEOUT_MS
    max_result_bytes: int = MAX_RESULT_BYTES

    async def posture(self) -> ReadOnlyPosture:
        """Measure the connection's privileges — see :func:`verify_posture`."""
        return await verify_posture(self.engine)

    async def schema(self, binding: ScopeBinding) -> tuple[TableInfo, ...]:
        """Read the catalog **as the console's role**, under ``binding``.

        The catalog is not tenant-scoped — a table's existence is not one tenant's secret
        — but the binding is applied anyway so the row estimates and every subsequent
        browse run under one authority for the whole page, and so a connection can never
        reach a query path that skipped the binding.

        Args:
            binding: The resolved scope, from :func:`aegis.dbadmin.scope.binding_for`.

        Returns:
            One :class:`~aegis.dbadmin.types.TableInfo` per readable base table, ordered
            by name.

        Raises:
            UnsafeRoleError: If the connection is not read-only.
        """
        await self._require_safe()
        async with self.engine.connect() as conn:
            await self._prepare(conn, binding)
            columns_rows = list(await conn.execute(text(_COLUMNS_SQL)))
            pk_rows = list(await conn.execute(text(_PRIMARY_KEY_SQL)))
            fk_rows = list(await conn.execute(text(_FOREIGN_KEY_SQL)))
            estimate_rows = list(await conn.execute(text(_ROW_ESTIMATE_SQL)))
            withheld_rows = list(await conn.execute(text(_WITHHELD_SQL)))
            await conn.rollback()

        keys: dict[str, list[str]] = {}
        for table_name, column_name, _position in pk_rows:
            keys.setdefault(str(table_name), []).append(str(column_name))
        references: dict[str, list[ForeignKey]] = {}
        for from_table, from_column, to_table, to_column in fk_rows:
            references.setdefault(str(from_table), []).append(
                ForeignKey(
                    column=str(from_column),
                    references_table=str(to_table),
                    references_column=str(to_column),
                )
            )
        estimates = {str(name): int(rows) for name, rows in estimate_rows}
        withheld: dict[str, list[str]] = {}
        for table_name, column_name in withheld_rows:
            withheld.setdefault(str(table_name), []).append(str(column_name))

        columns: dict[str, list[Column]] = {}
        for table_name, column_name, data_type, nullable, _position in columns_rows:
            name = str(table_name)
            columns.setdefault(name, []).append(
                Column(
                    name=str(column_name),
                    data_type=str(data_type),
                    nullable=bool(nullable),
                    is_primary_key=str(column_name) in keys.get(name, ()),
                )
            )
        return tuple(
            TableInfo(
                name=name,
                columns=tuple(table_columns),
                primary_key=tuple(keys.get(name, ())),
                foreign_keys=tuple(references.get(name, ())),
                row_estimate=estimates.get(name, 0),
                tenant_scoped=any(c.name == TENANT_COLUMN for c in table_columns),
                withheld_columns=tuple(withheld.get(name, ())),
            )
            for name, table_columns in sorted(columns.items())
        )

    async def run(
        self, query: ReadQuery, binding: ScopeBinding, *, row_limit: int
    ) -> QueryResult:
        """Execute one read under ``binding``, bounded and reported.

        Args:
            query: The statement to run. Built by :mod:`aegis.dbadmin.catalogue`; the
                runner does not inspect its text and does not need to.
            binding: The resolved scope. Both GUCs are written from it, transaction-local.
            row_limit: How many rows the caller will show. The statement itself asks for
                one more, so truncation is observed rather than assumed.

        Returns:
            The rows, with the bounds that fired stated on the result.

        Raises:
            UnsafeRoleError: If the connection is not read-only.
            PlanTooExpensiveError: If the pre-flight estimate exceeds the ceiling.
        """
        await self._require_safe()
        started = time.perf_counter()
        async with self.engine.connect() as conn:
            await self._prepare(conn, binding)
            plan_cost, plan_summary = await self._preflight(conn, query)
            result = await conn.execute(text(query.sql), query.params)
            columns = tuple(str(name) for name in result.keys())  # noqa: SIM118 - RMKeyView, not a dict
            # The statement asked for ``row_limit + 1`` rows (see
            # :func:`aegis.dbadmin.catalogue.browse_query`). Arriving at the (limit+1)th
            # row is therefore an *observation* that more matched, not an inference from
            # a full page — the failure mode where a result of exactly ``limit`` rows is
            # reported as truncated, or a truncated one as complete.
            rows: list[tuple[Any, ...]] = []
            approx_bytes = 0
            byte_capped = False
            row_capped = False
            for row in result:
                if len(rows) >= row_limit:
                    row_capped = True
                    break
                encoded = len(_encode(row))
                if approx_bytes + encoded > self.max_result_bytes:
                    byte_capped = True
                    break
                approx_bytes += encoded
                rows.append(tuple(row))
            await conn.rollback()

        duration_ms = (time.perf_counter() - started) * 1000
        truncated = row_capped or byte_capped
        return QueryResult(
            columns=columns,
            rows=tuple(rows),
            row_count=len(rows),
            truncated=truncated,
            truncation_reason=_truncation_sentence(
                row_capped=row_capped,
                byte_capped=byte_capped,
                shown=len(rows),
                max_bytes=self.max_result_bytes,
            ),
            duration_ms=duration_ms,
            approx_bytes=approx_bytes,
            plan_cost=plan_cost,
            plan_summary=plan_summary,
        )

    async def _require_safe(self) -> None:
        """Refuse to run anything unless the connection is measurably read-only.

        Raises:
            UnsafeRoleError: Carrying the sentence naming exactly which privilege
                disqualified the connection.
        """
        posture = await verify_posture(self.engine)
        if not posture.is_safe:
            raise UnsafeRoleError(posture.refusal())

    async def _prepare(self, conn: AsyncConnection, binding: ScopeBinding) -> None:
        """Bind the scope and the per-transaction bounds onto ``conn``.

        Every statement here carries a bind parameter or is a constant this module wrote,
        so none of them can smuggle a second command. ``SET TRANSACTION READ ONLY`` is a
        guard rail — the privilege is the boundary — but it turns a mistake into a clean
        error instead of a write.

        Args:
            conn: The open connection, inside its implicit transaction.
            binding: The resolved scope to write into both GUCs.
        """
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        await conn.execute(
            text(f"SET LOCAL statement_timeout = '{int(self.statement_timeout_ms)}ms'")
        )
        await conn.execute(
            text(
                "SET LOCAL idle_in_transaction_session_timeout = "
                f"'{int(IDLE_IN_TRANSACTION_TIMEOUT_MS)}ms'"
            )
        )
        await conn.execute(
            text("SELECT set_config(:name, :value, true)"),
            {"name": TENANT_GUC, "value": binding.tenant_value},
        )
        await conn.execute(
            text("SELECT set_config(:name, :value, true)"),
            {"name": ALL_TENANTS_GUC, "value": binding.all_tenants_value},
        )

    async def _preflight(self, conn: AsyncConnection, query: ReadQuery) -> tuple[float, str]:
        """``EXPLAIN`` the read and refuse it if the planner's estimate is too large.

        Never ``ANALYZE``: that executes the statement, which is the thing being decided
        about. The plan is returned even when it passes, so the screen can show *why* a
        read was slow rather than only that it was.

        Args:
            conn: The prepared connection.
            query: The read to estimate.

        Returns:
            ``(total_cost, top node description)``.

        Raises:
            PlanTooExpensiveError: If the estimate exceeds :attr:`max_plan_cost`.
        """
        explained = await conn.execute(
            text(f"EXPLAIN (FORMAT JSON) {query.sql}"), query.params
        )
        payload = explained.scalar_one()
        plan = (json.loads(payload) if isinstance(payload, str) else payload)[0]["Plan"]
        cost = float(plan.get("Total Cost", 0.0))
        estimated_rows = int(plan.get("Plan Rows", 0))
        summary = _plan_summary(plan)
        if cost > self.max_plan_cost:
            raise PlanTooExpensiveError(
                f"This read was refused before it ran: the planner estimates a cost of "
                f"{cost:,.0f} ({summary}, about {estimated_rows:,} rows), above this "
                f"deployment's ceiling of {self.max_plan_cost:,.0f}. Narrow it to one "
                f"tenant, add a filter, or ask for fewer rows."
            )
        return cost, summary


def _plan_summary(plan: dict[str, Any]) -> str:
    """Describe a plan in one phrase — the deepest node that names a relation.

    The root of a plan for a bounded read is almost always ``Limit``, which tells an
    operator nothing. What they need is *how* the rows were found: ``Seq Scan on
    usage_ledger`` and ``Index Scan on usage_ledger`` are the two answers that explain a
    slow page, and only the second one is fine.

    Args:
        plan: The root plan node from ``EXPLAIN (FORMAT JSON)``.

    Returns:
        ``"<Node Type> on <relation>"`` for the first relation-bearing node, else the
        root node's type.
    """
    node: dict[str, Any] | None = plan
    while node is not None:
        relation = node.get("Relation Name")
        if relation:
            return f"{node.get('Node Type', 'Scan')} on {relation}"
        children = node.get("Plans") or []
        node = children[0] if children else None
    return str(plan.get("Node Type", "Plan"))


def _encode(row: Any) -> bytes:  # noqa: ANN401 - a SQLAlchemy Row of arbitrary types
    """Return a stable byte encoding of one row, for the size cap.

    ``default=str`` because a row legitimately holds datetimes, enums and UUIDs; the exact
    encoding does not matter, only that it is proportional to what will be serialised to
    the browser.
    """
    return json.dumps(tuple(row), default=str).encode("utf-8", "replace")


def _truncation_sentence(
    *, row_capped: bool, byte_capped: bool, shown: int, max_bytes: int
) -> str:
    """Return the sentence naming what was cut, or the empty string when nothing was.

    Saying what was truncated rather than truncating silently is the whole of §7.9
    control 7: a bounded answer an operator knows is bounded is useful, and one they do
    not is wrong.
    """
    if byte_capped:
        megabytes = max_bytes / (1024 * 1024)
        ceiling = f"{megabytes:.0f} MB" if megabytes >= 1 else f"{max_bytes:,} bytes"
        return (
            f"Stopped after {shown:,} rows: the result reached this page's {ceiling} "
            f"ceiling. Narrow the read, or export it instead of browsing it."
        )
    if row_capped:
        return (
            f"Showing the first {shown:,} rows; more matched. Page forward, filter, or "
            f"narrow the scope to see the rest."
        )
    return ""
