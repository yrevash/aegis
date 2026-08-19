"""The pipeline: what Superset actually reads, and the role it reads it as.

A guest token pointed at nothing is not an integration. This module is the other half —
the **datasets** Superset connects to, and the Postgres role it connects as. Both are
generated as SQL from code here rather than clicked into existence, so the analytics
layer is reproducible from the repository and survives the loss of somebody's
``superset.db``.

**Six views, over tables that already exist.** Nothing is invented: each view is a
tenant-labelled projection of a table Aegis already writes on every run — the usage
ledger, the run header, the approvals inbox, the durable job substrate, the red-team
history and the audit trail. Every one carries ``tenant_id`` as its first column,
because that column is what the guest token's row-level-security clause filters on.
A view with no ``tenant_id`` cannot be made safe by any token.

**The role, and the one property that makes it defence in depth.** Superset connects as
a dedicated ``NOSUPERUSER NOBYPASSRLS`` role with ``SELECT`` and nothing else — never as
the table owner, because PostgreSQL skips row security entirely for a superuser or a
``BYPASSRLS`` role, and an owner-connected Superset would make Aegis's thirteen
``tenant_isolation`` policies inert for every query it ran.

**Why the views are handed to that role.** A view executes its query with the
privileges of the view's *owner*. A view owned by the table owner and read by Superset
would therefore reach the base tables as the owner — and if that owner is a superuser,
row security is skipped and the view is a hole straight through the policy. This is the
same shape as the partition bug this project already found the expensive way: *a
parent's policy does not protect what is reached by another name*. So the DDL below
creates each view and immediately ``ALTER VIEW … OWNER TO`` the read-only role. That
makes the base-table access run as a role that is subject to RLS, on **every**
PostgreSQL version — where ``security_invoker = true`` (the tidier spelling) would need
15 or later.

Emitted as SQL rather than executed here, and deliberately: this never runs at Aegis
boot. Superset is optional, provisioning it is an operator action, and a review-able
``.sql`` file is a better artefact than a migration that ran once on a laptop::

    python -m aegis.analytics --role aegis_superset --password '…' > analytics.sql
    psql -d aegis -f analytics.sql
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "ANALYTICS_VIEWS",
    "READ_ONLY_ROLE",
    "SOURCE_TABLES",
    "AnalyticsView",
    "provisioning_sql",
    "provisioning_statements",
    "revocation_statements",
]

#: The default name of the Postgres role Superset connects as. Never the table owner.
READ_ONLY_ROLE = "aegis_superset"

#: A role name this module is willing to interpolate into DDL. ``CREATE ROLE`` and
#: ``GRANT`` take no bind parameter for their subject, so the name is interpolated — and
#: anything interpolated is validated rather than trusted.
_SAFE_ROLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")


@dataclass(frozen=True)
class AnalyticsView:
    """One Superset dataset: a tenant-labelled projection of an Aegis table.

    Args:
        name: The view's name, and the Superset dataset's name.
        summary: What an operator is looking at, in one sentence.
        source: The Aegis table it reads. Named so a schema change has a search target.
        sql: The ``SELECT`` body. Carries **no** tenant predicate of its own: narrowing
            is the guest token's row-level-security clause, plus Aegis's own query
            filter, plus the base table's ``tenant_isolation`` policy reached through
            the read-only owner. Baking a fixed tenant into a view would be a fourth,
            unmaintainable copy of the same rule.
    """

    name: str
    summary: str
    source: str
    sql: str


#: Every Aegis table the analytics views read. The read-only role gets ``SELECT`` on
#: exactly these and nothing else — no ``chat_messages``, no ``memory_*``, no
#: ``documents``: a business dashboard has no business reading conversation content.
SOURCE_TABLES: tuple[str, ...] = (
    "usage_ledger",
    "runs",
    "approvals",
    "job_runs",
    "redteam_runs",
    "audit_log",
)


ANALYTICS_VIEWS: tuple[AnalyticsView, ...] = (
    AnalyticsView(
        name="analytics_spend_daily",
        summary="Model spend and token volume per day and deployment, from the usage ledger.",
        source="usage_ledger",
        sql="""
            SELECT
                u.tenant_id                              AS tenant_id,
                date_trunc('day', u.ts)                  AS day,
                COALESCE(u.model, 'unattributed')        AS model,
                COUNT(*)                                 AS calls,
                SUM(u.prompt_tokens)                     AS prompt_tokens,
                SUM(u.completion_tokens)                 AS completion_tokens,
                SUM(u.prompt_tokens + u.completion_tokens) AS total_tokens,
                SUM(u.cost_usd)                          AS cost_usd
            FROM usage_ledger u
            GROUP BY 1, 2, 3
        """,
    ),
    AnalyticsView(
        name="analytics_runs_daily",
        summary="Agent runs per day and outcome, with latency and attributed cost.",
        source="runs",
        sql="""
            SELECT
                r.tenant_id                                  AS tenant_id,
                date_trunc('day', r.started_at)              AS day,
                COALESCE(r.status::text, 'in_flight')        AS status,
                COUNT(*)                                     AS runs,
                SUM(CASE WHEN r.cache_hit THEN 1 ELSE 0 END) AS cache_hits,
                AVG(r.duration_ms)                           AS avg_duration_ms,
                MAX(r.duration_ms)                           AS max_duration_ms,
                SUM(r.cost_usd)                              AS cost_usd,
                SUM(r.approval_count)                        AS approvals_raised,
                SUM(r.guardrail_block_count)                 AS guardrail_blocks
            FROM runs r
            WHERE r.started_at IS NOT NULL
            GROUP BY 1, 2, 3
        """,
    ),
    AnalyticsView(
        name="analytics_approvals_daily",
        summary="The human gate: how many runs paused, at what risk, and how they ended.",
        source="approvals",
        sql="""
            SELECT
                a.tenant_id                     AS tenant_id,
                date_trunc('day', a.created_at) AS day,
                a.status::text                  AS status,
                a.risk::text                    AS risk,
                COUNT(*)                        AS gates,
                AVG(
                    EXTRACT(EPOCH FROM (a.decided_at - a.created_at))
                )                               AS avg_decision_seconds
            FROM approvals a
            GROUP BY 1, 2, 3, 4
        """,
    ),
    AnalyticsView(
        name="analytics_jobs_daily",
        summary="The durable job substrate: throughput and failure rate per job type.",
        source="job_runs",
        sql="""
            SELECT
                j.tenant_id                     AS tenant_id,
                date_trunc('day', j.created_at) AS day,
                j.job_type                      AS job_type,
                j.status::text                  AS status,
                COUNT(*)                        AS jobs,
                SUM(j.cost_usd)                 AS cost_usd,
                AVG(
                    EXTRACT(EPOCH FROM (j.finished_at - j.started_at))
                )                               AS avg_runtime_seconds
            FROM job_runs j
            GROUP BY 1, 2, 3, 4
        """,
    ),
    AnalyticsView(
        name="analytics_redteam_runs",
        summary="Every red-team run: which battery, offline or live, and what it blocked.",
        source="redteam_runs",
        sql="""
            SELECT
                t.tenant_id             AS tenant_id,
                t.run_id                AS run_id,
                t.started_at            AS started_at,
                t.suite                 AS suite,
                t.mode                  AS mode,
                t.attacks_total         AS attacks_total,
                t.attacks_blocked       AS attacks_blocked,
                t.block_rate            AS block_rate,
                t.false_positive_rate   AS false_positive_rate,
                t.passed                AS passed,
                t.duration_ms           AS duration_ms
            FROM redteam_runs t
        """,
    ),
    AnalyticsView(
        name="analytics_audit_daily",
        summary="The governance trail: which actions were recorded, per day.",
        source="audit_log",
        sql="""
            SELECT
                l.tenant_id             AS tenant_id,
                date_trunc('day', l.ts) AS day,
                l.action                AS action,
                COUNT(*)                AS events
            FROM audit_log l
            GROUP BY 1, 2, 3
        """,
    ),
)


def _check_role(role: str) -> str:
    """Return ``role`` if it is a bare SQL identifier, else raise."""
    if not _SAFE_ROLE.match(role):
        raise ValueError(
            f"{role!r} is not a bare SQL identifier. It is interpolated into CREATE ROLE "
            "and GRANT, which take no bind parameter for their subject, so it is refused "
            "rather than quoted."
        )
    return role


def _squash(sql: str) -> str:
    """Collapse a triple-quoted view body into one tidy indented block."""
    lines = [line.rstrip() for line in sql.strip("\n").rstrip().split("\n")]
    indents = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
    trim = min(indents) if indents else 0
    return "\n".join(line[trim:] if line.strip() else "" for line in lines)


def provisioning_statements(
    role: str = READ_ONLY_ROLE, *, password: str | None = None
) -> tuple[str, ...]:
    """Return the ordered DDL that provisions the analytics pipeline.

    Idempotent end to end: safe to re-run after a schema change, which is when it is
    most likely to be run.

    The ordering is the argument:

    1. create the read-only role (only when a password is supplied — an existing role is
       left alone rather than having its password reset out from under the operator);
    2. let it reach the schema and the six source tables, read-only;
    3. create each view, then **hand the view to that role**, so the base-table access a
       view performs runs as a role that PostgreSQL applies row security to;
    4. take back ``CREATE`` on the schema, which was needed only to accept ownership.

    Args:
        role: The Postgres role Superset connects as.
        password: Its password. ``None`` skips ``CREATE ROLE`` entirely, for a
            deployment that provisions roles elsewhere.

    Returns:
        The statements, in order.

    Raises:
        ValueError: If ``role`` is not a bare SQL identifier.
    """
    _check_role(role)
    out: list[str] = [
        "-- Aegis analytics pipeline. Generated by `python -m aegis.analytics`.\n"
        "-- Run as the OWNER of the Aegis tables. Idempotent."
    ]
    if password is not None:
        if "'" in password:
            raise ValueError("the analytics role's password may not contain a single quote")
        out.append(
            f"DO $$ BEGIN\n"
            f"  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN\n"
            f"    CREATE ROLE {role} LOGIN PASSWORD '{password}'\n"
            f"      NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE INHERIT;\n"
            f"  END IF;\n"
            f"END $$;"
        )
    out.append(f"GRANT USAGE ON SCHEMA public TO {role};")
    for table in SOURCE_TABLES:
        out.append(f"GRANT SELECT ON TABLE {table} TO {role};")
    # Needed only so ALTER VIEW … OWNER TO is accepted: PostgreSQL requires the incoming
    # owner to hold CREATE on the view's schema. Revoked again at the end, so the role
    # Superset connects with cannot create anything.
    out.append(f"GRANT CREATE ON SCHEMA public TO {role};")
    for view in ANALYTICS_VIEWS:
        out.append(
            f"-- {view.summary} (source: {view.source})\n"
            f"CREATE OR REPLACE VIEW {view.name} AS\n{_squash(view.sql)};"
        )
        out.append(f"ALTER VIEW {view.name} OWNER TO {role};")
        out.append(f"GRANT SELECT ON TABLE {view.name} TO {role};")
    out.append(f"REVOKE CREATE ON SCHEMA public FROM {role};")
    return tuple(out)


def revocation_statements(role: str = READ_ONLY_ROLE) -> tuple[str, ...]:
    """Return the DDL that removes the analytics pipeline again.

    Kept beside the provisioning so "turn this off" is a documented operation rather
    than an archaeology exercise. Does **not** drop the role: a role may own objects in
    other databases, and a provisioning script that drops roles is a provisioning script
    that eventually drops the wrong one.
    """
    _check_role(role)
    out = [f"DROP VIEW IF EXISTS {view.name};" for view in reversed(ANALYTICS_VIEWS)]
    out.extend(f"REVOKE SELECT ON TABLE {table} FROM {role};" for table in SOURCE_TABLES)
    out.append(f"REVOKE USAGE ON SCHEMA public FROM {role};")
    return tuple(out)


def provisioning_sql(role: str = READ_ONLY_ROLE, *, password: str | None = None) -> str:
    """The provisioning DDL as one runnable script."""
    return "\n\n".join(provisioning_statements(role, password=password)) + "\n"
