import pytest

from aegis.core.health import DependencyStatus, probe_pgvector, probe_postgres, probe_redis


class _OkRedis:
    async def ping(self) -> bool:
        return True


class _DownRedis:
    async def ping(self) -> None:
        raise ConnectionError("refused")


class _OkPostgres:
    async def execute(self, query: str) -> None:
        pass

    async def fetchrow(self, query: str) -> None:
        return {"extname": "vector"}


class _DownPostgres:
    async def execute(self, query: str) -> None:
        raise ConnectionError("connection failed")

    async def fetchrow(self, query: str) -> None:
        raise ConnectionError("connection failed")

    async def close(self) -> None:
        pass


class _NoPgvectorPostgres:
    async def fetchrow(self, query: str) -> None:
        return None


@pytest.mark.asyncio
async def test_probe_redis_up() -> None:
    s = await probe_redis("redis://x", client=_OkRedis())
    assert isinstance(s, DependencyStatus)
    assert s.status == "up"


@pytest.mark.asyncio
async def test_probe_redis_down() -> None:
    s = await probe_redis("redis://x", client=_DownRedis())
    assert s.status == "down"
    assert "refused" in (s.detail or "")


@pytest.mark.asyncio
async def test_probe_postgres_up() -> None:
    s = await probe_postgres("postgresql://x", conn=_OkPostgres())
    assert isinstance(s, DependencyStatus)
    assert s.status == "up"


@pytest.mark.asyncio
async def test_probe_postgres_down() -> None:
    s = await probe_postgres("postgresql://x", conn=_DownPostgres())
    assert s.status == "down"
    assert "connection failed" in (s.detail or "")


@pytest.mark.asyncio
async def test_probe_pgvector_up() -> None:
    s = await probe_pgvector("postgresql://x", conn=_OkPostgres())
    assert isinstance(s, DependencyStatus)
    assert s.status == "up"


@pytest.mark.asyncio
async def test_probe_pgvector_missing() -> None:
    s = await probe_pgvector("postgresql://x", conn=_NoPgvectorPostgres())
    assert s.status == "down"
    assert "missing" in (s.detail or "")
