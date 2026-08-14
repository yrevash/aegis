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
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.schema import CreateIndex

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from sqlalchemy import Column, MetaData

__all__ = ["SchemaDriftError", "plan_additive_columns", "reconcile_additive_columns"]

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
