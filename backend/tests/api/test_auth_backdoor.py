"""Demo-credential backdoor is gated to dev and never overrides real accounts (C2).

Pre-fix, ``_DEMO_USERS`` was consulted on ANY database miss — including a wrong
password for an existing account — and always granted ``platform_admin``. These
tests pin the closed behaviour: the demo table is a dev-only fallback that never
authenticates a real user's wrong password and is disabled entirely outside dev.
The money-shot (dev ``admin``/``admin`` still logs in) is covered in
``test_admin_governance``.
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
                role=Role.USER,
                tenant_id=tenant_id,
                password_hash=hash_password(password),
                is_active=True,
            )
        )
        await session.commit()


async def test_wrong_password_for_real_user_is_rejected(client, db):
    # Seed a real ``user`` account whose real password differs from the demo one.
    # Logging in with the *demo* password ("user") is a wrong password for this real
    # account: pre-fix it fell through to the demo table and authenticated anyway.
    await _seed_user("user", "s3cret-real")
    resp = await client.post(
        "/auth/login", json={"username": "user", "password": "user"}
    )
    assert resp.status_code == 401  # never falls through to the demo table

    ok = await client.post(
        "/auth/login", json={"username": "user", "password": "s3cret-real"}
    )
    assert ok.status_code == 200


async def test_demo_never_overrides_a_real_account(client, db):
    # A real ``admin`` row exists with its own password; the demo ``admin``/``admin``
    # must NOT authenticate (and must not grant platform_admin).
    await _seed_user("admin", "the-real-password", tenant_id=None)
    demo = await client.post(
        "/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert demo.status_code == 401

    real = await client.post(
        "/auth/login", json={"username": "admin", "password": "the-real-password"}
    )
    assert real.status_code == 200


async def test_demo_disabled_outside_dev(client, db, monkeypatch):
    # In any non-dev environment the demo backdoor is fully closed, even with no
    # real users row present.
    monkeypatch.setattr(get_settings(), "app_env", "prod")
    resp = await client.post(
        "/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert resp.status_code == 401
