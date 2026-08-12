"""Semantic-cache tests (offline, explicit in-memory fallback — RedisVL not faked).

Covers the SOTA cache contract aegis.memory.cache.MemorySemanticCache promises: hit/miss,
TTL expiry (via an injectable monotonic clock), the cosine similarity threshold, write
invalidation, subject+tenant isolation, size eviction, and the fail-loud full-mode rule.
The production path is RedisVL (needs RediSearch, cannot run offline); here the explicit,
labeled in-memory backend exercises identical semantics.
"""

from __future__ import annotations

import pytest

from aegis.memory.cache import BACKEND_MEMORY, MemorySemanticCache
from aegis.memory.config import MemoryConfig

pytestmark = pytest.mark.asyncio


class _Clock:
    """A mutable monotonic clock for deterministic TTL tests."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


async def _embedder(texts: list[str]) -> list[list[float]]:
    """Deterministic tiny embedder: 'A'->x-axis, 'B'->y-axis, else diagonal."""
    out: list[list[float]] = []
    for t in texts:
        if "alpha" in t:
            out.append([1.0, 0.0])
        elif "beta" in t:
            out.append([0.0, 1.0])
        else:
            out.append([1.0, 1.0])
    return out


def _cache(config: MemoryConfig | None = None, clock: _Clock | None = None):
    return MemorySemanticCache.in_memory(
        config or MemoryConfig(),
        embedder=_embedder,
        time_fn=clock or _Clock(),
    )


async def test_miss_then_store_then_hit_same_query():
    cache = _cache()
    subject = "user:1"
    assert await cache.check(subject_id=subject, query="alpha q", query_vec=[1.0, 0.0]) is None
    await cache.store(
        subject_id=subject, query="alpha q", value={"text": "cached"}, query_vec=[1.0, 0.0]
    )
    hit = await cache.check(subject_id=subject, query="alpha q", query_vec=[1.0, 0.0])
    assert hit is not None
    assert hit.value == {"text": "cached"}
    assert hit.similarity == pytest.approx(1.0)
    assert hit.backend == BACKEND_MEMORY


async def test_backend_label_and_is_redis():
    cache = _cache()
    assert cache.backend_label == BACKEND_MEMORY
    assert cache.is_redis is False


async def test_similarity_threshold_gates_near_matches():
    # threshold 0.05 distance ⇒ cosine >= 0.95.
    cache = _cache(MemoryConfig(cache_distance_threshold=0.05))
    await cache.store(
        subject_id="s", query="alpha", value={"v": 1}, query_vec=[1.0, 0.0]
    )
    # cosine([1,0],[0.98,0.2]) ≈ 0.98 → hit
    assert await cache.check(subject_id="s", query="x", query_vec=[0.98, 0.2]) is not None
    # cosine([1,0],[0.9,0.44]) ≈ 0.9 → below threshold → miss
    assert await cache.check(subject_id="s", query="x", query_vec=[0.9, 0.44]) is None


async def test_ttl_expiry():
    clock = _Clock()
    cache = _cache(MemoryConfig(cache_ttl_seconds=100), clock=clock)
    await cache.store(subject_id="s", query="alpha", value={"v": 1}, query_vec=[1.0, 0.0])
    assert await cache.check(subject_id="s", query="alpha", query_vec=[1.0, 0.0]) is not None
    clock.t = 99.0
    assert await cache.check(subject_id="s", query="alpha", query_vec=[1.0, 0.0]) is not None
    clock.t = 101.0  # past TTL
    assert await cache.check(subject_id="s", query="alpha", query_vec=[1.0, 0.0]) is None


async def test_invalidate_drops_subject_entries():
    cache = _cache()
    await cache.store(subject_id="s", query="alpha", value={"v": 1}, query_vec=[1.0, 0.0])
    await cache.store(subject_id="s", query="beta", value={"v": 2}, query_vec=[0.0, 1.0])
    dropped = await cache.invalidate(subject_id="s")
    assert dropped == 2
    assert await cache.check(subject_id="s", query="alpha", query_vec=[1.0, 0.0]) is None
    assert await cache.check(subject_id="s", query="beta", query_vec=[0.0, 1.0]) is None


async def test_subject_isolation():
    cache = _cache()
    await cache.store(subject_id="s1", query="alpha", value={"v": 1}, query_vec=[1.0, 0.0])
    # Same query vector, different subject → never a cross-subject hit.
    assert await cache.check(subject_id="s2", query="alpha", query_vec=[1.0, 0.0]) is None
    assert await cache.check(subject_id="s1", query="alpha", query_vec=[1.0, 0.0]) is not None


async def test_tenant_isolation():
    cache = _cache()
    await cache.store(
        subject_id="s", query="alpha", value={"v": 1}, query_vec=[1.0, 0.0], tenant_id=1
    )
    assert (
        await cache.check(subject_id="s", query="alpha", query_vec=[1.0, 0.0], tenant_id=2)
        is None
    )
    assert (
        await cache.check(subject_id="s", query="alpha", query_vec=[1.0, 0.0], tenant_id=1)
        is not None
    )
    # The null tenant is its own scope, distinct from tenant 1.
    assert await cache.check(subject_id="s", query="alpha", query_vec=[1.0, 0.0]) is None


async def test_invalidate_is_tenant_scoped():
    cache = _cache()
    await cache.store(
        subject_id="s", query="alpha", value={"v": 1}, query_vec=[1.0, 0.0], tenant_id=1
    )
    await cache.store(
        subject_id="s", query="alpha", value={"v": 2}, query_vec=[1.0, 0.0], tenant_id=2
    )
    assert await cache.invalidate(subject_id="s", tenant_id=1) == 1
    assert (
        await cache.check(subject_id="s", query="alpha", query_vec=[1.0, 0.0], tenant_id=2)
        is not None
    )


async def test_size_eviction_evicts_oldest():
    cache = _cache(MemoryConfig(cache_max_entries=2))
    for i, q in enumerate(["q0", "q1", "q2"]):
        await cache.store(
            subject_id="s", query=q, value={"i": i}, query_vec=[1.0, float(i)]
        )
    # q0 (oldest) evicted once the third entry pushed past the cap of 2.
    assert await cache.check(subject_id="s", query="q0", query_vec=[1.0, 0.0]) is None
    assert await cache.check(subject_id="s", query="q2", query_vec=[1.0, 2.0]) is not None


async def test_embedder_used_when_no_vector_supplied():
    cache = _cache()
    # Store/check without a precomputed vector → the injected embedder maps by keyword.
    await cache.store(subject_id="s", query="alpha question", value={"v": 1})
    assert await cache.check(subject_id="s", query="alpha again") is not None
    assert await cache.check(subject_id="s", query="beta different") is None


async def test_full_mode_requires_redis_url():
    with pytest.raises(RuntimeError, match="requires a Redis URL"):
        MemorySemanticCache.from_config(
            MemoryConfig(), embedder=_embedder, require_redis=True, redis_url=None, dims=2
        )


async def test_from_config_offline_defaults_to_in_memory():
    cache = MemorySemanticCache.from_config(
        MemoryConfig(), embedder=_embedder, redis_url=None, dims=2
    )
    assert cache.backend_label == BACKEND_MEMORY
    assert cache.is_redis is False
