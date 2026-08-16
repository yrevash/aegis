"""The host wiring for the owner/serving split, and the boot check that guards it.

Why this file exists. ``aegis.governance.rls`` can only report that the serving role
bypasses Row-Level Security if the *host* asks it, on the *serving* engine, on a path
that nothing swallows. All three have failed before in this codebase — an earlier
diagnostic here lived inside a broad ``except`` and could never fire — so each is pinned
here rather than assumed:

- the two engines are distinct and DDL runs on the owner one (``get_admin_engine``);
- :func:`app.data.session.verify_rls_enforcement` reports a bypassing role, and **raises
  outside dev** so the API does not start with its isolation control silently off;
- the lifespan actually calls it, and the unreachable-database branch cannot swallow the
  verdict (an absent database is still a clean, documented start).

The behaviour of the policies themselves is pinned in ``aegis/tests/governance``; what
is host wiring is pinned here.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from aegis.governance.rls import RlsBypassError, RlsEnforcement
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.asyncio


def _fake_engine(dialect: str = "postgresql") -> SimpleNamespace:
    """An object with the only attribute the check reads before connecting."""
    return SimpleNamespace(dialect=SimpleNamespace(name=dialect))


def _superuser() -> RlsEnforcement:
    return RlsEnforcement(dialect="postgresql", role="postgres", is_superuser=True)


# ── the two engines ──


async def test_the_admin_engine_falls_back_to_the_serving_dsn_when_unsplit(monkeypatch):
    """A single-DSN developer install keeps working — DDL just shares the connection."""
    import app.data.session as session
    from app.config import get_settings

    settings = get_settings()
    unsplit = "postgresql+asyncpg://solo:pw@localhost:5432/taif"
    monkeypatch.setattr(settings, "postgres_admin_dsn", "", raising=False)
    monkeypatch.setattr(settings, "postgres_dsn", unsplit, raising=False)
    monkeypatch.setattr(session, "_engine", None, raising=False)
    monkeypatch.setattr(session, "_admin_engine", None, raising=False)

    assert settings.admin_dsn == settings.postgres_dsn
    admin = session.get_admin_engine()
    try:
        # Only the URL is asserted: building an engine opens no connection, so this
        # pins the resolution rule without needing the named database to exist.
        # ``render_as_string`` rather than ``str``, which masks the password as "***".
        assert admin.url.render_as_string(hide_password=False) == unsplit
    finally:
        await admin.dispose()
        session._admin_engine = None
        session._engine = None


async def test_a_non_postgres_serving_dsn_overrides_a_leftover_admin_dsn(monkeypatch):
    """A non-Postgres serving DSN takes DDL with it, whatever POSTGRES_ADMIN_DSN says.

    Honouring a stale POSTGRES_ADMIN_DSN here would create the tables in Postgres while
    the process served reads from the other database entirely: a split brain that stays
    invisible until a query comes back empty.

    ``app.config.Settings.admin_dsn`` keys this off the scheme — anything that is not
    ``postgres``/``postgresql`` wins — so any non-Postgres URL exercises the branch. The
    DSN is never connected to; only the resolution rule is under test.
    """
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "postgres_dsn", "mysql+aiomysql://h/taif_other")
    monkeypatch.setattr(
        settings, "postgres_admin_dsn", "postgresql://postgres:pw@localhost:5432/taif"
    )
    assert settings.admin_dsn == "mysql+aiomysql://h/taif_other"


async def test_a_configured_admin_dsn_builds_a_separate_engine(monkeypatch):
    """The split is real: two engines, two pools, two roles."""
    import app.data.session as session
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(
        settings, "postgres_dsn", "postgresql://aegis_app:pw@localhost:5432/taif"
    )
    monkeypatch.setattr(
        settings, "postgres_admin_dsn", "postgresql://postgres:pw@localhost:5432/taif"
    )
    monkeypatch.setattr(session, "_engine", None, raising=False)
    monkeypatch.setattr(session, "_admin_engine", None, raising=False)

    serving, admin = session.get_engine(), session.get_admin_engine()
    try:
        assert serving is not admin
        assert serving.url.username == "aegis_app"
        assert admin.url.username == "postgres"
        # The one thing the split exists to guarantee: DDL never runs as the role that
        # serves requests.
        assert session.serving_role_name() == "aegis_app"
    finally:
        await serving.dispose()
        await admin.dispose()
        session._engine = None
        session._admin_engine = None


async def test_no_serving_role_is_reported_when_both_dsns_name_one_role(monkeypatch):
    """Same role for DDL and serving is 'unsplit', however many DSNs are set."""
    from app.config import get_settings
    from app.data.session import serving_role_name

    settings = get_settings()
    monkeypatch.setattr(
        settings, "postgres_dsn", "postgresql://postgres:pw@localhost:5432/taif"
    )
    monkeypatch.setattr(
        settings, "postgres_admin_dsn", "postgresql://postgres:pw@localhost:5432/taif"
    )
    assert serving_role_name() is None


async def test_configure_engine_binds_both_engines():
    """A caller that binds one engine must not have DDL land in another database.

    No connection is opened — ``configure_engine`` only stores the engine and rebuilds the
    sessionmaker — so an unreachable DSN is the right choice here: it proves the binding is
    what is under test and not the database behind it.
    """
    import app.data.session as session

    engine = create_async_engine("postgresql+asyncpg://bound:pw@localhost:5432/bound_db")
    try:
        session.configure_engine(engine)
        assert session.get_engine() is engine
        assert session.get_admin_engine() is engine
    finally:
        session._engine = None
        session._admin_engine = None
        session._sessionmaker = None
        await engine.dispose()


# ── the boot check ──


async def test_the_check_is_a_noop_on_a_non_postgres_serving_engine(monkeypatch):
    """The guard keys off the dialect name, so any non-Postgres dialect exercises it."""
    import app.data.session as session

    monkeypatch.setattr(session, "get_engine", lambda: _fake_engine("mysql"))
    assert await session.verify_rls_enforcement() is None


async def test_a_bypassing_serving_role_is_reported_at_error_in_dev(monkeypatch, caplog):
    """Dev keeps working and keeps complaining — the check must still fire."""
    import app.data.session as session
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "app_env", "dev")
    monkeypatch.setattr(session, "get_engine", lambda: _fake_engine())

    async def _audit(_engine) -> RlsEnforcement:
        return _superuser()

    monkeypatch.setattr(session, "audit_rls_enforcement", _audit)

    with caplog.at_level(logging.ERROR, logger="aegis.governance.rls"):
        enforcement = await session.verify_rls_enforcement()

    assert enforcement is not None and enforcement.bypassed
    assert "row-level security is INERT".lower() in caplog.text.lower()
    # It names the policy it renders inert, not just the connection.
    assert "tenant_isolation" in caplog.text


async def test_a_bypassing_serving_role_refuses_to_boot_outside_dev(monkeypatch):
    """SECURITY: a deployment must not serve tenants with RLS enforced against nobody."""
    import app.data.session as session
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "app_env", "staging")
    monkeypatch.setattr(session, "get_engine", lambda: _fake_engine())

    async def _audit(_engine) -> RlsEnforcement:
        return _superuser()

    monkeypatch.setattr(session, "audit_rls_enforcement", _audit)

    with pytest.raises(RlsBypassError):
        await session.verify_rls_enforcement()


async def test_an_unreachable_database_leaves_the_verdict_unverified(monkeypatch, caplog):
    """Narrow by design: this branch may swallow a connection error, never a verdict."""
    import app.data.session as session

    monkeypatch.setattr(session, "get_engine", lambda: _fake_engine())

    async def _audit(_engine) -> RlsEnforcement:
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(session, "audit_rls_enforcement", _audit)

    with caplog.at_level(logging.WARNING, logger="app.data.session"):
        assert await session.verify_rls_enforcement(fatal=True) is None
    assert "UNVERIFIED" in caplog.text


async def test_the_lifespan_runs_the_check_and_does_not_swallow_it(monkeypatch):
    """The wiring: an exempt serving role stops startup, it is not logged and forgotten.

    The lifespan's blanket ``except Exception`` around DB bootstrap is right for an
    unreachable database and wrong for this: the API would come up with every tenant
    policy bypassed. So the check sits outside that handler, and this test would fail if
    anyone moved it back inside.
    """
    import app.data.session as session
    from app.config import get_settings
    from app.main import app, lifespan

    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "prod")
    monkeypatch.setattr(settings, "db_bootstrap", False, raising=False)
    monkeypatch.setattr(settings, "stores", "on")
    monkeypatch.setattr(session, "get_engine", lambda: _fake_engine())

    async def _audit(_engine) -> RlsEnforcement:
        return _superuser()

    monkeypatch.setattr(session, "audit_rls_enforcement", _audit)

    with pytest.raises(RlsBypassError):
        async with lifespan(app):
            pass


async def test_the_rls_check_cli_separates_bypassed_from_unverified(monkeypatch):
    """preflight's readiness row: 0 enforced, 1 bypassed, 2 unknown — never conflated."""
    import app.data.rls_check as rls_check

    class _Disposable(SimpleNamespace):
        async def dispose(self) -> None:
            return None

    monkeypatch.setattr(
        rls_check,
        "get_engine",
        lambda: _Disposable(dialect=SimpleNamespace(name="postgresql")),
    )

    async def _bypassed(_engine) -> RlsEnforcement:
        return _superuser()

    monkeypatch.setattr(rls_check, "audit_rls_enforcement", _bypassed)
    monkeypatch.setattr(rls_check, "serving_role_name", lambda: None)
    assert await rls_check._check() == 1

    async def _enforced(_engine) -> RlsEnforcement:
        return RlsEnforcement(dialect="postgresql", role="aegis_app")

    monkeypatch.setattr(rls_check, "audit_rls_enforcement", _enforced)
    monkeypatch.setattr(rls_check, "serving_role_name", lambda: "aegis_app")
    assert await rls_check._check() == 0

    monkeypatch.setattr(
        rls_check, "get_engine", lambda: _Disposable(dialect=SimpleNamespace(name="mysql"))
    )
    assert await rls_check._check() == 2
