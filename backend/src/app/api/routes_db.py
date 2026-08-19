"""The database console — looking at the data without dropping out of the product (§7.9).

*"There is no way to look at the data without ``psql``"* is the same defect as every other
gap this phase closes, wearing different clothes: the operator leaves the product to answer
a question the product should answer. This module is the front door; the hardened path
behind it is :mod:`aegis.dbadmin`, and the order matters — **the path was built first, and
this file is a projection of it.**

# Three routes, and why not five

``GET /database/overview`` returns the posture, the schema, the inspection catalogue and
the tenant list in one call, following ``GET /governance/dashboard``'s precedent: a screen
that needs four facts to render should not make four round trips for them. The two
executing routes are separate because they are the ones that are rate-limited, audited and
bounded, and a read that runs a query should never share a path with one that does not.

# What is enforced here, and what is enforced one layer down

Here:

* :func:`require_db_console` — ``require_platform_admin``, never ``require_admin``: the
  latter admits the tenant-admin tier too, and §7.16 row 4 puts any database browse at
  ``readable_by: platform``. The guard also refuses when the console is switched off, so a
  disabled deployment cannot be talked into a read by a well-formed request.
* A **rate limit**. There is no rate limiting anywhere else in ``backend/src``, and this is
  the worst page for that to stay true: read-only is not the same as harmless, and a loop
  over this endpoint is a self-inflicted outage on the cluster the product runs on.
* **Two audit rows per execution.** ``db.query.execute`` is written *before* the statement
  runs, carrying the query, its parameters, the resolved scope and the caller;
  ``db.query.result`` is written after, carrying the row count, the bytes, the duration and
  the verdict. Two rows rather than one update because ``audit_log`` is append-only — and
  because a query that kills the process then leaves the first row standing alone, which is
  itself the signal. They are correlated by a ``query_id``. *Who looked at what* is the
  entire compliance story of this surface, so it is not best-effort: a read whose audit row
  cannot be written does not run.

One layer down, in :mod:`aegis.dbadmin`, because that is where it cannot be bypassed by
adding another route: the read-only role and the privilege check that refuses to serve a
connection that can write; the extended protocol; the statement timeout; the row, byte and
plan-cost bounds; and the fail-closed tenant predicate welded into every statement.

# There is no free-form SQL box

Deliberately, and the argument is written at the top of :mod:`aegis.dbadmin.catalogue`
where the decision lives rather than here where it would be a comment. The short version:
Metabase disables native SQL for any database with row or column security because it cannot
parse SQL well enough to know which tables a query touches; Aegis has ``tenant_isolation``
on nineteen relations; and this repository already refused the same shape one layer down,
in :mod:`aegis.guardrails.patterns`, for the same reason. The execution path is built so
that a free-form front door could be mounted on it later without weakening anything — one
path, two front doors, as §7.9 describes — and the cut order in that section puts the box
first out anyway.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import deque
from typing import Any

from aegis.dbadmin import (
    DEFAULT_ROW_LIMIT,
    INSPECTIONS,
    MAX_RESULT_BYTES,
    MAX_ROW_LIMIT,
    STATEMENT_TIMEOUT_MS,
    DbAdminError,
    QueryResult,
    ReadOnlyPosture,
    ReadOnlyRunner,
    ReadQuery,
    ScopeBinding,
    TableInfo,
    binding_for,
    browse_query,
    count_query,
    inspection_named,
    narrow_to,
    resolve_limit,
    table_named,
)
from aegis.retrieval.types import UntenantedPrincipalError
from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.api.routes import AuthContext, require_platform_admin
from app.config import get_settings
from app.data.session import to_asyncpg_dsn

logger = logging.getLogger(__name__)

db_router = APIRouter()

#: The environment variable that switches the whole surface on. Default **off**: a database
#: console that is on by default is a database console somebody forgot about.
ENABLE_ENV = "AEGIS_DB_CONSOLE_ENABLED"

#: The environment variable naming the console's own DSN. It must point at a role that
#: cannot write — :func:`aegis.dbadmin.runner.verify_posture` refuses to serve otherwise —
#: and it must **not** be ``POSTGRES_DSN``: the serving role holds INSERT/UPDATE/DELETE.
DSN_ENV = "AEGIS_DB_CONSOLE_DSN"

#: Reads one principal may run per minute. A sliding window, in process. Small on purpose:
#: this is a human looking at tables, not a batch job, and every legitimate use of the page
#: fits inside it.
RATE_LIMIT_PER_MINUTE = 30

#: The window the rate limit is measured over, in seconds.
RATE_WINDOW_SECONDS = 60.0

#: Per-principal timestamps of recent executions. Process-local, which is the honest scope
#: for a single-process deployment and is stated rather than dressed up as a cluster-wide
#: limit; a multi-process deployment needs this in Redis, and that is named in the report
#: rather than pretended.
_recent: dict[str, deque[float]] = {}

#: The console's engine, built once. Its **own** engine and pool, never the application's —
#: §7.9 finding 3: ``SET ROLE`` on a shared connection is not a boundary, because ``RESET
#: ROLE`` is one legal statement away.
_engine: AsyncEngine | None = None


def reset_console_engine() -> None:
    """Drop the memoised console engine and the rate-limit window.

    For tests and for a deployment that re-points its DSN without a restart. Named and
    exported rather than left as a private global poke, because a test that reaches into
    module state is a test that stops working when the state is renamed.
    """
    global _engine
    _engine = None
    _recent.clear()


def console_dsn() -> str:
    """Return the console's DSN, or the empty string when none is configured."""
    return str(getattr(get_settings(), "db_console_dsn", "") or "").strip()


