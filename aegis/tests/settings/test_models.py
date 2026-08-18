"""The ``settings`` table's own guarantees — the ones the database has to make.

Three of them exist because the specification's sketch of this table would not actually
have enforced anything on the target cluster, and the difference is only visible against
a live PostgreSQL:

* ``UNIQUE (scope, tenant_id, user_id, key)`` does **not** constrain rows with NULLs on
  PostgreSQL 14 (``NULLS NOT DISTINCT`` is 15), so two platform rows for one key would
  both be legal — and the resolver would read whichever it happened to get.
* a ``user``-scoped row with a NULL tenant would be **world readable**, because NULL is
  what marks the platform baseline on this table.
* the widened read needs an explicit ``WITH CHECK``, or Postgres reuses the widened
  ``USING`` for writes and any tenant can forge a platform default.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from aegis.data import AegisBase
from aegis.governance.rls import (
    _PLATFORM_BASELINE_TABLES,
    _POLICY_NAME,
    _TENANT_COLUMN,
    _TENANT_SCOPED_TABLES,
)
from aegis.settings.models import SETTINGS_TABLE, Setting

from .._seed import ensure_tenants, ensure_users

_SRC = str(Path(__file__).resolve().parents[2] / "src")
_TENANT = 611
_USER = 6111


def test_the_settings_table_is_tenant_scoped_and_registered():
    assert _TENANT_COLUMN in Setting.__table__.c
    assert SETTINGS_TABLE in _TENANT_SCOPED_TABLES
    assert SETTINGS_TABLE in AegisBase.metadata.tables


def test_the_settings_table_is_declared_a_platform_baseline_table():
    """Its NULL-tenant row is a baseline every tenant reads, not a platform-private one.

    The distinction decides a policy predicate, and getting it wrong in either direction
    is a real defect: without the widening the resolver loses the platform layer, and
    with the widening applied to ``job_runs`` a tenant would see platform jobs.
    """
    assert SETTINGS_TABLE in _PLATFORM_BASELINE_TABLES
    assert "job_runs" not in _PLATFORM_BASELINE_TABLES


async def test_the_policy_carries_a_widened_read_and_an_unwidened_write(pg_owner_engine):
    """Read back from ``pg_policy``, because this is the pair that has to be exact."""
    async with pg_owner_engine.connect() as conn:
        using, check = (
            await conn.execute(
                text(
                    "SELECT pg_get_expr(p.polqual, p.polrelid), "
                    "       pg_get_expr(p.polwithcheck, p.polrelid) "
                    "FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid "
                    "WHERE c.relname = :table AND p.polname = :policy"
                ),
                {"table": SETTINGS_TABLE, "policy": _POLICY_NAME},
            )
        ).one()
    assert using is not None and check is not None, (
        "the settings policy has no explicit WITH CHECK, so Postgres reuses the widened "
        "USING clause for writes and any tenant can insert a platform-scoped row"
    )
    assert "tenant_id IS NULL" in using
    assert "IS NULL" in check
    assert "tenant_id IS NULL" not in check.replace(
        "substring(current_setting('app.tenant_id'::text, true), '^[0-9]+$'::text) IS NULL", ""
    )


async def test_two_platform_rows_for_one_key_are_impossible(pg_owner_engine):
    """The PostgreSQL 14 NULL-distinct trap, closed by a partial unique index."""
    async with pg_owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO settings (scope, tenant_id, user_id, key, value) "
                "VALUES ('platform', NULL, NULL, 'agent.mode', '\"fast\"'::jsonb)"
            )
        )
    with pytest.raises(IntegrityError, match="uq_settings_platform_key"):
        async with pg_owner_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO settings (scope, tenant_id, user_id, key, value) "
                    "VALUES ('platform', NULL, NULL, 'agent.mode', '\"team\"'::jsonb)"
                )
            )


async def test_two_tenant_rows_for_one_key_are_impossible(pg_owner_engine, pg_sessionmaker):
    await ensure_tenants(pg_sessionmaker, _TENANT)
    async with pg_owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO settings (scope, tenant_id, user_id, key, value) "
                "VALUES ('tenant', :tenant, NULL, 'agent.mode', '\"fast\"'::jsonb)"
            ),
            {"tenant": _TENANT},
        )
    with pytest.raises(IntegrityError, match="uq_settings_tenant_key"):
        async with pg_owner_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO settings (scope, tenant_id, user_id, key, value) "
                    "VALUES ('tenant', :tenant, NULL, 'agent.mode', '\"team\"'::jsonb)"
                ),
                {"tenant": _TENANT},
            )


async def test_a_user_row_without_a_tenant_is_refused_by_the_database(
    pg_owner_engine, pg_sessionmaker
):
    """Not merely by the resolver: such a row would be readable by every tenant."""
    await ensure_tenants(pg_sessionmaker, _TENANT)
    await ensure_users(pg_sessionmaker, **{f"u{_USER}": _TENANT})
    with pytest.raises(IntegrityError, match="ck_settings_platform_row_has_no_tenant"):
        async with pg_owner_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO settings (scope, tenant_id, user_id, key, value) "
                    "VALUES ('user', NULL, :user, 'agent.mode', '\"fast\"'::jsonb)"
                ),
                {"user": _USER},
            )


async def test_a_platform_row_carrying_a_tenant_is_refused(pg_owner_engine, pg_sessionmaker):
    """The scope column and the id columns must agree, in both directions."""
    await ensure_tenants(pg_sessionmaker, _TENANT)
    with pytest.raises(IntegrityError, match="ck_settings_platform_row_has_no_tenant"):
        async with pg_owner_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO settings (scope, tenant_id, user_id, key, value) "
                    "VALUES ('platform', :tenant, NULL, 'agent.mode', '\"fast\"'::jsonb)"
                ),
                {"tenant": _TENANT},
            )


async def test_an_unknown_scope_is_refused(pg_owner_engine, pg_sessionmaker):
    """The scope is a closed set; a fourth layer would resolve as nothing at all.

    The row is otherwise perfectly formed — it carries a tenant, so the two
    scope/id agreement constraints are satisfied and the only thing left to refuse it is
    the closed set of scopes itself.
    """
    await ensure_tenants(pg_sessionmaker, _TENANT)
    with pytest.raises(IntegrityError, match="setting_scope"):
        async with pg_owner_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO settings (scope, tenant_id, user_id, key, value) "
                    "VALUES ('region', :tenant, NULL, 'agent.mode', '\"fast\"'::jsonb)"
                ),
                {"tenant": _TENANT},
            )


def test_importing_the_catalogue_pulls_no_host_or_execution_stack():
    """``aegis.settings`` is importable by a consumer with no web or agent stack."""
    code = (
        "import sys; import aegis.settings; "
        "assert 'sqlalchemy' in sys.modules; "
        "from aegis.data import AegisBase; "
        "missing = {'settings', 'tenants', 'users'} - set(AegisBase.metadata.tables); "
        "assert not missing, missing; "
        "banned = {'temporalio', 'fastapi', 'litellm', 'torch', 'langgraph', 'neo4j'}; "
        "hit = banned & set(sys.modules); assert not hit, hit"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": _SRC},
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
