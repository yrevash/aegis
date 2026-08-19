"""The web-search cache — Memurai/Redis in full mode, in-memory only when said so.

A rehearsed demo query on a warm cache costs zero provider calls, which is what the
phase-05 budget arithmetic assumes, and it is also the rate-limit and
conference-wifi insurance: the second run of a query that already worked cannot be
broken by the network.

Three properties are deliberate and all three are about not lying:

* **Backend choice is explicit.** ``full`` mode requires a real client and raises
  without one, exactly like :mod:`aegis.guardrails.cache`. There is no
  ``except -> in-memory`` path here; a caller that wants the degradation must ask
  for it by mode.
* **The cache is capped.** Both backends bound their entry count (plan 02's R8 —
  Memurai memory pressure on a 16 GB box is a real failure mode, and an unbounded
  cache of arbitrary web content is the fastest way to reach it). The Redis backend
  keeps a sorted-set index of its own keys and trims the oldest past the cap, so the
  bound holds across processes rather than only inside one.
* **The value holds public web content and nothing else.** The cache is shared by
  every tenant, so what goes into it has to be shareable. :class:`CachedWebResults`
  is the whole payload: the provider's raw hits. Not the query that found them, and
  not the guardrail verdict that any one tenant's rails reached over them — see its
  docstring for why each of those used to be there and what it cost.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any, Protocol

from pydantic import BaseModel

from aegis.core.cache_stats import (
    CACHE_WEB_SEARCH,
    note_size,
    record_eviction,
    record_hit,
    record_miss,
    record_store,
    register_cache,
)
from aegis.core.config import AegisMode
from aegis.websearch.types import WebSearchResult

logger = logging.getLogger(__name__)

#: Default time-to-live for a cached search, in seconds. An hour: long enough that a
#: rehearsal and the demo itself share one provider call, short enough that "what
#: happened today" is not answered from yesterday.
DEFAULT_TTL_SECONDS = 3600

#: Default cap on cached searches. Web content is large; this is the R8 bound.
DEFAULT_MAX_ENTRIES = 512

#: Redis key prefix, carrying a schema version. Bump it when the cached payload shape
#: changes, so an old entry can never be rehydrated as a new one. ``v1`` held a whole
#: screened :class:`~aegis.websearch.types.WebSearchResponse` — raw query text and one
#: tenant's guardrail verdict included — so the bump to ``v2`` is not housekeeping: it
#: is what guarantees a ``v1`` entry written before this fix can never be read back.
KEY_PREFIX = "aegis:websearch:v2"

#: The sorted-set that indexes this cache's own keys so the cap can be enforced.
INDEX_KEY = f"{KEY_PREFIX}:index"

#: Index keys from superseded cache versions. Their *entries* expire on their own
#: TTL, but the sorted set holding them carries none — so bumping the prefix leaves
#: a set of stale members behind forever in any store that ran the old code. Small
#: and bounded, and still a key nobody owns; :meth:`RedisSearchCache.__init__`
#: deletes them once. Add the old prefix here whenever :data:`KEY_PREFIX` moves.
SUPERSEDED_INDEX_KEYS: tuple[str, ...] = ("aegis:websearch:v1:index",)

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

    There is deliberately **no tenant in the key**. A public web search is not tenant
    data, and one provider call answering every tenant who asks the same question is
    the point of the cache. What makes that safe is not the digest — it is that the
    *value* is :class:`CachedWebResults`, which carries no query text and no tenant's
    guardrail verdict. The digest is a one-way function of the query, so a cache dump
    is not a log of what anybody asked; it is not a secret, though. Anyone who can
    read the store can also hash a guessed query and see whether that entry exists,
    which is inherent to any shared cache and is why the value, not the key, is where
    the isolation has to hold.

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


class CachedWebResults(BaseModel):
    """Everything the shared cache is allowed to hold: the provider's raw hits.

    Two things used to ride along in the cached value and neither survived audit.

    The **query** was in it, because the value was a whole
    :class:`~aegis.websearch.types.WebSearchResponse` and that model carries
    ``query``. :func:`cache_key`'s docstring claimed the query was "hashed, never
    stored" while ``model_dump_json`` wrote it out verbatim, and :data:`INDEX_KEY`
    made every entry enumerable — so read access to Redis was read access to every
    tenant's questions, no guessing required.

    The **screening verdict** was in it too, deliberately: caching the *screened*
    response made a warm query cost zero classifier calls as well as zero provider
    calls. But ``guardrails.denylist.terms`` and ``guardrails.pii.entities`` are
    tenant-scoped, UNION-merged settings, so the rails that screened the first
    tenant's copy are not the next tenant's rails. Caching the verdict meant
    whichever tenant searched first decided what the other was allowed to see.

    Raw hits are neither of those things: they are public web pages, identical for
    everybody who asks, which is exactly what a tenant-less cache may share.
    """

    results: tuple[WebSearchResult, ...] = ()


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
        register_cache(
            CACHE_WEB_SEARCH,
            backend="in_memory",
            ttl_seconds=DEFAULT_TTL_SECONDS,
            capacity=self._max_entries,
        )
        note_size(CACHE_WEB_SEARCH, 0)

    def get(self, key: str) -> str | None:
        """Return the cached value for ``key``, or None when absent or expired.

        An expired entry is a **miss**, and the sweep that drops it is not an
        eviction: the cap did not force it out, its own TTL ran. Counting it as one
        would make the eviction figure mean two different things at once.
        """
        entry = self._data.get(key)
        if entry is None:
            record_miss(CACHE_WEB_SEARCH)
            return None
        expires_at, value = entry
        if expires_at <= time.time():
            self._data.pop(key, None)
            note_size(CACHE_WEB_SEARCH, len(self._data))
            record_miss(CACHE_WEB_SEARCH)
            return None
        record_hit(CACHE_WEB_SEARCH)
        return value

    def set(self, key: str, value: str, *, ttl: int = DEFAULT_TTL_SECONDS) -> None:
        """Store ``value`` under ``key``, evicting the oldest entry past the cap."""
        self._data.pop(key, None)
        self._data[key] = (time.time() + ttl, value)
        record_store(CACHE_WEB_SEARCH)
        while len(self._data) > self._max_entries:
            self._data.pop(next(iter(self._data)))
            record_eviction(CACHE_WEB_SEARCH)
        note_size(CACHE_WEB_SEARCH, len(self._data))


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
        register_cache(
            CACHE_WEB_SEARCH,
            backend="redis",
            ttl_seconds=DEFAULT_TTL_SECONDS,
            capacity=self._max_entries,
        )
        self._drop_superseded_indexes()

    def _drop_superseded_indexes(self) -> None:
        """Delete index sets left behind by an earlier :data:`KEY_PREFIX`.

        A cache-version bump retires the value keys safely — they expire on their own
        TTL and the new prefix cannot read them — but the index *set* has no TTL, so
        the old one would linger with stale members for as long as the store lives.
        Bounded and harmless, and still an orphan nobody owns.

        Best-effort by design: a store that refuses the delete is not a reason to fail
        constructing a cache, so the failure is logged and swallowed. It is also
        idempotent — a second construction deletes nothing and says nothing.
        """
        for stale in SUPERSEDED_INDEX_KEYS:
            try:
                if self._client.delete(stale):
                    logger.info("Dropped the superseded web-search index %s.", stale)
            except Exception:  # noqa: BLE001 - a cleanup must never block a cache
                logger.warning(
                    "Could not drop the superseded web-search index %s; it is bounded "
                    "and harmless, but it will linger.",
                    stale,
                    exc_info=True,
                )

    def get(self, key: str) -> str | None:
        """Return the cached value for ``key`` or ``None`` (Redis expires it for us).

        A TTL expiry arrives here as an absent key, so it is counted as a miss and
        never as an eviction — nothing in this process saw the store drop it.
        """
        value = self._client.get(key)
        decoded = value.decode() if isinstance(value, bytes) else value
        if decoded is None:
            record_miss(CACHE_WEB_SEARCH)
        else:
            record_hit(CACHE_WEB_SEARCH)
        return decoded

    def set(self, key: str, value: str, *, ttl: int = DEFAULT_TTL_SECONDS) -> None:
        """Store ``value`` with a TTL and trim the index back to the cap."""
        self._client.setex(key, ttl, value)
        self._client.zadd(INDEX_KEY, {key: time.time()})
        record_store(CACHE_WEB_SEARCH)
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
        # The cap forced these out; that is exactly what an eviction is, and it is the
        # one this repo can count — the index set is ours, so the trim is observable
        # where a store-side TTL expiry is not.
        record_eviction(CACHE_WEB_SEARCH, len(keys))


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