def console_enabled() -> bool:
    """Whether this deployment serves the database console at all.

    Both halves are required: the switch **and** a DSN. A deployment that turned the
    console on without provisioning its role would otherwise fall back to the application
    connection, which is exactly the design §7.9 exists to refuse.
    """
    return bool(getattr(get_settings(), "db_console_enabled", False)) and bool(console_dsn())


def _console_engine() -> AsyncEngine:
    """Return the console's engine, building it on first use.

    Raises:
        HTTPException: 503 when no console DSN is configured.
    """
    global _engine
    if _engine is None:
        dsn = console_dsn()
        if not dsn:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    f"The database console has no connection of its own. Set {DSN_ENV} to "
                    f"the aegis_readonly role provisioned by "
                    f"`python -m aegis.dbadmin`, and restart."
                ),
            )
        _engine = create_async_engine(to_asyncpg_dsn(dsn))
    return _engine


def _runner() -> ReadOnlyRunner:
    """Build the runner over the console's engine, with this deployment's plan ceiling."""
    settings = get_settings()
    return ReadOnlyRunner(
        engine=_console_engine(),
        max_plan_cost=float(getattr(settings, "db_console_max_plan_cost", 5_000_000.0)),
    )


def require_db_console(auth: AuthContext = Depends(require_platform_admin)) -> AuthContext:
    """Admit a platform admin, and only when the console is switched on.

    ``require_platform_admin``, never ``require_admin``: the latter admits the tenant-admin
    tier as well, and §7.16 row 4 places any database browse at ``readable_by: platform``.
    The kill switch is checked here rather than per handler so a route added later cannot
    forget it.

    Args:
        auth: The authenticated platform admin.

    Returns:
        The same principal.

    Raises:
        HTTPException: 503 when the console is off, naming the variable that turns it on.
    """
    if not console_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"The database console is switched off in this deployment. Set "
                f"{ENABLE_ENV}=1 and point {DSN_ENV} at the read-only role to turn it on."
            ),
        )
    return auth


