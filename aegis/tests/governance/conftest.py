"""Shared fixtures for the governance suite — a real PostgreSQL store, no host.

Every DB-touching test gets its own clone of the session template database (see
``tests/conftest.py``), reached over the ``LOGIN NOSUPERUSER NOBYPASSRLS`` role, with the
enforcement/audit data layers configured to draw sessions from it. Argon2/JWT and the
RBAC/context helpers need no database and run without these fixtures.

Connecting as an unprivileged role is the point, not an incidental detail: the
``tenant_isolation`` policies the template carries are only enforced against a role that
is neither ``SUPERUSER`` nor ``BYPASSRLS``, so this is what makes a tenancy assertion in
this suite mean anything.
"""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

# Import the governance ORM so its tables register on AegisBase.metadata before
# create_all (import side-effect only).
import aegis.governance.models  # noqa: F401
from aegis.governance import configure_governance


@pytest_asyncio.fixture
async def db(pg_sessionmaker: async_sessionmaker) -> async_sessionmaker:
    """A private PostgreSQL governance database, wired into the governance data layers.

    Yields the sessionmaker and wires it into the enforcement/audit data layers via
    :func:`configure_governance`, so ``enforce_governance`` / ``record_usage`` /
    ``record_audit`` and the admin rollups all round-trip against it.

    Args:
        pg_sessionmaker: The unprivileged sessionmaker over this test's scratch database.
    """
    configure_governance(session_factory=lambda: pg_sessionmaker())
    return pg_sessionmaker
