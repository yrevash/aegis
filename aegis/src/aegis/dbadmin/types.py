"""The shapes the database console passes around, and the refusals it raises.

Nothing here does I/O. The types exist so the two facts that matter about a read —
**which authority it ran under** and **what it did not show you** — are fields on a
value rather than something a caller has to remember to compute.

:class:`ReadQuery` in particular is the seam. It is the *only* thing
:class:`~aegis.dbadmin.runner.ReadOnlyRunner` will execute, it has no public
constructor that takes free text, and every instance in the product is built by
:mod:`aegis.dbadmin.catalogue` out of identifiers matched against the live catalog.
That is what makes "the page cannot send a write" a property of the type system rather
than of a regex somebody has to keep ahead of an attacker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "Column",
    "ConsoleDisabledError",
    "DbAdminError",
    "ForeignKey",
    "PlanTooExpensiveError",
    "QueryResult",
    "ReadOnlyPosture",
    "ReadQuery",
    "TableInfo",
    "UnsafeRoleError",
]


class DbAdminError(RuntimeError):
    """Base class for every refusal this package raises.

    Every subclass carries a sentence a person can act on, because these reach an
    operator's screen verbatim through ``web/src/lib/api/apiError.ts``. "Request failed"
    is not a refusal, it is a shrug.
    """


class ConsoleDisabledError(DbAdminError):
    """The database console is switched off for this deployment.

    Its own class rather than a boolean return, so a caller cannot forget to check: a
    disabled console that quietly answers is the failure mode the kill switch exists
    to prevent.
    """


class UnsafeRoleError(DbAdminError):
    """The connection offered to the console can write, or can bypass row security.

    Raised by :func:`aegis.dbadmin.runner.verify_posture` **before** any query runs.
    The read-only property of this page is the *privilege* the role holds, not a
    setting and not a keyword filter (finding 4 of §7.9: the read-only role turned its
    own ``default_transaction_read_only`` off, and what stopped the write was the
    absent grant). So the privilege is re-read over the very connection the queries
    will use, and a connection that fails the check serves nothing at all.
    """


class PlanTooExpensiveError(DbAdminError):
    """The planner's estimate for this read exceeded the configured ceiling.

    A separate class from the generic refusal because it carries a *remedy* — narrow
    the scope, or ask for fewer rows — where a timeout carries none. Turning "it timed
    out after 10 seconds" into "this would scan 40 million rows" is the whole point of
    the pre-flight.
    """


@dataclass(frozen=True, slots=True)
class ReadQuery:
    """One statement the runner is willing to execute, plus its bound parameters.

    Attributes:
        sql: The statement text. Assembled by :mod:`aegis.dbadmin.catalogue` from
            identifiers matched against the live catalog — never from a request body.
        params: The bind parameters. **Never empty**: a statement sent with at least
            one bind travels the PostgreSQL *extended* protocol, which refuses
            multiple commands in one message outright (``cannot insert multiple
            commands into a prepared statement``). Finding 2 of §7.9 makes *how the
            query is sent* a control, and it is free.
        label: What this read is, in one phrase, for the audit row and the screen.
        table: The relation being read, when there is exactly one. ``None`` for a
            catalog query that spans several.
        tenant_filtered: Whether :data:`aegis.dbadmin.catalogue.TENANT_PREDICATE` is
            welded into this statement's ``WHERE``. Recorded rather than inferred, so
            the audit row states the isolation that actually applied.
    """

    sql: str
    params: dict[str, Any]
    label: str
    table: str | None = None
    tenant_filtered: bool = False

    def __post_init__(self) -> None:
        """Refuse a query with no bind parameters.

        Raises:
            ValueError: If ``params`` is empty, which would let SQLAlchemy's asyncpg
                dialect fall back to the simple protocol and silently run several
                statements from one string (finding 1 of §7.9).
        """
        if not self.params:
            raise ValueError(
                "a ReadQuery must carry at least one bind parameter so it travels the "
                "extended protocol, which refuses multi-statement text. Add the row "
                "limit or the scope binding to params rather than inlining it."
            )


@dataclass(frozen=True, slots=True)
class Column:
    """One column of one table, as the *permission model* reports it.

    A column the console's role holds no ``SELECT`` grant on does not appear here at
    all, because ``information_schema.columns`` respects column-level grants
    (finding 5 of §7.9). That is why this package has no denylist: withholding
    ``users.password_hash`` from the role removes it from the browser, from every
    generated projection and from every predicate, with nothing to drift.

    Attributes:
        name: The column name.
        data_type: The SQL type, as ``information_schema`` spells it.
        nullable: Whether it accepts NULL.
        is_primary_key: Whether it participates in the primary key.
    """

    name: str
    data_type: str
    nullable: bool
    is_primary_key: bool = False


@dataclass(frozen=True, slots=True)
class ForeignKey:
    """One outgoing reference, for navigating from a row to what it points at."""

    column: str
    references_table: str
    references_column: str


@dataclass(frozen=True, slots=True)
class TableInfo:
    """One browsable relation: its readable columns, its key, and how big it is.

    Attributes:
        name: The relation name.
        columns: Every column the console's role may read, in ordinal order.
        primary_key: The primary-key columns, in key order. Empty when the table has
            none, which makes it unbrowsable by keyset — reported, not paged over with
            ``OFFSET``.
        foreign_keys: Outgoing references, for foreign-key navigation.
        row_estimate: ``pg_class.reltuples``, i.e. the planner's estimate. Never
            presented as an exact count, because it is not one.
        tenant_scoped: Whether the table carries a ``tenant_id`` column. Derived from
            the live catalog rather than from a registry, so a new table is covered the
            moment it exists.
        withheld_columns: Columns the catalog knows the table has but this role may not
            read. Named so the browser says *"one column is withheld from this role"*
            rather than silently showing a narrower table than the one that exists.
    """

    name: str
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...] = ()
    foreign_keys: tuple[ForeignKey, ...] = ()
    row_estimate: int = 0
    tenant_scoped: bool = False
    withheld_columns: tuple[str, ...] = ()

    def column(self, name: str) -> Column:
        """Return the readable column called ``name``.

        The identifier gate for everything this package interpolates. An ordering
        column, a keyset cursor column and a filter column all arrive from a request,
        and all three are resolved through here — matched against the catalog list,
        never escaped, which is the discipline
        :data:`aegis.governance.rls._SAFE_ROLE_NAME` uses for role names.

        Args:
            name: The column name a request asked for.

        Returns:
            The matching :class:`Column`.

        Raises:
            DbAdminError: If no readable column of that name exists — which is also
                the answer for a column this role's grants withhold.
        """
        for candidate in self.columns:
            if candidate.name == name:
                return candidate
        known = ", ".join(column.name for column in self.columns) or "none"
        raise DbAdminError(
            f"{self.name!r} has no readable column called {name!r}. This connection may "
            f"read: {known}. A column withheld by a column-level grant is not readable "
            f"and is not listed."
        )


@dataclass(frozen=True, slots=True)
class QueryResult:
    """The rows, and — just as importantly — what was left out of them.

    Attributes:
        columns: Column names, in projection order.
        rows: The rows actually returned, already capped.
        row_count: ``len(rows)``. Carried explicitly so a caller serialising this does
            not have to recompute it from a generator it has consumed.
        truncated: Whether more rows existed than were returned, or the byte cap fired.
        truncation_reason: The sentence naming *what* was cut and by which bound.
            Empty exactly when ``truncated`` is false. Saying what was truncated rather
            than truncating silently is the difference between a bounded answer and a
            wrong one.
        duration_ms: Server round-trip for the statement, measured here.
        approx_bytes: Serialised size of the rows returned, against the byte cap.
        plan_cost: The planner's total-cost estimate from the pre-flight ``EXPLAIN``.
        plan_summary: The top plan node, e.g. ``Seq Scan on usage_ledger``.
    """

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    row_count: int
    truncated: bool = False
    truncation_reason: str = ""
    duration_ms: float = 0.0
    approx_bytes: int = 0
    plan_cost: float = 0.0
    plan_summary: str = ""


@dataclass(frozen=True, slots=True)
class ReadOnlyPosture:
    """What the console's connection can actually do, re-read from the server.

    The audit trail of this page's central claim. Every field is measured over the same
    connection the queries run on, so a DSN that was pointed at the owner role by
    mistake is a refusal at the first request rather than a silent hole.

    Attributes:
        role: The role the console connects as.
        is_superuser: True disqualifies the connection outright — PostgreSQL skips row
            security entirely for a superuser.
        bypasses_rls: True disqualifies it for the same reason.
        writable_tables: Tables in the search schema this role holds INSERT, UPDATE or
            DELETE on. **Must be empty.** This is the read-only boundary; the
            ``default_transaction_read_only`` setting below is a guard rail the role can
            turn off itself.
        default_read_only: Whether ``default_transaction_read_only`` is on for this
            role. Reported for the operator, never relied on.
        statement_timeout: The role's ``statement_timeout``, as PostgreSQL reports it.
        escalations: Superuser/BYPASSRLS roles this role could ``SET ROLE`` into.
    """

    role: str
    is_superuser: bool
    bypasses_rls: bool
    writable_tables: tuple[str, ...] = ()
    default_read_only: bool = False
    statement_timeout: str = ""
    escalations: tuple[str, ...] = field(default=())

    @property
    def is_safe(self) -> bool:
        """Whether this connection may serve the console at all."""
        return not (
            self.is_superuser
            or self.bypasses_rls
            or self.writable_tables
            or self.escalations
        )

    def refusal(self) -> str:
        """Return the sentence naming why this connection was refused.

        Returns:
            One sentence, or the empty string when :attr:`is_safe`.
        """
        if self.is_safe:
            return ""
        faults: list[str] = []
        if self.is_superuser:
            faults.append("it is a SUPERUSER, so PostgreSQL skips row security for it")
        if self.bypasses_rls:
            faults.append("it holds BYPASSRLS, so every tenant_isolation policy is inert")
        if self.writable_tables:
            shown = ", ".join(self.writable_tables[:5])
            extra = len(self.writable_tables) - 5
            more = "" if extra <= 0 else f" (+{extra} more)"
            faults.append(f"it holds write privileges on {shown}{more}")
        if self.escalations:
            faults.append(
                "it is a member of "
                + ", ".join(self.escalations)
                + ", which it could SET ROLE into"
            )
        return (
            f"The database console will not run: its connection role {self.role!r} is not "
            f"read-only — " + "; ".join(faults) + ". Point AEGIS_DB_CONSOLE_DSN at the "
            "aegis_readonly role provisioned by scripts/sql/aegis-readonly-role.sql."
        )
