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


# ── Probe-owned clients must be closed (a polled /readyz leaks otherwise) ──


class _CountingRedis:
    """A probe-constructed redis client that records whether it was closed."""

    def __init__(self) -> None:
        self.closed = 0

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        self.closed += 1


class _CountingQdrant:
    def __init__(self) -> None:
        self.closed = 0

    def get_collections(self) -> object:
        return object()

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
async def test_probe_qdrant_closes_the_client_it_constructed(monkeypatch) -> None:
    """REGRESSION: probe_qdrant built a client per call and never closed it."""
    import aegis.core.health as health

    made: list[_CountingQdrant] = []

    class _Module:
        @staticmethod
        def QdrantClient(url: str) -> _CountingQdrant:  # noqa: N802 - mirrors the driver
            client = _CountingQdrant()
            made.append(client)
            return client

    monkeypatch.setattr(health, "require", lambda *a, **k: _Module)
    status = await health.probe_qdrant("http://x:6333")
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
