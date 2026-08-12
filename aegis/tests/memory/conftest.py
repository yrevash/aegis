"""Shared fixtures for the memory suite — an offline SQLite store + a default fake spec.

Every test gets a fresh SQLite database with the ``aegis.data.AegisBase`` schema created,
and a process-wide default :class:`~aegis.memory.spec.MemorySpec` set to the offline
:data:`FAKE_SPEC` so recall/consolidate resolve the domain seam without a host.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aegis.data import AegisBase
from aegis.memory import reset_default_index, set_default_spec

from ._spec import FAKE_SPEC


@pytest.fixture(autouse=True)
def _default_spec():
    """Configure the process-wide default MemorySpec for every memory test."""
    set_default_spec(FAKE_SPEC)


@pytest.fixture(autouse=True)
def _fresh_vector_index():
    """Give every test a pristine embedded Qdrant index (no cross-test point bleed)."""
    reset_default_index()
    yield
    reset_default_index()


@pytest_asyncio.fixture
async def db(tmp_path) -> async_sessionmaker:
    """A dedicated SQLite memory DB with the aegis schema materialised."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mem.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(AegisBase.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()
