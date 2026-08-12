import pytest

from aegis.core.health import DependencyStatus, probe_postgres, probe_qdrant, probe_redis


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


class _OkQdrant:
    def get_collections(self) -> object:
        return object()


class _DownQdrant:
    def get_collections(self) -> None:
        raise ConnectionError("qdrant unreachable")


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
async def test_probe_qdrant_up() -> None:
    s = await probe_qdrant("http://x:6333", client=_OkQdrant())
    assert isinstance(s, DependencyStatus)
    assert s.name == "qdrant"
    assert s.status == "up"


@pytest.mark.asyncio
async def test_probe_qdrant_down() -> None:
    s = await probe_qdrant("http://x:6333", client=_DownQdrant())
    assert s.status == "down"
    assert "unreachable" in (s.detail or "")
