"""The web-search cache: explicit backend choice, a real key, and a real cap."""

from __future__ import annotations

import time

import pytest

from aegis.core.config import AegisMode
from aegis.websearch.cache import (
    INDEX_KEY,
    InMemoryWebSearchCache,
    RedisWebSearchCache,
    cache_key,
    make_web_search_cache,
    normalise_query,
)


class FakeRedis:
    """A minimal in-process stand-in for Memurai/Redis (never a real connection)."""

    def __init__(self):
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.zset: dict[str, float] = {}

    def get(self, key):
        return self.values.get(key)

    def setex(self, key, ttl, value):
        self.values[key] = value
        self.ttls[key] = ttl

    def zadd(self, key, mapping):
        assert key == INDEX_KEY
        self.zset.update(mapping)

    def zcard(self, key):
        return len(self.zset)

    def zrange(self, key, start, end):
        ordered = sorted(self.zset, key=lambda k: self.zset[k])
        return ordered[start : end + 1]

    def zrem(self, key, *members):
        for member in members:
            self.zset.pop(member, None)

    def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)
            self.ttls.pop(key, None)


# ── The key ──────────────────────────────────────────────────────────────────


def test_the_key_folds_case_and_whitespace():
    """One paid call, not two, for the same question typed differently."""
    assert normalise_query("  Latest   FDA Guidance ") == "latest fda guidance"
    assert cache_key("tavily", "Latest  FDA Guidance", 5) == cache_key(
        "tavily", "latest fda guidance", 5
    )


def test_the_key_separates_providers_and_result_caps():
    """A 5-result request must never be served from a 3-result entry."""
    assert cache_key("tavily", "q", 5) != cache_key("brave", "q", 5)
    assert cache_key("tavily", "q", 5) != cache_key("tavily", "q", 3)


def test_the_key_never_carries_the_raw_query():
    """The query is hashed; a cache dump is not a log of what people asked."""
    key = cache_key("tavily", "acquire competitor xyz", 5)
    assert "acquire" not in key and "competitor" not in key
    assert key.startswith("aegis:websearch:v1:")


def test_punctuation_still_changes_the_key():
    """Normalisation folds case and spacing only — punctuation changes meaning."""
    assert cache_key("tavily", "who is on call?", 5) != cache_key(
        "tavily", "who is on call", 5
    )


# ── The in-memory backend ────────────────────────────────────────────────────


def test_in_memory_round_trip_and_miss():
    cache = InMemoryWebSearchCache()
    assert cache.get("k") is None
    cache.set("k", "v")
    assert cache.get("k") == "v"


def test_in_memory_honours_the_ttl():
    cache = InMemoryWebSearchCache()
    cache.set("k", "v", ttl=0)
    time.sleep(0.001)
    assert cache.get("k") is None


def test_in_memory_is_capped():
    """R8: an unbounded cache of arbitrary web pages is the memory-pressure route."""
    cache = InMemoryWebSearchCache(max_entries=2)
    cache.set("a", "1")
    cache.set("b", "2")
    cache.set("c", "3")
    assert cache.get("a") is None
    assert cache.get("b") == "2" and cache.get("c") == "3"


# ── The Redis backend ────────────────────────────────────────────────────────


def test_redis_round_trip_sets_a_ttl():
    client = FakeRedis()
    cache = RedisWebSearchCache(client)
    cache.set("k", "v", ttl=42)
    assert cache.get("k") == "v"
    assert client.ttls["k"] == 42


def test_redis_decodes_bytes():
    """redis-py returns bytes by default; callers must not have to know that."""
    client = FakeRedis()
    client.values["k"] = b"v"
    assert RedisWebSearchCache(client).get("k") == "v"


def test_redis_trims_the_oldest_entries_past_the_cap():
    """The cap holds across processes because the index lives in Redis, not in RAM."""
    client = FakeRedis()
    cache = RedisWebSearchCache(client, max_entries=2)
    for key in ("a", "b", "c", "d"):
        cache.set(key, key)
        time.sleep(0.001)
    assert cache.get("a") is None and cache.get("b") is None
    assert cache.get("c") == "c" and cache.get("d") == "d"
    assert len(client.zset) == 2


# ── The backend choice is explicit ───────────────────────────────────────────


def test_full_mode_without_a_client_raises_rather_than_degrading_silently():
    """Same discipline as the injection cache: no `except -> in-memory` path."""
    with pytest.raises(RuntimeError, match="AEGIS_MODE=full"):
        make_web_search_cache(AegisMode.full)


def test_full_mode_with_a_client_is_redis_backed():
    cache = make_web_search_cache(AegisMode.full, redis_client=FakeRedis())
    assert isinstance(cache, RedisWebSearchCache)


@pytest.mark.parametrize("mode", [AegisMode.lite, AegisMode.auto])
def test_non_full_modes_get_the_in_memory_backend_with_a_warning(mode, caplog):
    with caplog.at_level("WARNING", logger="aegis.websearch.cache"):
        cache = make_web_search_cache(mode)
    assert isinstance(cache, InMemoryWebSearchCache)
    assert any("in-memory" in r.getMessage() for r in caplog.records)