def _rate_limit(auth: AuthContext) -> None:
    """Refuse a caller that has run too many reads in the last minute.

    Args:
        auth: The calling principal. The window is per principal, not per process: one
            operator hammering the page must not lock another one out.

    Raises:
        HTTPException: 429, naming the limit and when the caller may retry.
    """
    now = time.monotonic()
    window = _recent.setdefault(auth.username, deque())
    while window and now - window[0] > RATE_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= RATE_LIMIT_PER_MINUTE:
        wait = int(RATE_WINDOW_SECONDS - (now - window[0])) + 1
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"You have run {RATE_LIMIT_PER_MINUTE} database reads in the last minute, "
                f"which is this page's limit. Wait {wait}s and try again."
            ),
        )
    window.append(now)


def _scope_for(auth: AuthContext, requested: int | None) -> tuple[ScopeBinding, Any]:
    """Resolve the authority one read runs under, refusing anything that would widen.

    Args:
        auth: The calling principal. Its :meth:`AuthContext.tenant_scope` is the sealed
            authority; the request cannot supply one.
        requested: The tenant the operator selected in the impersonation control, or
            ``None`` for their own authority.

    Returns:
        ``(binding, scope)`` — the GUC values, and the resolved scope for the audit row.

    Raises:
        HTTPException: 403 when the selector would move the read to another tenant, or the
            principal resolves to no authority at all.
    """
    try:
        scope = narrow_to(auth.tenant_scope(), requested)
        return binding_for(scope), scope
    except UntenantedPrincipalError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


# ─────────────────────────────────────────────────────────────────────────────
# Wire shapes
# ─────────────────────────────────────────────────────────────────────────────


class _Model(BaseModel):
    """Base wire model: camelCase on the wire, snake_case in Python."""

    model_config = ConfigDict(populate_by_name=True)


class ColumnOut(_Model):
    """One column of one table, as the console's grants report it."""

    name: str
    data_type: str = Field(serialization_alias="dataType")
    nullable: bool
    is_primary_key: bool = Field(serialization_alias="isPrimaryKey")


class ForeignKeyOut(_Model):
    """One outgoing reference, for navigating from a row to what it points at."""

    column: str
    references_table: str = Field(serialization_alias="referencesTable")
    references_column: str = Field(serialization_alias="referencesColumn")


class TableOut(_Model):
    """One browsable relation."""

    name: str
    columns: list[ColumnOut]
    primary_key: list[str] = Field(serialization_alias="primaryKey")
    foreign_keys: list[ForeignKeyOut] = Field(serialization_alias="foreignKeys")
    row_estimate: int = Field(serialization_alias="rowEstimate")
    tenant_scoped: bool = Field(serialization_alias="tenantScoped")
    withheld_columns: list[str] = Field(serialization_alias="withheldColumns")


class InspectionOut(_Model):
    """One curated read the operator may run."""

    id: str
    title: str
    summary: str
    source: str
    tenant_scoped: bool = Field(serialization_alias="tenantScoped")
    parameters: dict[str, Any]


class PostureOut(_Model):
    """What the console's connection can actually do, measured not configured."""

    role: str
    read_only: bool = Field(serialization_alias="readOnly")
    is_superuser: bool = Field(serialization_alias="isSuperuser")
    bypasses_rls: bool = Field(serialization_alias="bypassesRls")
    writable_tables: list[str] = Field(serialization_alias="writableTables")
    default_read_only: bool = Field(serialization_alias="defaultReadOnly")
    statement_timeout: str = Field(serialization_alias="statementTimeout")
    refusal: str | None


class TenantOut(_Model):
    """One tenant, for the scope selector."""

    id: int
    name: str


class OverviewOut(_Model):
    """Everything the page needs to render before anybody presses anything."""

    enabled: bool
    posture: PostureOut | None
    tables: list[TableOut]
    inspections: list[InspectionOut]
    tenants: list[TenantOut]
    scope: str
    row_limit_default: int = Field(serialization_alias="rowLimitDefault")
    row_limit_max: int = Field(serialization_alias="rowLimitMax")
    max_result_mb: int = Field(serialization_alias="maxResultMb")
    statement_timeout_ms: int = Field(serialization_alias="statementTimeoutMs")
    free_form_sql: bool = Field(serialization_alias="freeFormSql")
    free_form_reason: str = Field(serialization_alias="freeFormReason")


