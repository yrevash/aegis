"""Shared fixtures for the memory suite — a real PostgreSQL store + a default fake spec.

Every DB-touching test gets its own clone of the session template database (see
``tests/conftest.py``), reached over the ``LOGIN NOSUPERUSER NOBYPASSRLS`` role, and a
process-wide default :class:`~aegis.memory.spec.MemorySpec` set to the offline
:data:`FAKE_SPEC` so recall/consolidate resolve the domain seam without a host.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from aegis.memory import reset_default_index, set_default_spec

from ._spec import FAKE_SPEC


@pytest.fixture(autouse=True)
def _default_spec():
    """Configure the process-wide default MemorySpec for every memory test."""
    set_default_spec(FAKE_SPEC)


@pytest.fixture(autouse=True)
def _fresh_vector_index():
    """Give every test a pristine embedded Chroma index (no cross-test point bleed)."""
    reset_default_index()
    yield
    reset_default_index()


@pytest_asyncio.fixture
async def db(pg_sessionmaker: async_sessionmaker) -> async_sessionmaker:
    """A private PostgreSQL memory database with the aegis schema materialised.

    Args:
        pg_sessionmaker: The unprivileged sessionmaker over this test's scratch database.
    """
    return pg_sessionmaker
