"""The web-search cache — Memurai/Redis in full mode, in-memory only when said so.

A rehearsed demo query on a warm cache costs zero provider calls, which is what the
phase-05 budget arithmetic assumes, and it is also the rate-limit and
conference-wifi insurance: the second run of a query that already worked cannot be
broken by the network.

Two properties are deliberate and both are about not lying:

* **Backend choice is explicit.** ``full`` mode requires a real client and raises
  without one, exactly like :mod:`aegis.guardrails.cache`. There is no
  ``except -> in-memory`` path here; a caller that wants the degradation must ask
  for it by mode.
* **The cache is capped.** Both backends bound their entry count (plan 02's R8 —
  Memurai memory pressure on a 16 GB box is a real failure mode, and an unbounded
  cache of arbitrary web content is the fastest way to reach it). The Redis backend
  keeps a sorted-set index of its own keys and trims the oldest past the cap, so the
  bound holds across processes rather than only inside one.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any, Protocol

from aegis.core.config import AegisMode

logger = logging.getLogger(__name__)

#: Default time-to-live for a cached search, in seconds. An hour: long enough that a
#: rehearsal and the demo itself share one provider call, short enough that "what
#: happened today" is not answered from yesterday.
DEFAULT_TTL_SECONDS = 3600

#: Default cap on cached searches. Web content is large; this is the R8 bound.
DEFAULT_MAX_ENTRIES = 512

#: Redis key prefix, carrying a schema version. Bump ``v1`` when the cached payload
#: shape changes, so an old entry can never be rehydrated as a new one.
KEY_PREFIX = "aegis:websearch:v1"

#: The sorted-set that indexes this cache's own keys so the cap can be enforced.
INDEX_KEY = f"{KEY_PREFIX}:index"

_WHITESPACE = re.compile(r"\s+")


def normalise_query(query: str) -> str:
    """Return the canonical form of ``query`` used for cache keying.

    Case-folded and whitespace-collapsed, so ``"Latest  FDA Guidance"`` and
    ``"latest fda guidance"`` are one cache entry rather than two. Nothing else is
    touched — punctuation changes meaning in a search query.
    """
    return _WHITESPACE.sub(" ", query.strip()).casefold()


def cache_key(provider: str, query: str, max_results: int) -> str:
    """Build the cache key for one search.

    Keyed on ``(provider, normalised query, max_results)`` hashed with SHA-256.
    Every part matters: two providers answer the same query differently, and a
    5-result request must not be served from a 3-result entry.

    There is deliberately **no tenant in the key**. A public web search is not
    tenant data — the query text is the only thing that could be, and it is hashed,
    never stored. Sharing the entry across tenants is the point of the cache.

    Args:
        provider: The client's stable ``name``.
        query: The raw query text.
        max_results: The requested result count.

    Returns:
        The fully-qualified cache key string.
    """
    material = f"{provider}\x1f{normalise_query(query)}\x1f{max_results}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"{KEY_PREFIX}:{digest}"


class WebSearchCache(Protocol):
    """A minimal TTL key→value cache for serialised search responses."""

    def get(self, key: str) -> str | None:
        """Return the cached value for ``key`` or ``None``."""
        ...

    def set(self, key: str, value: str, *, ttl: int = DEFAULT_TTL_SECONDS) -> None:
        """Store ``value`` under ``key`` for ``ttl`` seconds."""
        ...


class InMemoryWebSearchCache:
    """A process-local, size-capped, TTL cache (lite/tests only — non-durable)."""

    def __init__(self, *, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        """Create an empty cache bounded to ``max_entries`` insertion-ordered entries."""
        self._max_entries = max(1, max_entries)
        self._data: dict[str, tuple[float, str]] = {}

    def get(self, key: str) -> str | None:
        """Return the cached value for ``key``, or None when absent or expired."""
        entry = self._data.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= time.time():
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value: str, *, ttl: int = DEFAULT_TTL_SECONDS) -> None:
        """Store ``value`` under ``key``, evicting the oldest entry past the cap."""
        self._data.pop(key, None)
        self._data[key] = (time.time() + ttl, value)
        while len(self._data) > self._max_entries:
            self._data.pop(next(iter(self._data)))


class RedisWebSearchCache:
    """Redis/Memurai-backed cache with a sorted-set index enforcing the entry cap."""

    def __init__(self, client: Any, *, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:  # noqa: ANN401
        """Wrap a redis client exposing sync ``get``/``setex``/``zadd``/``zcard``/``zrange``.

        Args:
            client: A redis-py-compatible sync client (Memurai speaks the same protocol).
            max_entries: Cap on cached searches; the oldest are trimmed past it.
        """
        self._client = client
        self._max_entries = max(1, max_entries)

    def get(self, key: str) -> str | None:
        """Return the cached value for ``key`` or ``None`` (Redis expires it for us)."""
        value = self._client.get(key)
        return value.decode() if isinstance(value, bytes) else value

    def set(self, key: str, value: str, *, ttl: int = DEFAULT_TTL_SECONDS) -> None:
        """Store ``value`` with a TTL and trim the index back to the cap."""
        self._client.setex(key, ttl, value)
        self._client.zadd(INDEX_KEY, {key: time.time()})
        self._trim()

    def _trim(self) -> None:
        """Evict the oldest keys until the index is back within ``max_entries``."""
        overflow = int(self._client.zcard(INDEX_KEY)) - self._max_entries
        if overflow <= 0:
            return
        stale = self._client.zrange(INDEX_KEY, 0, overflow - 1)
        if not stale:
            return
        keys = [k.decode() if isinstance(k, bytes) else k for k in stale]
        self._client.delete(*keys)
        self._client.zrem(INDEX_KEY, *keys)


def make_web_search_cache(
    mode: AegisMode,
    *,
    redis_client: Any | None = None,  # noqa: ANN401
    max_entries: int = DEFAULT_MAX_ENTRIES,
) -> WebSearchCache:
    """Select the cache backend by explicit mode, never by accident.

    Args:
        mode: The resolved :class:`~aegis.core.config.AegisMode`.
        redis_client: A redis/Memurai client. Required in ``full`` mode.
        max_entries: Cap on cached searches.

    Returns:
        The chosen :class:`WebSearchCache`.

    Raises:
        RuntimeError: if ``mode`` is ``full`` and no ``redis_client`` is supplied.
    """
    if mode is AegisMode.full:
        if redis_client is None:
            raise RuntimeError(
                "AEGIS_MODE=full requires a Redis/Memurai client for the web-search "
                "cache. Provide one, or set AEGIS_MODE=lite for an in-memory cache."
            )
        logger.info("WebSearchCache: Redis-backed (durable, capped at %d).", max_entries)
        return RedisWebSearchCache(redis_client, max_entries=max_entries)
    logger.warning(
        "WebSearchCache: in-memory selected (AEGIS_MODE=%s, non-durable, capped at %d).",
        mode.value,
        max_entries,
    )
    return InMemoryWebSearchCache(max_entries=max_entries)
