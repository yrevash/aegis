"""Every statement the console can run — assembled here, never accepted from a request.

# The decision: no free-form SQL box, and why

§7.9 offers three layers and an explicit cut order: *"hardened read-only path → schema
browser + saved parameterised queries on it → free-form SQL on the same path behind a
platform-admin toggle. Cut the box if something must go; never cut the path."* This
package ships the path and the first two layers, and **deliberately does not ship the
box**. Four arguments, in descending order of how much they carry:

1. **Row-level security and free-form SQL do not compose through string analysis.**
   Metabase — a mature product whose entire business is this problem — disables native
   SQL for any database with row or column security, because it cannot parse SQL well
   enough to know which tables a query touches. Aegis has ``tenant_isolation`` on
   nineteen relations. Refusing the same thing for the same reason is not timidity; it
   is the considered verdict of the only serious prior art there is.

2. **Aegis's own policy predicate fails open on an unset scope.** A generated statement
   carries :data:`TENANT_PREDICATE` — which does not — because this module builds it.
   A statement typed into a box carries whatever the typist wrote, and the layer
   underneath it does not restrict when the GUC is missing. The box would be the one
   surface in the product where the isolation story depends on the operator's SQL being
   correct.

3. **This repository has already refused the same shape once, one layer down.**
   :mod:`aegis.guardrails.patterns` refuses tenant-supplied regex because a pattern this
   process executes against attacker-influenced text is a denial-of-service control
   handed to the least-trusted writer. A statement this process executes against the
   whole database is the same argument with more behind it.

4. **The closed set is the better product.** "Which tenant is closest to its cap", "which
   documents failed ingestion", "what did this actor do last week" are the questions an
   operator actually opens a console to answer, and each is one click here instead of
   twenty minutes of remembered column names. A text box answers them worse.

**What is not cut is the path.** :class:`~aegis.dbadmin.runner.ReadOnlyRunner` executes a
:class:`~aegis.dbadmin.types.ReadQuery` and knows nothing about where it came from. If a
free-form front door is ever added it mounts on that same execution path — one path, two
front doors, exactly as §7.9 describes — and every control in :mod:`aegis.dbadmin.runner`
applies to it unchanged. The decision recorded here is about the front door only.

# The tenant filter

Every generated statement over a tenant-scoped relation carries :data:`TENANT_PREDICATE`
in its ``WHERE``, and there is no field on :class:`Inspection` in which to write a
``WHERE`` that replaces it — the same shape, for the same reason, as
:class:`aegis.analytics.provision.AnalyticsView`. Extra conditions are ``AND``-ed after
the predicate or they do not exist.

# Identifiers

Table and column names reach this module from a request. Not one of them is escaped.
Every one is matched against the live catalog — :meth:`TableInfo.column` and
:func:`table_named` — and a name that is not in the catalog is refused rather than
quoted. That is the discipline :data:`aegis.governance.rls._SAFE_ROLE_NAME` uses, and it
has the property escaping does not: a column this role's grants withhold is not in the
catalog, so it cannot be named in a projection, an ``ORDER BY`` or a predicate.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from aegis.dbadmin.scope import ALL_TENANTS_GUC, ALL_TENANTS_ON, TENANT_GUC
from aegis.dbadmin.types import DbAdminError, ReadQuery, TableInfo

__all__ = [
    "DEFAULT_ROW_LIMIT",
    "INSPECTIONS",
    "MAX_ROW_LIMIT",
    "TENANT_COLUMN",
    "TENANT_PREDICATE",
    "Inspection",
    "browse_query",
    "count_query",
    "inspection_named",
    "resolve_limit",
    "table_named",
]

#: The per-tenant discriminator column. A relation that has it is filtered; one that does
#: not (``tenants``, keyed by ``id``) is platform data and is readable only by a
#: platform-wide authority — enforced in :func:`browse_query`, not by convention.
TENANT_COLUMN = "tenant_id"

#: Rows returned to the screen by default. The runner fetches one more than the limit so
#: truncation is a **fact it observed**, not a guess from a full page.
DEFAULT_ROW_LIMIT = 100

#: The most rows any single read may return. §7.9 control 7: read-only is not the same as
#: harmless, and this page could otherwise dump every tenant to CSV in one request.
MAX_ROW_LIMIT = 1000

#: The row-visibility predicate every generated statement carries, and the whole of the
#: fail-closed property.
#:
#: * :data:`~aegis.dbadmin.scope.ALL_TENANTS_GUC` = ``'on'`` → every row. The deliberate,
#:   resolved opt-out, taken only by :func:`aegis.dbadmin.scope.binding_for`.
#: * ``app.tenant_id`` holding digits → that tenant's rows.
#: * **anything else — unset, empty, non-numeric — → no rows.** ``substring`` yields SQL
#:   NULL, ``NULL::int`` is NULL, and ``tenant_id = NULL`` is NULL, which is not true.
#:
#: Contrast :data:`aegis.governance.rls._TENANT_ISOLATION_PREDICATE`, whose first branch is
#: ``IS NULL`` and therefore does *not* restrict when nothing is bound. That is right for
#: the request path and catastrophic here, and the difference is the reason this constant
#: exists rather than a re-use.
#:
#: The ``substring(… from '^[0-9]+$')`` shape is borrowed from :mod:`aegis.governance.rls`
#: for the reason it is used there: it cannot raise. A bare ``''::int`` cast would error,
#: and PostgreSQL gives no evaluation-order guarantee that an ``OR`` guard would protect.
TENANT_PREDICATE = (
    f"(current_setting('{ALL_TENANTS_GUC}', true) = '{ALL_TENANTS_ON}'"
    f" OR {{alias}}.{TENANT_COLUMN} = substring("
    f"current_setting('{TENANT_GUC}', true) from '^[0-9]+$')::int)"
)

#: The alias every generated statement gives its source relation. A constant, so the
#: predicate above and the projection below cannot drift apart.
_ALIAS = "t"

#: An identifier this module is willing to place inside double quotes. Every name is also
#: matched against the live catalog before it gets here; this is the second gate, and it
#: is deliberately narrower than PostgreSQL allows — a name that would need escaping is
#: refused, not escaped.
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")


def _quoted(name: str) -> str:
    """Return ``name`` double-quoted, or raise if it is not a bare identifier.

    Args:
        name: An identifier already matched against the live catalog.

    Returns:
        The quoted identifier.

    Raises:
        DbAdminError: If it is not a bare SQL identifier. Reaching this means a catalog
            row carried a name this module will not interpolate, which is a refusal
            rather than an escape.
    """
    if not _SAFE_IDENTIFIER.match(name):
        raise DbAdminError(
            f"{name!r} is not a bare SQL identifier, so it is refused rather than "
            "escaped. Rename the object, or read it with psql."
        )
    return f'"{name}"'


def table_named(tables: Sequence[TableInfo], name: str) -> TableInfo:
    """Return the catalog entry for ``name``, or refuse.

    The single identifier gate for relation names. Every browse, count and inspection
    resolves its table through here, against the catalog **this role can see** — so a
    relation the console's grants withhold is indistinguishable from one that does not
    exist, which is the correct answer to give.

    Args:
        tables: The live catalog, as read by :meth:`ReadOnlyRunner.schema`.
        name: The relation a request asked for.

    Returns:
        The matching :class:`~aegis.dbadmin.types.TableInfo`.

    Raises:
        DbAdminError: If no readable relation of that name exists.
    """
    for table in tables:
        if table.name == name:
            return table
    raise DbAdminError(
        f"There is no readable table called {name!r}. Pick one from the schema list — a "
        "table this connection holds no SELECT grant on is not listed and cannot be read."
    )


def resolve_limit(limit: int | None) -> int:
    """Return the number of rows a read may **show**, defaulting and clamping.

    The number of rows the statement *asks* for is always one more than this — the extra
    row is what turns "was this truncated?" from a guess into an observation, and it is
    dropped before anything leaves :meth:`ReadOnlyRunner.run`. Callers pass this value as
    ``row_limit`` so the two halves cannot disagree.

    Args:
        limit: What the caller asked for, or ``None`` for the default.

    Returns:
        A row count in ``1 .. MAX_ROW_LIMIT``.

    Raises:
        DbAdminError: If a caller asked for fewer than one row.
    """
    if limit is None:
        return DEFAULT_ROW_LIMIT
    if limit < 1:
        raise DbAdminError("A read must ask for at least one row.")
    return min(limit, MAX_ROW_LIMIT)


def _predicate_for(table: TableInfo) -> str:
    """Return the tenant predicate for ``table``, or ``TRUE`` when it has no tenant.

    A relation with no ``tenant_id`` column is platform data. It gets no predicate here
    because there is no column to filter on — and :func:`browse_query` refuses to read
    one at all unless the authority is platform-wide, which is where that case is
    actually decided.
    """
    if not table.tenant_scoped:
        return "TRUE"
    return TENANT_PREDICATE.format(alias=_ALIAS)


def browse_query(
    table: TableInfo,
    *,
    platform_wide: bool,
    limit: int | None = None,
    order_by: str | None = None,
    after: Any | None = None,  # noqa: ANN401 - a key value of the table's own type
    filter_column: str | None = None,
    filter_value: Any | None = None,  # noqa: ANN401 - compared as text, bound never inlined
) -> ReadQuery:
    """Build the keyset-paginated browse of one table.

    Keyset, never ``OFFSET``: ``OFFSET 40000`` makes PostgreSQL produce and discard forty
    thousand rows on every page, so the page that is slowest to render is the one an
    operator reaches by scrolling — and it is inconsistent under concurrent writes.
    Paging on the ordering column's last value is O(1) in the page number.

    Args:
        table: The catalog entry, from :func:`table_named`. Every identifier below is
            resolved against it.
        platform_wide: Whether the caller's resolved authority is every tenant. A table
            with no ``tenant_id`` column is platform data and is refused to anyone else,
            because :data:`TENANT_PREDICATE` has no column to narrow it with and "no
            predicate" must never mean "no restriction" on this page.
        limit: Rows to return, clamped to :data:`MAX_ROW_LIMIT`.
        order_by: Ordering column. Defaults to the primary key's first column.
        after: Keyset cursor — the ordering column's value on the last row of the
            previous page. Bound as a parameter; never interpolated.
        filter_column: Optional equality filter column, matched against the catalog.
        filter_value: Its value, bound as a parameter.

    Returns:
        The statement plus its bound parameters.

    Raises:
        DbAdminError: If the table is unbrowsable, an identifier is not in the catalog,
            or platform data was asked for by a tenant-scoped authority.
    """
    if not table.columns:
        raise DbAdminError(
            f"This connection may read no columns of {table.name!r}, so there is nothing "
            "to show. That is a grant, not an empty table."
        )
    if not table.tenant_scoped and not platform_wide:
        raise DbAdminError(
            f"{table.name!r} carries no {TENANT_COLUMN} column, so no tenant filter can be "
            "applied to it. It is readable only under a platform-wide authority; this "
            "sign-in is scoped to one tenant."
        )

    order_name = order_by or (table.primary_key[0] if table.primary_key else "")
    if not order_name:
        raise DbAdminError(
            f"{table.name!r} has no primary key and no ordering column was given, so it "
            "cannot be paged consistently. Name a column to order by."
        )
    order_column = table.column(order_name)

    projection = ", ".join(
        f"{_ALIAS}.{_quoted(column.name)}" for column in table.columns
    )
    clauses = [
        f"SELECT {projection}",
        f"FROM {_quoted(table.name)} {_ALIAS}",
        f"WHERE {_predicate_for(table)}",
    ]
    params: dict[str, Any] = {"row_limit": resolve_limit(limit) + 1}
    if after is not None:
        clauses.append(f"  AND {_ALIAS}.{_quoted(order_column.name)} > :cursor")
        params["cursor"] = after
    if filter_column is not None:
        filtered = table.column(filter_column)
        clauses.append(f"  AND {_ALIAS}.{_quoted(filtered.name)}::text = :filter_value")
        params["filter_value"] = "" if filter_value is None else str(filter_value)
    clauses.append(f"ORDER BY {_ALIAS}.{_quoted(order_column.name)}")
    clauses.append("LIMIT :row_limit")
    return ReadQuery(
        sql="\n".join(clauses),
        params=params,
        label=f"Browse {table.name}",
        table=table.name,
        tenant_filtered=table.tenant_scoped,
    )


def count_query(table: TableInfo, *, platform_wide: bool) -> ReadQuery:
    """Build the **exact** row count for one table, under the same tenant predicate.

    Separate from :func:`browse_query` and never run automatically: an exact count is a
    full scan on a large relation, and the schema browser shows ``pg_class.reltuples``
    instead. §7.9: *"row-count estimates from pg_class.reltuples with exact counts only
    on request"*.

    Args:
        table: The catalog entry.
        platform_wide: The caller's resolved authority, as in :func:`browse_query`.

    Returns:
        A one-row, one-column statement.

    Raises:
        DbAdminError: If platform data was asked for by a tenant-scoped authority.
    """
    if not table.tenant_scoped and not platform_wide:
        raise DbAdminError(
            f"{table.name!r} carries no {TENANT_COLUMN} column and is readable only under "
            "a platform-wide authority."
        )
    return ReadQuery(
        sql=(
            f"SELECT count(*) AS exact_rows\n"
            f"FROM {_quoted(table.name)} {_ALIAS}\n"
            f"WHERE {_predicate_for(table)}\n"
            f"LIMIT :row_limit"
        ),
        params={"row_limit": 1},
        label=f"Count {table.name}",
        table=table.name,
        tenant_filtered=table.tenant_scoped,
    )


@dataclass(frozen=True, slots=True)
class Inspection:
    """One curated, parameterised read — a question an operator actually asks.

    Deliberately **not** a free-text ``SELECT``. An inspection is described by its parts,
    and :meth:`build` welds :data:`TENANT_PREDICATE` into the ``WHERE``. That is the point
    of the shape: an inspection added next month cannot forget the tenant filter, because
    there is no field in which to write a ``WHERE`` that replaces it.

    Attributes:
        id: Stable identifier the screen sends. Never SQL.
        title: What the operator is asking, in their words.
        summary: What the answer means, in one sentence. Required for the same reason a
            :class:`~aegis.settings.spec.SettingSpec` needs one.
        source: The relation it reads. Named so a schema change has a search target.
        projection: The ``SELECT`` list, written against the alias ``t``.
        tenant_scoped: Whether ``source`` carries ``tenant_id``. Declared rather than
            derived so an inspection over a platform table is a visible choice; verified
            against the live catalog by :meth:`build`.
        where: Extra conditions, ``AND``-ed **after** the tenant predicate. Never
            replaces it. May reference the named parameters in :attr:`parameters`.
        group_by: The ``GROUP BY`` list, or empty for a row-level inspection.
        order_by: The ``ORDER BY`` list. Required — an unordered read paged by a limit
            returns an arbitrary subset and calls it an answer.
        parameters: Named bind parameters this inspection accepts, mapped to the default
            used when the caller supplies none. A parameter absent from here is refused,
            so the request cannot introduce a name the SQL does not expect.
    """

    id: str
    title: str
    summary: str
    source: str
    projection: str
    order_by: str
    tenant_scoped: bool = True
    where: str = ""
    group_by: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)

    def build(
        self, table: TableInfo, *, limit: int | None = None, values: dict[str, Any] | None = None
    ) -> ReadQuery:
        """Assemble this inspection into a runnable statement.

        Args:
            table: The live catalog entry for :attr:`source`. Passed in rather than
                looked up so the caller has already proved the relation is readable.
            limit: Rows to return, clamped to :data:`MAX_ROW_LIMIT`.
            values: Caller-supplied values for :attr:`parameters`. Every key must be
                declared; an undeclared one is refused rather than ignored, because a
                silently dropped filter answers a different question than the one asked.

        Returns:
            The statement plus its bound parameters.

        Raises:
            DbAdminError: If the catalog disagrees with :attr:`tenant_scoped`, or a
                caller supplied an undeclared parameter.
        """
        if table.name != self.source:
            raise DbAdminError(
                f"Inspection {self.id!r} reads {self.source!r} and was handed the catalog "
                f"entry for {table.name!r}."
            )
        if table.tenant_scoped != self.tenant_scoped:
            raise DbAdminError(
                f"Inspection {self.id!r} declares tenant_scoped={self.tenant_scoped} but "
                f"the live {table.name!r} {'has' if table.tenant_scoped else 'has no'} a "
                f"{TENANT_COLUMN} column. The schema moved; the inspection has not."
            )
        supplied = dict(values or {})
        unknown = sorted(set(supplied) - set(self.parameters))
        if unknown:
            raise DbAdminError(
                f"Inspection {self.id!r} takes no parameter called {unknown[0]!r}. It "
                f"accepts: {', '.join(sorted(self.parameters)) or 'none'}."
            )
        params: dict[str, Any] = dict(self.parameters)
        params.update(supplied)
        params["row_limit"] = resolve_limit(limit) + 1

        clauses = [
            f"SELECT {self.projection}",
            f"FROM {_quoted(self.source)} {_ALIAS}",
            f"WHERE {_predicate_for(table)}",
        ]
        if self.where:
            clauses.append(f"  AND {self.where}")
        if self.group_by:
            clauses.append(f"GROUP BY {self.group_by}")
        clauses.append(f"ORDER BY {self.order_by}")
        clauses.append("LIMIT :row_limit")
        return ReadQuery(
            sql="\n".join(clauses),
            params=params,
            label=self.title,
            table=self.source,
            tenant_filtered=self.tenant_scoped,
        )


#: The closed set. Every entry answers a question an operator opened this page to ask,
#: and every one is a projection of a table Aegis already writes on every run — nothing
#: here is invented, and nothing here needs a schema change to keep working.
INSPECTIONS: tuple[Inspection, ...] = (
    Inspection(
        id="spend_by_tenant",
        title="Spend by tenant",
        summary=(
            "What each tenant has spent and how many calls it took, over the last N days, "
            "summed from the same usage ledger the budget enforcer reads."
        ),
        source="usage_ledger",
        projection=(
            "t.tenant_id AS tenant_id, "
            "count(*) AS calls, "
            "sum(t.prompt_tokens + t.completion_tokens) AS tokens, "
            "round(sum(t.cost_usd)::numeric, 4) AS cost_usd, "
            "max(t.ts) AS last_call"
        ),
        where="t.ts >= now() - make_interval(days => :days)",
        group_by="t.tenant_id",
        order_by="4 DESC NULLS LAST",
        parameters={"days": 30},
    ),
    Inspection(
        id="spend_by_model",
        title="Spend by model",
        summary=(
            "Which deployments the money went to over the last N days. The deployment "
            "that answered, not the tier that was asked for."
        ),
        source="usage_ledger",
        projection=(
            "coalesce(t.model, 'unattributed') AS model, "
            "count(*) AS calls, "
            "sum(t.prompt_tokens) AS prompt_tokens, "
            "sum(t.completion_tokens) AS completion_tokens, "
            "round(sum(t.cost_usd)::numeric, 4) AS cost_usd"
        ),
        where="t.ts >= now() - make_interval(days => :days)",
        group_by="1",
        order_by="5 DESC NULLS LAST",
        parameters={"days": 30},
    ),
    Inspection(
        id="recent_audit",
        title="Recent audit trail",
        summary=(
            "The last actions recorded, newest first — who did what, under which trace, "
            "and who approved it."
        ),
        source="audit_log",
        projection=(
            "t.ts AS ts, t.tenant_id AS tenant_id, t.action AS action, "
            "t.actor AS actor, t.model AS model, t.approved_by AS approved_by, "
            "t.trace_id AS trace_id"
        ),
        order_by="t.ts DESC",
    ),
    Inspection(
        id="audit_by_actor",
        title="Audit trail for one actor",
        summary=(
            "Everything one principal did, filtered in SQL rather than out of a page that "
            "was already limited — the distinction that decides whether the answer is true."
        ),
        source="audit_log",
        projection=(
            "t.ts AS ts, t.tenant_id AS tenant_id, t.action AS action, "
            "t.actor AS actor, t.payload AS payload"
        ),
        where="t.actor = :actor",
        order_by="t.ts DESC",
        parameters={"actor": ""},
    ),
    Inspection(
        id="failed_jobs",
        title="Jobs that failed or stalled",
        summary=(
            "The durable job substrate's failures and the rows the reconciler could not "
            "account for, newest first, with the error each run recorded. The first place "
            "to look when an upload never became an answer."
        ),
        source="job_runs",
        projection=(
            "t.created_at AS created_at, t.tenant_id AS tenant_id, t.job_type AS job_type, "
            "t.status AS status, t.completed_stage AS completed_stage, "
            "t.finished_at AS finished_at, t.error AS error"
        ),
        where="t.status::text IN ('failed', 'reconciling')",
        order_by="t.created_at DESC",
    ),
    Inspection(
        id="documents_by_status",
        title="Documents by ingestion status",
        summary=(
            "How far each uploaded document got through ingestion, and how many chunks it "
            "produced. A document with zero chunks is a document no answer can cite."
        ),
        source="documents",
        projection=(
            "t.tenant_id AS tenant_id, t.status AS status, count(*) AS documents, "
            "sum(t.chunk_count) AS chunks, max(t.created_at) AS newest"
        ),
        group_by="t.tenant_id, t.status",
        order_by="1, 2",
    ),
    Inspection(
        id="pending_approvals",
        title="Approvals still waiting",
        summary=(
            "Every human gate that has been raised and not decided, oldest first. An "
            "approval nobody sees is a run that never finishes."
        ),
        source="approvals",
        projection=(
            "t.created_at AS created_at, t.tenant_id AS tenant_id, t.action AS action, "
            "t.risk AS risk, t.status AS status, t.requested_by AS requested_by"
        ),
        where="t.status::text = 'pending'",
        order_by="t.created_at",
    ),
    Inspection(
        id="users_by_tenant",
        title="Who is in each tenant",
        summary=(
            "The roster, with the role each account holds. Password hashes are withheld "
            "from this connection by a column-level grant, so they are not in the "
            "projection and not in the catalog either."
        ),
        source="users",
        projection=(
            "t.tenant_id AS tenant_id, t.id AS id, t.username AS username, "
            "t.role AS role, t.is_active AS is_active"
        ),
        order_by="t.tenant_id, t.username",
    ),
)


def inspection_named(inspection_id: str) -> Inspection:
    """Return the inspection with this id, or refuse.

    Args:
        inspection_id: The id a request sent.

    Returns:
        The matching :class:`Inspection`.

    Raises:
        DbAdminError: If no inspection carries that id. There is no fallback to a
            free-form statement, because there is no free-form statement.
    """
    for inspection in INSPECTIONS:
        if inspection.id == inspection_id:
            return inspection
    raise DbAdminError(
        f"There is no inspection called {inspection_id!r}. This console runs a closed set "
        f"of reads: {', '.join(item.id for item in INSPECTIONS)}."
    )
