"""Aegis DB console — looking at the data without leaving the product, or `psql`.

The requirement is *"view full db, not go into code or db checking"*, and the naive
implementation of it — ``await conn.execute(user_sql)`` on the application connection — is
unsafe in five distinct, **measured** ways (§7.9). This package is the safe version, and
its shape is decided entirely by those measurements:

* :mod:`aegis.dbadmin.provision` — the ``aegis_readonly`` login role. ``NOSUPERUSER
  NOBYPASSRLS``, owning nothing, holding ``SELECT`` and nothing else, with
  ``users.password_hash`` withheld by a **column** grant so the catalog stops listing it.
* :mod:`aegis.dbadmin.runner` — the one execution path. Its own engine and pool (never
  ``SET ROLE`` on the app connection), every statement over the extended protocol, a
  statement timeout, a read-only transaction that never commits, row and byte caps that
  *say what they cut*, and an ``EXPLAIN`` pre-flight that refuses an expensive read before
  it runs instead of timing out inside it. It re-reads the connection's privileges before
  every query and refuses to run at all if that connection can write.
* :mod:`aegis.dbadmin.scope` — the sealed tenant authority, and the two GUCs a read runs
  under. Three inputs, three outcomes, one of them an exception.
* :mod:`aegis.dbadmin.catalogue` — every statement the console can run: the schema browse
  with keyset pagination, and a closed set of parameterised inspections. **There is no
  free-form SQL box, and the argument for that is written where the decision lives**, at
  the top of that module.

The tenant filter is the part worth reading twice.
:data:`aegis.dbadmin.catalogue.TENANT_PREDICATE` is welded into every generated statement
and is **not** null-tolerant, because Aegis's own ``tenant_isolation`` policy is: an unset
``app.tenant_id`` does not restrict, so a database page that forgot to bind a scope would
return every tenant's rows and look healthy doing it. Here, nothing bound means no rows.
"""

from __future__ import annotations

from aegis.dbadmin.catalogue import (
    DEFAULT_ROW_LIMIT,
    INSPECTIONS,
    MAX_ROW_LIMIT,
    TENANT_PREDICATE,
    Inspection,
    browse_query,
    count_query,
    inspection_named,
    resolve_limit,
    table_named,
)
from aegis.dbadmin.provision import (
    READONLY_ROLE,
    WITHHELD_COLUMNS,
    provisioning_sql,
    provisioning_statements,
    revocation_statements,
)
from aegis.dbadmin.runner import (
    DEFAULT_MAX_PLAN_COST,
    MAX_RESULT_BYTES,
    STATEMENT_TIMEOUT_MS,
    ReadOnlyRunner,
    verify_posture,
)
from aegis.dbadmin.scope import (
    ALL_TENANTS_GUC,
    TENANT_GUC,
    ScopeBinding,
    binding_for,
    narrow_to,
)
from aegis.dbadmin.types import (
    Column,
    ConsoleDisabledError,
    DbAdminError,
    ForeignKey,
    PlanTooExpensiveError,
    QueryResult,
    ReadOnlyPosture,
    ReadQuery,
    TableInfo,
    UnsafeRoleError,
)

__all__ = [
    "ALL_TENANTS_GUC",
    "DEFAULT_MAX_PLAN_COST",
    "DEFAULT_ROW_LIMIT",
    "INSPECTIONS",
    "MAX_RESULT_BYTES",
    "MAX_ROW_LIMIT",
    "READONLY_ROLE",
    "STATEMENT_TIMEOUT_MS",
    "TENANT_GUC",
    "TENANT_PREDICATE",
    "WITHHELD_COLUMNS",
    "Column",
    "ConsoleDisabledError",
    "DbAdminError",
    "ForeignKey",
    "Inspection",
    "PlanTooExpensiveError",
    "QueryResult",
    "ReadOnlyPosture",
    "ReadOnlyRunner",
    "ReadQuery",
    "ScopeBinding",
    "TableInfo",
    "UnsafeRoleError",
    "binding_for",
    "browse_query",
    "count_query",
    "inspection_named",
    "narrow_to",
    "provisioning_sql",
    "provisioning_statements",
    "resolve_limit",
    "revocation_statements",
    "table_named",
    "verify_posture",
]