class BrowseIn(_Model):
    """Body of ``POST /database/browse``."""

    table: str
    limit: int | None = None
    order_by: str | None = Field(default=None, validation_alias="orderBy")
    after: str | None = None
    filter_column: str | None = Field(default=None, validation_alias="filterColumn")
    filter_value: str | None = Field(default=None, validation_alias="filterValue")
    tenant_id: int | None = Field(default=None, validation_alias="tenantId")
    exact_count: bool = Field(default=False, validation_alias="exactCount")


class InspectionIn(_Model):
    """Body of ``POST /database/inspections/{inspection_id}``."""

    limit: int | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    tenant_id: int | None = Field(default=None, validation_alias="tenantId")


class ResultOut(_Model):
    """One executed read: the rows, the bounds that fired, and what it ran as."""

    label: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int = Field(serialization_alias="rowCount")
    truncated: bool
    truncation_reason: str = Field(serialization_alias="truncationReason")
    duration_ms: float = Field(serialization_alias="durationMs")
    approx_bytes: int = Field(serialization_alias="approxBytes")
    plan_cost: float = Field(serialization_alias="planCost")
    plan_summary: str = Field(serialization_alias="planSummary")
    scope: str
    tenant_filtered: bool = Field(serialization_alias="tenantFiltered")
    sql: str
    exact_count: int | None = Field(default=None, serialization_alias="exactCount")
    query_id: str = Field(serialization_alias="queryId")


# ─────────────────────────────────────────────────────────────────────────────
# Execution — audited before, audited after, bounded throughout
# ─────────────────────────────────────────────────────────────────────────────

#: The sentence the screen shows where a free-form SQL box would be. Served from the
#: backend rather than written into the component, so the product's answer to "why can I
#: not type SQL here?" has one source.
FREE_FORM_REASON = (
    "This console runs a closed set of reads, not typed SQL. A statement typed here would "
    "be executed against a database with row-level security on nineteen tables, and no "
    "amount of parsing can tell which tenants a given statement would touch — so every "
    "read is assembled by the server with the tenant filter welded in."
)


async def _audited_run(
    auth: AuthContext,
    query: ReadQuery,
    binding: ScopeBinding,
    *,
    row_limit: int,
    via: str,
) -> tuple[QueryResult, str]:
    """Run one read, with an audit row on each side of it.

    The first row is written **before** the statement is sent, so a read that never returns
    still leaves a trace of having been attempted — the case an after-the-fact audit misses
    entirely. The second carries the outcome. They share a ``query_id``.

    Neither is best-effort. ``_safe_audit`` swallows its own failures, which is right for a
    bookkeeping row beside an action that already happened, and wrong here: on this surface
    the audit row *is* the control, so an audit that cannot be written stops the read.

    Args:
        auth: The calling principal.
        query: The statement to run.
        binding: The resolved scope, for the record and for the connection.
        row_limit: How many rows to show.
        via: ``'browser'`` or ``'saved'`` — which front door this came through. There is no
            ``'freeform'``, and there is no code path that could produce one.

    Returns:
        ``(result, query_id)``.

    Raises:
        HTTPException: 4xx/5xx carrying the refusal's own sentence.
    """
    from aegis.governance.audit import record_audit

    query_id = uuid.uuid4().hex[:16]
    base = {
        "query_id": query_id,
        "via": via,
        "label": query.label,
        "table": query.table,
        "sql": query.sql,
        "parameters": {key: str(value) for key, value in query.params.items()},
        "scope": binding.describe(),
        "tenant_filtered": query.tenant_filtered,
        "row_limit": row_limit,
    }
    await record_audit(
        action="db.query.execute",
        actor=auth.username,
        model=None,
        trace_id=None,
        payload=base,
        tenant_id=binding.tenant_id,
    )
    try:
        result = await _runner().run(query, binding, row_limit=row_limit)
    except DbAdminError as exc:
        await _record_outcome(auth, binding, base, verdict="refused", detail=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except SQLAlchemyError as exc:
        detail = _database_sentence(exc)
        await _record_outcome(auth, binding, base, verdict="failed", detail=detail)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=detail
        ) from exc
    await _record_outcome(
        auth,
        binding,
        base,
        verdict="truncated" if result.truncated else "completed",
        detail=result.truncation_reason,
        rows=result.row_count,
        bytes_=result.approx_bytes,
        duration_ms=result.duration_ms,
    )
    return result, query_id


