import pytest

from aegis.core.health import (
    DependencyStatus,
    probe_postgres,
    probe_redis,
    probe_vector_store,
)


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


class _OkVectorStore:
    def list_collections(self) -> list[str]:
        return []


class _DownVectorStore:
    def list_collections(self) -> None:
        raise OSError("vector store directory unreadable")


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
async def test_probe_vector_store_up() -> None:
    s = await probe_vector_store("/var/aegis/vectors", client=_OkVectorStore())
    assert isinstance(s, DependencyStatus)
    assert s.name == "vector_store"
    assert s.status == "up"


@pytest.mark.asyncio
async def test_probe_vector_store_down() -> None:
    s = await probe_vector_store("/var/aegis/vectors", client=_DownVectorStore())
    assert s.status == "down"
    assert "unreadable" in (s.detail or "")


# ── Probe-owned clients must be closed (a polled /readyz leaks otherwise) ──


class _CountingRedis:
    """A probe-constructed redis client that records whether it was closed."""

    def __init__(self) -> None:
        self.closed = 0

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        self.closed += 1


class _CountingVectorStore:
    def __init__(self) -> None:
        self.closed = 0

    def list_collections(self) -> list[str]:
        return []

    def close(self) -> None:
        self.closed += 1


@pytest.mark.asyncio
async def test_probe_redis_closes_the_client_it_constructed(monkeypatch) -> None:
    """REGRESSION: probe_redis built a client per call and never closed it."""
    import aegis.core.health as health

    made: list[_CountingRedis] = []

    class _Module:
        @staticmethod
        def from_url(url: str) -> _CountingRedis:
            client = _CountingRedis()
            made.append(client)
            return client

    monkeypatch.setattr(health, "require", lambda *a, **k: _Module)
    status = await health.probe_redis("redis://x")
    assert status.status == "up"
    assert made and made[0].closed == 1


@pytest.mark.asyncio
async def test_probe_vector_store_closes_the_client_it_constructed(monkeypatch) -> None:
    """REGRESSION: a probe that builds a client per call must also close it."""
    import aegis.core.health as health

    made: list[_CountingVectorStore] = []

    class _Module:
        @staticmethod
        def PersistentClient(path: str) -> _CountingVectorStore:  # noqa: N802 - driver name
            client = _CountingVectorStore()
            made.append(client)
            return client

    monkeypatch.setattr(health, "require", lambda *a, **k: _Module)
    status = await health.probe_vector_store("/var/aegis/vectors")
    assert status.status == "up"
    assert made and made[0].closed == 1


@pytest.mark.asyncio
async def test_probe_closes_the_client_even_when_the_probe_fails(monkeypatch) -> None:
    import aegis.core.health as health

    made: list[_CountingRedis] = []

    class _Boom(_CountingRedis):
        async def ping(self) -> None:
            raise ConnectionError("refused")

    class _Module:
        @staticmethod
        def from_url(url: str) -> _Boom:
            client = _Boom()
            made.append(client)
            return client

    monkeypatch.setattr(health, "require", lambda *a, **k: _Module)
    status = await health.probe_redis("redis://x")
    assert status.status == "down"
    assert made[0].closed == 1


@pytest.mark.asyncio
async def test_an_injected_client_is_never_closed_by_the_probe() -> None:
    """An injected client belongs to the caller — the probe must not close it."""
    injected = _CountingRedis()
    await probe_redis("redis://x", client=injected)
    assert injected.closed == 0
