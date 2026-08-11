"""RLS helpers are Postgres-only and cleanly no-op on SQLite.

The unit suite runs with no Postgres, so ``set_tenant_scope`` and ``bootstrap_rls``
must be safe no-ops on the aiosqlite engine (their real effect is exercised only on
Postgres). This pins that contract so the offline path never errors.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aegis.governance import bootstrap_rls, set_tenant_scope


async def test_set_tenant_scope_is_a_noop_on_sqlite(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rls.db'}")
    try:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            # Neither a scoped nor an unscoped call raises on SQLite.
            await set_tenant_scope(session, 7)
            await set_tenant_scope(session, None)
    finally:
        await engine.dispose()


async def test_bootstrap_rls_is_a_noop_on_sqlite(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rls2.db'}")
    try:
        # No policy DDL is emitted on a non-Postgres dialect; it simply returns.
        await bootstrap_rls(engine)
    finally:
        await engine.dispose()