async def _record_outcome(
    auth: AuthContext,
    binding: ScopeBinding,
    base: dict[str, Any],
    *,
    verdict: str,
    detail: str = "",
    rows: int | None = None,
    bytes_: int | None = None,
    duration_ms: float | None = None,
) -> None:
    """Write the second audit row — what the read actually did."""
    from aegis.governance.audit import record_audit

    await record_audit(
        action="db.query.result",
        actor=auth.username,
        model=None,
        trace_id=None,
        payload={
            "query_id": base["query_id"],
            "via": base["via"],
            "label": base["label"],
            "table": base["table"],
            "scope": base["scope"],
            "verdict": verdict,
            "detail": detail,
            "rows": rows,
            "bytes": bytes_,
            "duration_ms": round(duration_ms, 2) if duration_ms is not None else None,
        },
        tenant_id=binding.tenant_id,
    )


def _database_sentence(exc: SQLAlchemyError) -> str:
    """Turn a driver exception into a sentence an operator can act on.

    The two that actually happen on this page are the statement timeout and a privilege
    refusal, and both have a next move. Anything else keeps the server's own words, because
    the database explaining itself beats this function guessing.
    """
    raw = str(getattr(exc, "orig", exc)).strip()
    if "statement timeout" in raw:
        return (
            f"That read was cancelled after {STATEMENT_TIMEOUT_MS // 1000}s. Narrow it to "
            "one tenant, add a filter, or ask for fewer rows."
        )
    if "permission denied" in raw.lower():
        return (
            f"The console's connection is not allowed to read that: {raw}. Re-run "
            "`python -m aegis.dbadmin` as the table owner to refresh its grants."
        )
    return f"The database refused that read: {raw}"


def _result_out(
    query: ReadQuery,
    result: QueryResult,
    binding: ScopeBinding,
    query_id: str,
    *,
    exact_count: int | None = None,
) -> ResultOut:
    """Project one executed read onto the wire.

    The statement text goes back to the browser deliberately. An operator who cannot see
    the query cannot check the answer, and this page's whole claim is that the server built
    a query with a tenant filter in it — a claim best made by showing the filter.
    """
    return ResultOut(
        label=query.label,
        columns=list(result.columns),
        rows=[list(row) for row in result.rows],
        row_count=result.row_count,
        truncated=result.truncated,
        truncation_reason=result.truncation_reason,
        duration_ms=round(result.duration_ms, 2),
        approx_bytes=result.approx_bytes,
        plan_cost=result.plan_cost,
        plan_summary=result.plan_summary,
        scope=binding.describe(),
        tenant_filtered=query.tenant_filtered,
        sql=query.sql,
        exact_count=exact_count,
        query_id=query_id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────


@db_router.get(
    "/database/overview",
    response_model=OverviewOut,
    response_model_by_alias=True,
    tags=["governance"],
    summary="The schema, the console's own privileges, and the reads it offers",
)
async def database_overview(
    auth: AuthContext = Depends(require_db_console),
) -> OverviewOut:
    """Everything the database page needs to render before anything is executed.

    One call rather than four, following ``GET /governance/dashboard``. Nothing here runs a
    tenant's data through a query except the tenant list itself, which is what the scope
    selector is built from.

    Args:
        auth: The platform admin.

    Returns:
        The posture, the readable schema, the inspection catalogue and the tenant list.
    """
    binding, _scope = _scope_for(auth, None)
    runner = _runner()
    try:
        posture = await runner.posture()
        tables = list(await runner.schema(binding)) if posture.is_safe else []
        tenants = await _tenant_list(tables, binding) if posture.is_safe else []
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The database console could not reach its own connection: "
                f"{_database_sentence(exc)}"
            ),
        ) from exc
    return OverviewOut(
        enabled=True,
        posture=_posture_out(posture),
        tables=[_table_out(table) for table in tables],
        inspections=[
            InspectionOut(
                id=item.id,
                title=item.title,
                summary=item.summary,
                source=item.source,
                tenant_scoped=item.tenant_scoped,
                parameters=dict(item.parameters),
            )
            for item in INSPECTIONS
        ],
        tenants=tenants,
        scope=binding.describe(),
        row_limit_default=DEFAULT_ROW_LIMIT,
        row_limit_max=MAX_ROW_LIMIT,
        max_result_mb=MAX_RESULT_BYTES // (1024 * 1024),
        statement_timeout_ms=STATEMENT_TIMEOUT_MS,
        free_form_sql=False,
        free_form_reason=FREE_FORM_REASON,
    )


