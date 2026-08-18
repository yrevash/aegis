"""The ``users`` table is the only authority a login has (C2, §3.8).

There used to be a second one: ``_DEMO_USERS``, a table inside the login handler that
minted an un-tenanted ``platform_admin`` on any database miss. It was narrowed (dev only,
never overriding a real row) and then **deleted** in §3.8, because a login path that
invents a principal when the database has none is why this deployment reached 2026-08
with zero tenants, zero budgets and every per-tenant control unexercised.

These tests pin what replaced it: a real row authenticates, a wrong password for a real
row does not — in *any* environment, since there is no environment-gated fallback left —
and the two states that are not a credential failure (an empty identity store, an
unreachable one) are reported as themselves rather than as a 401.
"""

from __future__ import annotations

import pytest

from app.api.schemas import Role
from app.config import get_settings
from app.core.security import hash_password
from app.data import Tenant, User, get_sessionmaker

pytestmark = pytest.mark.asyncio


async def _seed_user(username: str, password: str, *, tenant_id: int | None = 1) -> None:
    async with get_sessionmaker()() as session:
        if tenant_id is not None:
            session.add(Tenant(id=tenant_id, name=f"T{tenant_id}"))
        session.add(
            User(
                username=username,
                role=Role.CLIENT,
                tenant_id=tenant_id,
                password_hash=hash_password(password),
                is_active=True,
            )
        )
        await session.commit()


async def test_wrong_password_for_real_user_is_rejected(client, db):
    # Seed a real ``client`` account whose real password differs from the seed's.
    # ``client`` is a seeded username; logging in with the *seed* password ("demo") is a
    # wrong password for this account, so it must fail — never falling through to
    # anything else (pre-fix it authenticated anyway).
    await _seed_user("client", "s3cret-real")
    resp = await client.post(
        "/auth/login", json={"username": "client", "password": "demo"}
    )
    assert resp.status_code == 401  # there is no second authority to fall through to

    ok = await client.post(
        "/auth/login", json={"username": "client", "password": "s3cret-real"}
    )
    assert ok.status_code == 200


async def test_demo_never_overrides_a_real_account(client, db):
    # A real ``admin`` row exists with its own password; the old demo ``admin``/``demo``
    # credential must NOT authenticate (and must not grant platform_admin).
    await _seed_user("admin", "the-real-password", tenant_id=None)
    demo = await client.post(
        "/auth/login", json={"username": "admin", "password": "demo"}
    )
    assert demo.status_code == 401

    real = await client.post(
        "/auth/login", json={"username": "admin", "password": "the-real-password"}
    )
    assert real.status_code == 200


async def test_demo_credentials_are_refused_in_every_environment(client, db, monkeypatch):
    """The old demo credential authenticates nowhere, dev included (§3.8).

    Its predecessor asserted this only for ``app_env != "dev"``, because in dev the
    fallback deliberately still worked. There is no fallback now, so the assertion holds
    in both environments — and dev is the one that used to be open, which is why it is
    the case worth pinning.
    """
    await _seed_user("someone.else", "unrelated-password", tenant_id=None)
    for env in ("dev", "prod"):
        monkeypatch.setattr(get_settings(), "app_env", env)
        resp = await client.post(
            "/auth/login", json={"username": "admin", "password": "demo"}
        )
        assert resp.status_code == 401, env


async def test_an_empty_identity_store_says_so_and_names_the_fix(client, db):
    """No users at all is a 503 naming ``python -m app.seed``, not a 401.

    A 401 would blame the operator's typing for a deployment that was never provisioned —
    and it is exactly the answer the deleted fallback existed to avoid giving, by
    inventing an admin instead. Reporting the real state is the point of removing it.
    """
    resp = await client.post(
        "/auth/login", json={"username": "admin", "password": "demo"}
    )
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "python -m app.seed" in detail, detail


async def test_a_provisioned_store_answers_an_unknown_user_with_401(client, db):
    """Once *any* account exists, an unknown username is an ordinary credential failure.

    The empty-store 503 must not leak into the normal case: a login surface that answered
    503 for every unknown username would be an account-enumeration oracle and would hide
    real typos behind an infrastructure error.
    """
    await _seed_user("northwind.client", "a-real-password", tenant_id=None)
    resp = await client.post(
        "/auth/login", json={"username": "nobody", "password": "whatever"}
    )
    assert resp.status_code == 401
