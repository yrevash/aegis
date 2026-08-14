"""RLS helpers are Postgres-only and cleanly no-op on SQLite.

The unit suite runs with no Postgres, so ``set_tenant_scope`` and ``bootstrap_rls``
must be safe no-ops on the aiosqlite engine (their real effect is exercised only on
Postgres). This pins that contract so the offline path never errors.
"""

from __future__ import annotations

from types import SimpleNamespace

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


# ── the Postgres DDL itself (recorded against a fake postgresql engine) ──
#
# The unit suite has no Postgres, but the *statements* bootstrap_rls emits are the
# whole security control, so they are pinned here directly. Without FORCE the policy
# is inert for the table owner — which is the role this application connects as, since
# ``app.data.session.bootstrap`` creates the tables and then serves every request on
# the very same engine.


class _RecordingConn:
    """Captures the SQL text executed inside ``engine.begin()``."""

    def __init__(self, statements: list[str]) -> None:
        self._statements = statements

    async def execute(self, clause, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        self._statements.append(str(clause))
        return None


class _FakeBegin:
    def __init__(self, statements: list[str]) -> None:
        self._statements = statements

    async def __aenter__(self) -> _RecordingConn:
        return _RecordingConn(self._statements)

    async def __aexit__(self, *exc) -> bool:  # noqa: ANN002
        return False


class _FakePostgresEngine:
    """Just enough of an AsyncEngine for ``bootstrap_rls``: a dialect and ``begin()``."""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.dialect = SimpleNamespace(name="postgresql")

    def begin(self) -> _FakeBegin:
        return _FakeBegin(self.statements)


async def _bootstrap_statements() -> list[str]:
    engine = _FakePostgresEngine()
    await bootstrap_rls(engine)
    return engine.statements


async def test_bootstrap_forces_rls_on_every_tenant_scoped_table():
    """ENABLE alone leaves the owner exempt; FORCE is what makes the policy real."""
    statements = await _bootstrap_statements()
    for table in ("users", "usage_ledger", "approvals"):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in statements
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in statements


async def test_force_is_issued_after_enable_for_each_table():
    statements = await _bootstrap_statements()
    for table in ("users", "usage_ledger", "approvals"):
        enable = statements.index(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        force = statements.index(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        assert enable < force


async def test_policy_is_recreated_idempotently_with_a_safe_predicate():
    statements = await _bootstrap_statements()
    policies = [s for s in statements if s.startswith("CREATE POLICY tenant_isolation")]
    assert len(policies) == 3
    for policy in policies:
        # A bound numeric scope restricts rows to that tenant …
        assert "tenant_id = substring(current_setting('app.tenant_id', true)" in policy
        # … and the predicate can never raise on a non-numeric GUC (no bare ''::int).
        assert "NULLIF(current_setting('app.tenant_id', true), '')::int" not in policy
        assert "IS NULL" in policy
    assert sum(s.startswith("DROP POLICY IF EXISTS tenant_isolation") for s in statements) == 3