async def _tenant_list(
    tables: list[TableInfo], binding: ScopeBinding
) -> list[TenantOut]:
    """Read the tenants the scope selector offers, over the console's own connection.

    Returns an empty list rather than raising when ``tenants`` is not readable: a console
    whose grants do not reach that table still browses everything else, and a page that
    500s because one optional control has no data is a worse answer than one control fewer.
    """
    if not binding.all_tenants:
        return []
    try:
        table = table_named(tables, "tenants")
        query = browse_query(table, platform_wide=True, limit=MAX_ROW_LIMIT, order_by="id")
        result = await _runner().run(query, binding, row_limit=MAX_ROW_LIMIT)
    except (DbAdminError, SQLAlchemyError):
        logger.info("database console: no readable tenants table for the scope selector")
        return []
    columns = {name: index for index, name in enumerate(result.columns)}
    if "id" not in columns or "name" not in columns:
        return []
    return [
        TenantOut(id=int(row[columns["id"]]), name=str(row[columns["name"]]))
        for row in result.rows
    ]


def _posture_out(posture: ReadOnlyPosture) -> PostureOut:
    """Project the measured posture onto the wire, refusal sentence and all."""
    return PostureOut(
        role=posture.role,
        read_only=posture.is_safe,
        is_superuser=posture.is_superuser,
        bypasses_rls=posture.bypasses_rls,
        writable_tables=list(posture.writable_tables),
        default_read_only=posture.default_read_only,
        statement_timeout=posture.statement_timeout,
        refusal=posture.refusal() or None,
    )


def _table_out(table: TableInfo) -> TableOut:
    """Project one catalog entry onto the wire."""
    return TableOut(
        name=table.name,
        columns=[
            ColumnOut(
                name=column.name,
                data_type=column.data_type,
                nullable=column.nullable,
                is_primary_key=column.is_primary_key,
            )
            for column in table.columns
        ],
        primary_key=list(table.primary_key),
        foreign_keys=[
            ForeignKeyOut(
                column=key.column,
                references_table=key.references_table,
                references_column=key.references_column,
            )
            for key in table.foreign_keys
        ],
        row_estimate=table.row_estimate,
        tenant_scoped=table.tenant_scoped,
        withheld_columns=list(table.withheld_columns),
    )


