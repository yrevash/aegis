"""Additive schema reconciliation — the missing migration step for a no-Alembic host.

``create_all`` is ``CREATE TABLE IF NOT EXISTS``: it materialises a *new* table and
then never touches it again. So the moment a column is added to a model
(:class:`aegis.governance.models.UsageLedger`'s ``audio_seconds`` / ``images`` were
exactly this), every database that already carried the old table kept the old shape,
and every INSERT naming the new column raised ``UndefinedColumn``.

That is not a cosmetic drift for the ledger. ``aegis.gateway.llm._record_usage``
records usage **best-effort** — a failure there is swallowed so a logging problem can
never break a live call — so the failing INSERT was silent, the row was lost, and with
it the per-tenant spend attribution *and* the USD caps that are computed by summing
those rows. The caps stop binding, and nothing says so.

This module is the reconciliation that closes that window, in keeping with this
project's deliberate no-Alembic choice:

* **Additive only.** It adds columns the declarative metadata has and the database
  does not. It never drops, renames, retypes or reorders anything, so it cannot
  destroy data and is safe to run on every boot.
* **Idempotent.** The plan is computed from ``information_schema``; a second run
  finds nothing to do. The emitted DDL also carries ``IF NOT EXISTS``, so two
  processes racing at startup cannot collide.
* **Postgres-only.** SQLite (the test database) returns immediately and is untouched:
  the test suite recreates its schema from scratch on every run, so it has no drift
  to reconcile, and SQLite's ``ALTER TABLE`` has restrictions this deliberately does
  not try to work around.
* **Loud.** Every added column is logged at INFO, and a column that *cannot* be added
  safely raises :class:`SchemaDriftError` rather than being skipped. Refusing to boot
  is the correct outcome for the ledger: the alternative is a running system whose
  spend caps silently do not bind.

Constraints, indexes on pre-existing columns, and type changes are explicitly out of
scope (a timestamp retype has its own dedicated step in the host's bootstrap); an
index declared *on a newly added column* is created alongside it, so an added column
is never left half-installed.

**Enum members are the second thing ``create_all`` cannot do**, and they failed the
same way. ``CREATE TYPE`` is emitted once, on the boot that first creates the type, and
never again — so adding a member to a Python enum backing a native PostgreSQL type
(``RunStatus.REJECTED`` was exactly this) leaves every existing database with the old
label set, and the first write of the new member fails with ``invalid input value for
enum``. :func:`reconcile_enum_values` is the same idea applied to types instead of
columns: additive, idempotent, positioned to match the declaration, and loud.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import Enum as SAEnum
from sqlalchemy import text
from sqlalchemy.schema import CreateIndex

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from sqlalchemy import Column, MetaData

__all__ = [
    "SchemaDriftError",
    "declared_enum_labels",
    "plan_additive_columns",
    "plan_enum_values",
    "reconcile_additive_columns",
    "reconcile_enum_values",
]

logger = logging.getLogger(__name__)


class SchemaDriftError(RuntimeError):
    """Raised when a live table is missing a column that cannot be added safely.

    "Safely" means: addable by a plain ``ALTER TABLE ... ADD COLUMN`` with no
    back-fill decision to make — i.e. the column is nullable, or it carries a
    ``server_default`` the database can apply to every existing row. A ``NOT NULL``
    column with no server default has no correct value for the rows already present,
    so only a human can decide what it should be.

    This is raised, never logged-and-skipped, because the drift it reports means
    writes to that table are failing right now.
    """


async def _existing_columns(conn: Any) -> set[tuple[str, str]]:  # noqa: ANN401
    """Return every ``(table, column)`` present in the connection's current schema.

    Args:
        conn: An open async connection (PostgreSQL).

    Returns:
        The set of ``(table_name, column_name)`` pairs that physically exist.
    """
    result = await conn.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema()"
        )
    )
    return {(row[0], row[1]) for row in result}


def plan_additive_columns(
    existing: set[tuple[str, str]], metadatas: Iterable[MetaData]
) -> tuple[list[Column[Any]], list[Column[Any]]]:
    """Split the model↔database column drift into "addable" and "needs a human".

    Pure and database-free, so the decision this module makes is testable without a
    live PostgreSQL.

    A table that does not exist at all is skipped: ``create_all`` owns brand-new
    tables and will have created it in full, with no drift to reconcile.

    Args:
        existing: ``(table, column)`` pairs that physically exist, as returned by
            :func:`_existing_columns`.
        metadatas: The declarative metadata objects describing the wanted schema.

    Returns:
        ``(addable, unsafe)`` — columns that a plain ``ADD COLUMN`` can install, and
        columns whose absence needs an explicit human migration.
    """
    live_tables = {table for table, _ in existing}
    addable: list[Column[Any]] = []
    unsafe: list[Column[Any]] = []
    for metadata in metadatas:
        for table in metadata.sorted_tables:
            if table.name not in live_tables:
                continue
            for column in table.columns:
                if (table.name, column.name) in existing:
                    continue
                # A missing primary key is a different table, not a missing column.
                if column.primary_key or not (
                    column.nullable or column.server_default is not None
                ):
                    unsafe.append(column)
                else:
                    addable.append(column)
    return addable, unsafe


def _column_ddl(column: Column[Any], dialect: Any) -> str:  # noqa: ANN401
    """Render one column's ``ADD COLUMN`` body using SQLAlchemy's own compiler.

    Compiling from the declarative metadata (rather than hand-writing SQL) keeps the
    added column's type, nullability and server default identical to what
    ``create_all`` would have produced on a fresh database.

    Args:
        column: The declarative column to render.
        dialect: The connection's dialect.

    Returns:
        The DDL fragment, e.g. ``audio_seconds FLOAT NOT NULL DEFAULT '0'``.
    """
    from sqlalchemy.schema import CreateColumn  # noqa: PLC0415 - local, DDL-only import

    return str(CreateColumn(column).compile(dialect=dialect)).strip()


def _indexes_for(columns: Sequence[Column[Any]]) -> list[Any]:
    """Return the declared indexes that cover only newly added columns.

    An index whose columns are all being added in this same pass can be created now;
    one that also spans pre-existing columns is left alone, because index drift on
    columns that already exist is out of this module's scope.

    Args:
        columns: The columns added in this pass.

    Returns:
        The index objects to create, de-duplicated and stably ordered.
    """
    added = {(column.table.name, column.name) for column in columns}
    seen: set[str] = set()
    wanted: list[Any] = []
    for column in columns:
        for index in column.table.indexes:
            if index.name in seen:
                continue
            if all((column.table.name, c.name) in added for c in index.columns):
                seen.add(str(index.name))
                wanted.append(index)
    return wanted


async def reconcile_additive_columns(
    conn: Any,  # noqa: ANN401 - AsyncConnection, kept loose (no import-time asyncpg dep)
    metadatas: Iterable[MetaData],
) -> list[str]:
    """Add every column the models declare and the live PostgreSQL tables lack.

    Call once at bootstrap, on the same connection as ``create_all``, alongside
    :func:`aegis.governance.rls.bootstrap_rls`. See the module docstring for why this
    exists and the guarantees it holds to (additive, idempotent, Postgres-only, loud).

    Args:
        conn: An open (transactional) async connection.
        metadatas: The declarative metadata objects describing the wanted schema.

    Returns:
        The ``"table.column"`` names added by this call — empty when the database was
        already in step, and always empty on a non-PostgreSQL dialect.

    Raises:
        SchemaDriftError: If a live table is missing a column that cannot be added by
            a plain ``ADD COLUMN``. Refusing to boot is deliberate: the tables this
            reconciles include the usage ledger, and a ledger that cannot be written
            means the USD budget caps computed from it have stopped binding.
    """
    if conn.dialect.name != "postgresql":
        return []
    metadatas = list(metadatas)
    existing = await _existing_columns(conn)
    addable, unsafe = plan_additive_columns(existing, metadatas)
    if unsafe:
        detail = ", ".join(
            f"{column.table.name}.{column.name}" for column in unsafe
        )
        msg = (
            f"Schema drift that cannot be reconciled additively: {detail}. Each is "
            "declared NOT NULL with no server default (or is a primary key), so "
            "there is no correct value for the rows already in the table — only an "
            "explicit migration can decide one. Refusing to continue: writes naming "
            "these columns are failing right now."
        )
        # Logged at CRITICAL as well as raised: a host that wraps its bootstrap in a
        # broad "the database is optional" handler would otherwise reduce this to a
        # traceback nobody reads, and for the usage ledger the consequence of missing
        # it is uncapped, unattributed spend.
        logger.critical("%s", msg)
        raise SchemaDriftError(msg)
    if not addable:
        return []

    added: list[str] = []
    for column in addable:
        ddl = _column_ddl(column, conn.dialect)
        try:
            # The identifiers come from our own declarative metadata, never user input.
            await conn.execute(
                text(f'ALTER TABLE "{column.table.name}" ADD COLUMN IF NOT EXISTS {ddl}')
            )
        except Exception:
            # Never swallowed. A refused ALTER (no privilege, a lock timeout) leaves
            # the table unwritable for any statement naming the column, and for
            # ``usage_ledger`` that means spend recording — and therefore the USD
            # caps read back from it — silently stop working.
            logger.critical(
                "schema reconcile FAILED for %s.%s (%s); writes naming this column "
                "will keep failing.",
                column.table.name,
                column.name,
                ddl,
            )
            raise
        added.append(f"{column.table.name}.{column.name}")
        logger.info(
            "schema reconcile: added missing column %s.%s (%s)",
            column.table.name,
            column.name,
            ddl,
        )
    for index in _indexes_for(addable):
        await conn.execute(CreateIndex(index, if_not_exists=True))
        logger.info("schema reconcile: created index %s", index.name)
    return added


# ─────────────────────────────────────────────────────────────────────────────
# Native enum types — the second thing ``create_all`` will not do for you
# ─────────────────────────────────────────────────────────────────────────────


def declared_enum_labels(metadatas: Iterable[MetaData]) -> dict[str, tuple[str, ...]]:
    """Return every **named native** enum type the models declare, and its labels.

    The labels are read from SQLAlchemy's own :class:`~sqlalchemy.Enum`, in declaration
    order, so they are byte-identical to what ``CREATE TYPE`` would have emitted on a
    fresh database — including the choice of storing a Python enum's *names*
    (``'COMPLETED'``) rather than its values (``'completed'``), which is SQLAlchemy's
    default and what the live ``run_status`` type actually holds.

    Unnamed enums are skipped: without a type name there is nothing to ``ALTER``.
    Non-native enums are skipped too — they are rendered as ``VARCHAR`` plus a CHECK,
    so they have no type to reconcile (and this is why SQLite needs no handling here).

    Args:
        metadatas: The declarative metadata objects describing the wanted schema.

    Returns:
        ``{type_name: (label, …)}`` in declaration order. A type used by more than one
        column appears once; if two columns disagree about its labels the union is
        refused rather than guessed — see :func:`plan_enum_values`.

    Raises:
        SchemaDriftError: If one type name is declared with two different label lists.
    """
    declared: dict[str, tuple[str, ...]] = {}
    for metadata in metadatas:
        for table in metadata.sorted_tables:
            for column in table.columns:
                type_ = column.type
                if not isinstance(type_, SAEnum):
                    continue
                if not type_.name or not type_.native_enum:
                    continue
                labels = tuple(type_.enums)
                previous = declared.get(type_.name)
                if previous is not None and previous != labels:
                    msg = (
                        f"The enum type {type_.name!r} is declared with two different "
                        f"label lists: {previous} on one column and {labels} on "
                        f"{table.name}.{column.name}. One type cannot have two "
                        "definitions; reconciling either one would silently contradict "
                        "the other, so nothing is done."
                    )
                    logger.critical("%s", msg)
                    raise SchemaDriftError(msg)
                declared[type_.name] = labels
    return declared


async def _existing_enum_labels(conn: Any) -> dict[str, tuple[str, ...]]:  # noqa: ANN401
    """Return every enum type in the current schema and its labels, in sort order.

    Args:
        conn: An open async connection (PostgreSQL).

    Returns:
        ``{type_name: (label, …)}`` as the database currently holds them.
    """
    result = await conn.execute(
        text(
            "SELECT t.typname, e.enumlabel "
            "FROM pg_type t "
            "JOIN pg_enum e ON e.enumtypid = t.oid "
            "JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE n.nspname = current_schema() "
            "ORDER BY t.typname, e.enumsortorder"
        )
    )
    live: dict[str, list[str]] = {}
    for type_name, label in result:
        live.setdefault(type_name, []).append(label)
    return {name: tuple(labels) for name, labels in live.items()}


def plan_enum_values(
    existing: dict[str, tuple[str, ...]], declared: dict[str, tuple[str, ...]]
) -> tuple[list[tuple[str, str, str | None]], list[tuple[str, str]]]:
    """Split enum drift into "labels to add" and "labels only the database has".

    Pure and database-free, so the decision is testable without a live PostgreSQL —
    the same property :func:`plan_additive_columns` is written for.

    A type the database does not have at all is skipped: ``create_all`` emits
    ``CREATE TYPE`` for a type it is about to use, in full, so there is no drift.

    **Position is part of the plan.** ``ALTER TYPE … ADD VALUE`` appends by default, so
    a member declared in the middle of a Python enum would sort last on an upgraded
    database and in the middle on a fresh one — two databases built from one source
    disagreeing about ``ORDER BY status``. Each added label therefore carries the
    declared label it must come *before*, when a later declared label already exists.

    Args:
        existing: Live types and their labels, from :func:`_existing_enum_labels`.
        declared: Wanted types and their labels, from :func:`declared_enum_labels`.

    Returns:
        ``(additions, extraneous)``. ``additions`` is ``(type_name, label,
        before_label)`` in the order they must be applied, where ``before_label`` is
        ``None`` for an append. ``extraneous`` is ``(type_name, label)`` for labels the
        database holds and the models no longer declare — reported, never removed:
        ``ALTER TYPE … DROP VALUE`` does not exist in PostgreSQL, and rows may still
        carry the label.
    """
    additions: list[tuple[str, str, str | None]] = []
    extraneous: list[tuple[str, str]] = []
    for type_name, labels in declared.items():
        live = existing.get(type_name)
        if live is None:
            continue
        live_set = set(live)
        # Grows as we plan, so a run of consecutive new labels anchors each one on the
        # previous plan's target rather than all of them on the same later neighbour.
        placed = set(live_set)
        for index, label in enumerate(labels):
            if label in placed:
                continue
            before = next((nxt for nxt in labels[index + 1 :] if nxt in placed), None)
            additions.append((type_name, label, before))
            placed.add(label)
        extraneous.extend(
            (type_name, label) for label in live if label not in set(labels)
        )
    return additions, extraneous


async def reconcile_enum_values(
    conn: Any,  # noqa: ANN401 - AsyncConnection, kept loose (no import-time asyncpg dep)
    metadatas: Iterable[MetaData],
) -> list[str]:
    """Add every native-enum label the models declare and the live types lack.

    Call once at bootstrap, **before** ``create_all`` and on a connection in
    ``AUTOCOMMIT``. Both of those are requirements, not preferences:

    * *Before ``create_all``* because a table created on this same boot may already
      declare a column of the type, and because nothing in the bootstrap needs to
      *write* the new label — PostgreSQL forbids using a value in the transaction that
      added it (before 15 it forbids adding one inside a transaction block at all).
    * *AUTOCOMMIT* for the same reason, and it is what makes this work identically on
      PostgreSQL 9.3 through 18 rather than only on 12 and later.

    Additive only, and that is the whole safety argument: ``ADD VALUE IF NOT EXISTS``
    cannot invalidate a stored row, cannot rewrite a table, and takes no long lock. A
    label the database has and the models do not is **reported and left alone** —
    PostgreSQL has no ``DROP VALUE``, and rows may still carry it.

    Args:
        conn: An open async connection (PostgreSQL), ideally in AUTOCOMMIT.
        metadatas: The declarative metadata objects describing the wanted schema.

    Returns:
        The ``"type.label"`` names added by this call — empty when the database was
        already in step, and always empty on a non-PostgreSQL dialect (SQLite renders
        these enums as ``VARCHAR`` + CHECK and the unit suite rebuilds its schema every
        run, so it has no drift to reconcile).

    Raises:
        SchemaDriftError: If one enum type name is declared with two label lists.
    """
    if conn.dialect.name != "postgresql":
        return []
    declared = declared_enum_labels(metadatas)
    if not declared:
        return []
    existing = await _existing_enum_labels(conn)
    additions, extraneous = plan_enum_values(existing, declared)
    for type_name, label in extraneous:
        logger.warning(
            "Enum type %s holds the label %r, which the models no longer declare. "
            "Left in place: PostgreSQL cannot drop an enum label, and rows may still "
            "carry it. Reading code must keep handling it.",
            type_name,
            label,
        )
    added: list[str] = []
    for type_name, label, before in additions:
        # Every identifier here comes from our own declarative metadata, never from
        # user input. Quoted anyway, because a label is a string literal and a type
        # name an identifier, and mixing those up is how DDL builders go wrong.
        clause = f" BEFORE '{before}'" if before else ""
        statement = (
            f"ALTER TYPE \"{type_name}\" ADD VALUE IF NOT EXISTS '{label}'{clause}"
        )
        try:
            await conn.execute(text(statement))
        except Exception:
            # Never swallowed, and this one is worth a CRITICAL for the same reason the
            # column path is: until the label exists, every INSERT/UPDATE writing it
            # fails, and for ``run_status`` that is the run header — the row a console
            # lists runs from and the row analytics reconcile against.
            logger.critical(
                "enum reconcile FAILED for %s.%s (%s); writes of this value will keep "
                "failing with 'invalid input value for enum'.",
                type_name,
                label,
                statement,
            )
            raise
        added.append(f"{type_name}.{label}")
        logger.info(
            "enum reconcile: added missing label %s.%s (%s)", type_name, label, statement
        )
    return added
