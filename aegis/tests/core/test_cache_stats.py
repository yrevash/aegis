"""The cache counters say what happened, and say nothing where nothing happened.

These tests exist because the cache surface used to render invented figures. The
defence is not "the numbers look plausible" — it is that every number comes off the
branch inside a real cache that decided it, and that the absence of a number is
representable and is actually returned.

Each test drives a **real cache class**, never the registry alone: a counter wired to
the wrong branch, or wired to no branch, is exactly the defect that would survive a test
of the registry in isolation.
"""

from __future__ import annotations

import pytest

from aegis.core.cache_stats import (
    CACHE_INJECTION,
    CACHE_KEYS,
    CACHE_WEB_SEARCH,
    cache_reports,
    record_eviction,
    reset_cache_stats,
    spec_for,
)
from aegis.guardrails.cache import InMemoryInjectionCache
from aegis.websearch.cache import InMemoryWebSearchCache


@pytest.fixture(autouse=True)
def _clean() -> None:
    """Start every test from an empty registry; other tests share the process."""
    reset_cache_stats()
    yield
    reset_cache_stats()


def _report(key: str):
    """Return the single report for ``key``."""
    return next(r for r in cache_reports() if r.key == key)


def test_an_unused_cache_reports_no_hit_rate_rather_than_zero() -> None:
    """The fabricated-zero rule, at the one place it can be enforced.

    ``0.0`` and "nobody has read this cache" are different facts and the surface has to
    be able to say the second. A cache with no lookups reports ``hit_rate is None`` — a
    value the wire model carries through as ``null`` and the page renders as a sentence.
    """
    for report in cache_reports():
        assert report.lookups == 0
        assert report.hit_rate is None, report.key
        assert report.registered is False, report.key
        # Configuration is unknown, not defaulted: claiming a TTL for a cache no
        # instance was built with would be the same invention in another field.
        assert report.backend is None, report.key
        assert report.ttl_seconds is None, report.key


def test_an_injection_hit_and_miss_are_counted_on_the_branch_that_decided_them() -> None:
    """A miss then a hit through the real cache produce exactly one of each."""
    cache = InMemoryInjectionCache()

    assert cache.get("digest") is None
    assert cache.get("digest") is None
    cache.set("digest", "clean")
    assert cache.get("digest") == "clean"

    report = _report(CACHE_INJECTION)
    assert (report.lookups, report.hits, report.misses) == (3, 1, 2)
    assert report.hit_rate == pytest.approx(1 / 3)
    assert report.stores == 1
    assert report.entries == 1
    assert report.registered is True
    assert report.backend == "in_memory"


def test_a_cache_that_cannot_evict_reports_none_and_one_that_can_reports_the_count() -> None:
    """The capped cache counts real evictions; the uncapped one reports ``None``.

    ``None`` is the whole point: the injection cache performs no eviction this process
    can observe, and ``0`` would claim it had the opportunity and declined.
    """
    web = InMemoryWebSearchCache(max_entries=1)
    web.set("a", "1")
    web.set("b", "2")  # forces "a" out — the cap, not a TTL

    evicting = _report(CACHE_WEB_SEARCH)
    assert evicting.evictions == 1
    assert evicting.capacity == 1
    assert evicting.entries == 1

    InMemoryInjectionCache().set("k", "v")
    assert _report(CACHE_INJECTION).evictions is None


def test_an_expired_entry_is_a_miss_and_never_an_eviction() -> None:
    """A TTL expiry and a cap eviction are different events and stay different numbers.

    Counting the expiry sweep as an eviction would make the eviction figure mean "the
    cache dropped something", which is not what an operator reads it as — they read it
    as "the cap is biting".
    """
    web = InMemoryWebSearchCache(max_entries=8)
    web.set("k", "v", ttl=0)

    assert web.get("k") is None
    report = _report(CACHE_WEB_SEARCH)
    assert (report.hits, report.misses) == (0, 1)
    assert report.evictions == 0


def test_counting_an_eviction_against_a_cache_declared_not_to_evict_is_refused() -> None:
    """The spec and the counter cannot drift apart silently.

    If a cache starts evicting, its :class:`~aegis.core.cache_stats.CacheSpec` has to say
    so — otherwise the page would keep reporting "no eviction here" while the number
    climbed, which is the quiet version of the defect this module exists to end.
    """
    assert spec_for(CACHE_INJECTION).evicts is False
    with pytest.raises(ValueError, match="declared as not evicting"):
        record_eviction(CACHE_INJECTION)


def test_every_declared_cache_gets_a_row_even_when_it_did_nothing() -> None:
    """A cache that did nothing is listed saying so, never omitted."""
    assert tuple(r.key for r in cache_reports()) == CACHE_KEYS
