"""Shared fixtures for the governance suite — an offline SQLite store, no host.

Every DB-touching test gets a fresh in-memory-file SQLite database with the
``aegis.data.AegisBase`` schema created (the governance tables register on it), and
the enforcement/audit data layers configured to draw sessions from it. Argon2/JWT and
the RBAC/context helpers need no database and run without these fixtures.
"""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Import the governance ORM so its tables register on AegisBase.metadata before
# create_all (import side-effect only).
import aegis.governance.models  # noqa: F401
from aegis.data import AegisBase
from aegis.governance import configure_governance


@pytest_asyncio.fixture
async def db(tmp_path) -> async_sessionmaker:
    """A dedicated SQLite governance DB with the aegis schema materialised.

    Yields the sessionmaker and wires it into the enforcement/audit data layers via
    :func:`configure_governance`, so ``enforce_governance`` / ``record_usage`` /
    ``record_audit`` and the admin rollups all round-trip against it.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gov.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(AegisBase.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    configure_governance(session_factory=lambda: sessionmaker())
    yield sessionmaker
    await engine.dispose()
