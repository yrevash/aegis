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


async def test_bootstrap_reconciles_additive_drift_after_create_all(monkeypatch, tmp_path):
    """The reconciliation runs, on the same connection, with both metadatas."""
    seen: dict = {}

    async def _spy(conn, metadatas):
        seen["metadatas"] = list(metadatas)
        seen["dialect"] = conn.dialect.name
        return []

    monkeypatch.setattr("app.data.session.reconcile_additive_columns", _spy)

    from app.data.session import bootstrap

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'boot.db'}")
    try:
        await bootstrap(engine)
    finally:
        await engine.dispose()

    assert seen["dialect"] == "sqlite"
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
