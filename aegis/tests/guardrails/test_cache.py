import pytest

from aegis.core.config import AegisMode
from aegis.guardrails.cache import InMemoryInjectionCache, make_injection_cache


def test_lite_returns_in_memory() -> None:
    c = make_injection_cache(AegisMode.lite)
    assert isinstance(c, InMemoryInjectionCache)


def test_full_without_redis_raises_not_falls_back() -> None:
    with pytest.raises(RuntimeError):
        make_injection_cache(AegisMode.full, redis_client=None)


def test_in_memory_roundtrip() -> None:
    c = InMemoryInjectionCache()
    c.set("k", "v")
    assert c.get("k") == "v"
