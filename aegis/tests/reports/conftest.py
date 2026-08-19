"""A real PostgreSQL governance store for the export readers.

Identical to the governance suite's fixture, and deliberately so: the export reads the
governance tables, over the same ``LOGIN NOSUPERUSER NOBYPASSRLS`` role, so a tenancy
assertion here means what it means there. A second, laxer fixture would be a second
answer to "is this row visible?".
"""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

# Import the governance ORM so its tables register before create_all (side-effect only).
import aegis.governance.models  # noqa: F401
from aegis.governance import configure_governance


@pytest_asyncio.fixture
async def db(pg_sessionmaker: async_sessionmaker) -> async_sessionmaker:
    """Wire the governance data layer to this test's private database.

    Args:
        pg_sessionmaker: The unprivileged sessionmaker over the scratch database.
    """
    configure_governance(session_factory=lambda: pg_sessionmaker())
    return pg_sessionmaker
