"""Tests for the answer-level semantic cache (cosine NN over cached answers, per scope).

Offline: uses the real :class:`app.retrieval.memory.InMemoryRedis` fake so the genuine
``AnswerCache`` runs with no Redis server. ``asyncio_mode = "auto"`` (pyproject) runs the
coroutine tests without per-test decorators.
"""

from __future__ import annotations

from aegis.retrieval.answer_cache import AnswerCache, AnswerCacheHit
from aegis.retrieval.memory import InMemoryRedis


class _TTLSpyRedis(InMemoryRedis):
    """InMemoryRedis that records the ``ex`` (TTL) passed to every ``set``.

    InMemoryRedis accepts a TTL for parity but does not enforce expiry, so we cannot
    exercise a real expiry path. Instead we verify — honestly and offline — that the
    cache actually hands the configured TTL to the client, which is the behaviour a real
    Redis would act on.
    """

    def __init__(self) -> None:
        super().__init__()
        self.ex_calls: list[int | None] = []

    async def set(self, key: str, value: str, *, ex: int | None = None) -> object:
        self.ex_calls.append(ex)
        return await super().set(key, value, ex=ex)


def _cache(client: InMemoryRedis | None = None, threshold: float = 0.97) -> AnswerCache:
    return AnswerCache(
        client if client is not None else InMemoryRedis(),
        ttl_seconds=60,
        similarity_threshold=threshold,
    )


async def test_set_then_get_returns_answer_for_same_embedding():
    cache = _cache(threshold=0.9)
    await cache.set(
        query="what is x?",
        embedding=[1.0, 0.0, 0.0],
        answer="x is the answer",
        scope="tenant-a",
        sources=[{"id": "s1", "text": "src"}],
    )
    hit = await cache.get([1.0, 0.0, 0.0], scope="tenant-a")
    assert isinstance(hit, AnswerCacheHit)
    assert hit.answer == "x is the answer"
    assert hit.query == "what is x?"
    assert hit.similarity == 1.0
    assert hit.cached_at  # ISO8601 write timestamp recorded
    assert hit.sources == [{"id": "s1", "text": "src"}]


async def test_near_duplicate_above_threshold_hits_below_misses():
    cache = _cache(threshold=0.97)
    await cache.set(
        query="original", embedding=[1.0, 0.0, 0.0], answer="cached", scope="s"
    )
    # cosine ~0.99 with the stored vector → clears the bar → hit.
    near = await cache.get([0.99, 0.141, 0.0], scope="s")
    assert near is not None
    assert near.answer == "cached"
    assert near.similarity >= 0.97
    # cosine ~0.95 with the stored vector → below the bar → miss.
    far = await cache.get([0.95, 0.3122, 0.0], scope="s")
    assert far is None


async def test_scope_isolation_never_returns_other_scope():
    cache = _cache(threshold=0.5)
    await cache.set(
        query="q", embedding=[1.0, 0.0], answer="tenant-a secret", scope="tenant-a"
    )
    # Identical embedding, different scope → MUST NOT leak across scopes.
    assert await cache.get([1.0, 0.0], scope="tenant-b") is None
    # Same scope still hits.
    hit = await cache.get([1.0, 0.0], scope="tenant-a")
    assert hit is not None
    assert hit.answer == "tenant-a secret"


async def test_same_query_different_scopes_do_not_collide():
    cache = _cache(threshold=0.9)
    await cache.set(query="q", embedding=[1.0, 0.0], answer="A", scope="a")
    await cache.set(query="q", embedding=[1.0, 0.0], answer="B", scope="b")
    hit_a = await cache.get([1.0, 0.0], scope="a")
    hit_b = await cache.get([1.0, 0.0], scope="b")
    assert hit_a is not None and hit_a.answer == "A"
    assert hit_b is not None and hit_b.answer == "B"


async def test_ttl_is_passed_to_client():
    # InMemoryRedis does not enforce expiry; verify the configured TTL is handed to the
    # client on every write (the value a real Redis would expire on).
    client = _TTLSpyRedis()
    cache = AnswerCache(client, ttl_seconds=1800, similarity_threshold=0.9)
    await cache.set(query="q", embedding=[1.0, 0.0], answer="a", scope="s")
    assert client.ex_calls == [1800]


async def test_empty_index_returns_none():
    cache = _cache()
    assert await cache.get([1.0, 0.0], scope="never-written") is None


async def test_none_client_never_raises_and_misses():
    cache = AnswerCache(None, ttl_seconds=60, similarity_threshold=0.9)  # type: ignore[arg-type]
    # set is a no-op, get is an honest miss — neither raises without a client.
    await cache.set(query="q", embedding=[1.0, 0.0], answer="a", scope="s")
    assert await cache.get([1.0, 0.0], scope="s") is None