@db_router.post(
    "/database/browse",
    response_model=ResultOut,
    response_model_by_alias=True,
    tags=["governance"],
    summary="Read one table, keyset-paginated and tenant-filtered",
)
async def database_browse(
    body: BrowseIn,
    auth: AuthContext = Depends(require_db_console),
) -> ResultOut:
    """Browse one table under a resolved scope.

    Every identifier in ``body`` — the table, the ordering column, the filter column — is
    matched against the catalog **this connection can read**, never escaped. A column a
    grant withholds is not in the catalog, so it cannot be named here at all.

    Args:
        body: The table, the page, and the optional tenant selector.
        auth: The platform admin.

    Returns:
        The rows, with the bounds that fired stated on the result.

    Raises:
        HTTPException: 400 for an identifier that is not in the catalog or a read the
            planner refuses, 403 for a scope this caller may not read, 429 for the rate
            limit, 503 when the console's connection is not read-only.
    """
    _rate_limit(auth)
    binding, _scope = _scope_for(auth, body.tenant_id)
    runner = _runner()
    posture = await runner.posture()
    if not posture.is_safe:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=posture.refusal()
        )
    shown = _resolve_limit(body.limit)
    try:
        tables = list(await runner.schema(binding))
        table = table_named(tables, body.table)
        query = browse_query(
            table,
            platform_wide=binding.all_tenants,
            limit=shown,
            order_by=body.order_by,
            after=body.after,
            filter_column=body.filter_column,
            filter_value=body.filter_value,
        )
    except DbAdminError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    result, query_id = await _audited_run(
        auth, query, binding, row_limit=shown, via="browser"
    )
    exact = None
    if body.exact_count:
        counted, _ = await _audited_run(
            auth,
            count_query(table, platform_wide=binding.all_tenants),
            binding,
            row_limit=1,
            via="browser",
        )
        exact = int(counted.rows[0][0]) if counted.rows else 0
    return _result_out(query, result, binding, query_id, exact_count=exact)


@db_router.post(
    "/database/inspections/{inspection_id}",
    response_model=ResultOut,
    response_model_by_alias=True,
    tags=["governance"],
    summary="Run one curated, parameterised read",
)
async def database_inspection(
    body: InspectionIn,
    inspection_id: str = Path(..., description="An id from GET /database/overview"),
    auth: AuthContext = Depends(require_db_console),
) -> ResultOut:
    """Run one entry from the closed set of inspections.

    An id that is not in the catalogue is refused; there is no fallback to a free-form
    statement, because there is no free-form statement. A parameter the inspection does not
    declare is refused too, rather than dropped — a silently dropped filter answers a
    different question than the one asked.

    Args:
        body: The row limit, the declared parameters, and the optional tenant selector.
        inspection_id: Which inspection to run.
        auth: The platform admin.

    Returns:
        The rows, with the bounds that fired stated on the result.

    Raises:
        HTTPException: 400 for an unknown inspection or parameter, 403 for a scope this
            caller may not read, 429 for the rate limit, 503 when the console's connection
            is not read-only.
    """
    _rate_limit(auth)
    binding, _scope = _scope_for(auth, body.tenant_id)
    runner = _runner()
    posture = await runner.posture()
    if not posture.is_safe:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=posture.refusal()
        )
    shown = _resolve_limit(body.limit)
    try:
        inspection = inspection_named(inspection_id)
        tables = list(await runner.schema(binding))
        table = table_named(tables, inspection.source)
        if not inspection.tenant_scoped and not binding.all_tenants:
            raise DbAdminError(
                f"{inspection.title!r} reads {inspection.source!r}, which carries no "
                "tenant column, so it is readable only under a platform-wide authority."
            )
        query = inspection.build(table, limit=shown, values=body.parameters)
    except DbAdminError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    result, query_id = await _audited_run(
        auth, query, binding, row_limit=shown, via="saved"
    )
    return _result_out(query, result, binding, query_id)


def _resolve_limit(limit: int | None) -> int:
    """Clamp a requested row limit, translating the package's refusal into a 400."""
    try:
        return resolve_limit(limit)
    except DbAdminError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


def mount(target: APIRouter) -> None:
    """Attach this module's routes to ``target`` as real ``APIRoute`` objects.

    Idempotent, exactly as :func:`app.api.routes_redteam.mount` and
    :func:`app.api.routes_reports.mount` are, and for the same reason: this module is
    mounted from the composition root while :mod:`app.api.routes` is being edited
    elsewhere, and a second shadowed copy of a handler is invisible at runtime and
    confusing in the route-coverage analysis.

    Args:
        target: The application's main router, extended in place.
    """
    present = {
        (route.path, frozenset(getattr(route, "methods", ()) or ()))
        for route in target.routes
    }
    target.routes.extend(
        route
        for route in db_router.routes
        if (route.path, frozenset(getattr(route, "methods", ()) or ())) not in present
    )
