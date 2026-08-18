"""Bootstrap reconciles additive column drift — and refuses to boot when it cannot.

Why this exists. ``create_all`` is ``CREATE TABLE IF NOT EXISTS``: it never alters a
table that already exists, and this project has no Alembic. So when ``audio_seconds``
and ``images`` were added to :class:`aegis.governance.models.UsageLedger`, every
database that predated them kept the old shape and every ledger INSERT raised
``UndefinedColumn`` — swallowed, because ``aegis.gateway.llm._record_usage`` records
usage best-effort so a logging problem cannot break a live model call. The row was
lost, per-tenant spend attribution stopped, and the USD caps computed by summing those
rows stopped binding, with nothing anywhere saying so.

``bootstrap`` is therefore the schema owner and runs
:func:`aegis.governance.schema.reconcile_additive_columns` right after ``create_all``.
The reconciliation's own behaviour is unit-tested in
``aegis/tests/governance/test_schema_reconcile.py``; what is pinned here is the host
wiring — that bootstrap calls it, and that the one failure mode that must never be
degraded away is not.
"""

from __future__ import annotations

import pytest
from aegis.governance.schema import SchemaDriftError
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.asyncio


async def test_bootstrap_reconciles_additive_drift_after_create_all(
    monkeypatch, postgres_database
):
    """The reconciliation runs, on the same connection, with both metadatas.

    Driven over the session's scratch **PostgreSQL** database via its owner DSN, which is
    the role that may run DDL. The dialect is part of the assertion rather than incidental:
    ``reconcile_additive_columns`` inspects and ``ALTER``s real Postgres catalogs, and the
    drift this whole file exists to catch (a live cluster missing ``usage_ledger``'s newer
    columns) cannot occur on a database recreated from scratch on every connection — which
    is exactly what the temp-file SQLite engine this test used to build gave it.

    ``bootstrap`` is idempotent — ``create_all`` is ``CREATE TABLE IF NOT EXISTS`` and the
    grant/RLS steps re-apply cleanly — so re-running it against the already-bootstrapped
    scratch database leaves it in the same state the other tests expect.
    """
    seen: dict = {}

    async def _spy(conn, metadatas):
        seen["metadatas"] = list(metadatas)
        seen["dialect"] = conn.dialect.name
        return []

    monkeypatch.setattr("app.data.session.reconcile_additive_columns", _spy)

    from app.data.session import bootstrap

    engine = create_async_engine(postgres_database.scratch.owner_dsn)
    try:
        await bootstrap(engine)
    finally:
        await engine.dispose()

    assert seen["dialect"] == "postgresql"
    tables = {name for md in seen["metadatas"] for name in md.tables}
    # The governance metadata is included — the ledger is the table that matters.
    assert "usage_ledger" in tables


async def test_irreconcilable_drift_stops_the_api_from_starting(monkeypatch):
    """SECURITY: an unwritable ledger must not be degraded into a startup warning.

    The lifespan's blanket ``except Exception`` is right for an *unreachable*
    database — the platform genuinely runs without one. It is wrong for reachable
    drift: the API would come up serving paid model calls whose spend is neither
    capped nor recorded. So ``SchemaDriftError`` is re-raised ahead of that handler.
    """
    from app.config import get_settings
    from app.main import app, lifespan

    monkeypatch.setattr(get_settings(), "db_bootstrap", True, raising=False)

    async def _boom() -> None:
        raise SchemaDriftError("usage_ledger.audio_seconds cannot be added")

    monkeypatch.setattr("app.data.session.bootstrap", _boom)

    with pytest.raises(SchemaDriftError):
        async with lifespan(app):
            pass


async def test_an_unreachable_database_still_degrades_cleanly(monkeypatch):
    """The offline/lite path is unchanged: no database is still a clean start."""
    from app.config import get_settings
    from app.main import app, lifespan

    monkeypatch.setattr(get_settings(), "db_bootstrap", True, raising=False)

    async def _unreachable() -> None:
        raise OSError("connection refused")

    monkeypatch.setattr("app.data.session.bootstrap", _unreachable)

    async with lifespan(app):
        pass  # no exception: an absent database never blocks startup


#: The pre-tenancy ``chunks`` shape, verbatim: a ``doc_id`` string that referenced
#: nothing and no owner column at all.
_LEGACY_CHUNKS = (
    "CREATE TABLE chunks ("
    " id serial PRIMARY KEY,"
    " doc_id varchar(255) NOT NULL,"
    " persona varchar(128),"
    " content varchar NOT NULL,"
    " embedding jsonb NOT NULL,"
    " meta jsonb NOT NULL)"
)


async def _install_legacy_chunks(engine, rows: int = 0) -> None:
    """Replace the live ``chunks`` table with the pre-tenancy one, optionally populated."""
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS chunks"))
        await conn.execute(text(_LEGACY_CHUNKS))
        for index in range(rows):
            await conn.execute(
                text(
                    "INSERT INTO chunks (doc_id, content, embedding, meta) "
                    "VALUES (:doc, 'legacy passage', '[]'::jsonb, '{}'::jsonb)"
                ),
                {"doc": f"doc-{index}"},
            )


async def _chunk_columns(engine) -> set[str]:
    """Return the live ``chunks`` column names."""
    from sqlalchemy import text

    async with engine.connect() as conn:
        return set(
            (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = current_schema() AND table_name = 'chunks'"
                    )
                )
            ).scalars().all()
        )


async def test_bootstrap_rebuilds_an_empty_pre_tenancy_chunks_table(postgres_database):
    """The one table no additive ALTER can fix is recreated instead of blocking the boot.

    ``chunks`` gained two ``NOT NULL`` foreign keys, and
    :func:`aegis.governance.schema.reconcile_additive_columns` correctly refuses to add
    those to an existing table — there is no value to back-fill. Left alone, that means
    every database bootstrapped before this change refuses to start. An **empty** legacy
    table has nothing to back-fill, so recreating it is the whole migration, and that is
    what this asserts end to end: the legacy shape goes in, ``bootstrap`` runs, and the
    current shape comes out.
    """
    from app.data.session import bootstrap

    engine = create_async_engine(postgres_database.scratch.owner_dsn)
    try:
        await _install_legacy_chunks(engine)
        assert "doc_id" in await _chunk_columns(engine)

        await bootstrap(engine)

        columns = await _chunk_columns(engine)
        assert "doc_id" not in columns, "the legacy column survived the rebuild"
        assert {"tenant_id", "document_id"} <= columns
    finally:
        await bootstrap(engine)  # leave the session's schema as the other tests expect
        await engine.dispose()


async def test_bootstrap_refuses_to_drop_a_populated_pre_tenancy_chunks_table(
    postgres_database,
):
    """A table with rows in it is a migration, not a rebuild — and it is not this code's.

    The rebuild above is only safe because "empty" is checked rather than assumed. This
    is the other half: with a row present the boot fails loudly and the row is still
    there afterwards, so the recreate can never quietly become a ``DELETE`` of a tenant's
    corpus.
    """
    from sqlalchemy import text

    from app.data.session import bootstrap

    engine = create_async_engine(postgres_database.scratch.owner_dsn)
    try:
        await _install_legacy_chunks(engine, rows=2)

        with pytest.raises(SchemaDriftError, match="pre-tenancy"):
            await bootstrap(engine)

        async with engine.connect() as conn:
            survivors = (
                await conn.execute(text("SELECT count(*) FROM chunks"))
            ).scalar_one()
        assert survivors == 2, "the refusal deleted rows it refused to migrate"
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS chunks"))
        await bootstrap(engine)
        await engine.dispose()
