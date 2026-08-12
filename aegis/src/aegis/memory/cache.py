"""SOTA semantic cache for memory recall — RedisVL in production, explicit fallback offline.

The expensive part of a turn's READ path is recall + assembly (Qdrant ANN over facts and
episodic turns, RRF fusion, budgeted lost-in-the-middle layout). When the *same* subject
asks a semantically-equivalent question, that whole result can be served from a cache
instead of recomputed. This module is that cache.

Two backends behind one wrapper (:class:`MemorySemanticCache`):

* **Production — RedisVL** (:class:`redisvl.extensions.cache.llm.SemanticCache`, the
  industry-standard semantic cache): a real RediSearch vector index over cached
  ``(subject, query)`` entries, vectorized by the injected embedder, with a genuine
  ``distance_threshold`` (cosine) and per-entry ``TTL``. In full mode a real Redis-Stack
  is **required** — construction fails loud if it is unreachable, exactly like Qdrant /
  Postgres. RedisVL needs the RediSearch module, so it cannot run in an offline unit test.

* **Offline — explicit in-memory fallback** (:class:`_InMemoryBackend`): a *labeled*,
  non-silent degrade for dev/tests. It implements the SAME semantics — real TTL expiry,
  cosine similarity threshold, subject/tenant scoping, and a ``max_entries`` eviction
  knob — in pure Python (no RediSearch), so the cache behaviour is fully testable offline
  without ever faking RedisVL. Every hit/entry is stamped ``backend="in-memory"`` so a
  caller/UI can see it is not the production path.

**Data consistency.** The durable SQL rows are authoritative; this cache is derived. Any
write to a subject's facts (consolidation, forget/delete, prune) MUST call
:meth:`MemorySemanticCache.invalidate` for that subject so the next recall recomputes.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from aegis.memory.config import MemoryConfig
from aegis.retrieval.vectors import cosine_similarity

#: Async batched embedder (the same seam consolidation/recall inject: gateway or offline).
EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]

_WHITESPACE = re.compile(r"\s+")


def _normalise(query: str) -> str:
    """Collapse whitespace + lowercase for a stable exact-key (the redis backend keys too)."""
    return _WHITESPACE.sub(" ", query).strip().lower()

#: Backend labels — surfaced on every hit so a caller never confuses the two paths.
BACKEND_REDIS = "redisvl"
BACKEND_MEMORY = "in-memory"


@dataclass(frozen=True)
class MemoryCacheHit:
    """A recall result served from the semantic cache, with honest provenance.

    Attributes:
        value: The cached, JSON-round-tripped recall/assembly payload (opaque to the cache).
        query: The original query the entry was stored under.
        cached_at: ISO8601 timestamp of when the entry was written.
        similarity: Cosine similarity between the asked and stored query embeddings.
        subject_id: The subject the entry belongs to (scoping proof).
        tenant_id: The tenant the entry belongs to, or ``None``.
        backend: ``"redisvl"`` (production) or ``"in-memory"`` (explicit fallback).
    """

    value: dict[str, Any]
    query: str
    cached_at: str
    similarity: float
    subject_id: str
    tenant_id: int | None
    backend: str


def _scope_tag(tenant_id: int | None) -> str:
    """Normalise a tenant id to a stable tag string (``"-"`` for the null tenant)."""
    return "-" if tenant_id is None else str(tenant_id)


# --------------------------------------------------------------------------- backends


@dataclass
class _Entry:
    """One in-memory cache entry (the fallback backend's stored record)."""

    query: str
    embedding: list[float]
    value: dict[str, Any]
    cached_at: str
    subject_id: str
    tenant_id: int | None
    expires_at: float  # monotonic deadline; math.inf when ttl is disabled


class _InMemoryBackend:
    """Explicit, labeled in-memory semantic cache (no RediSearch) — dev/test fallback.

    Enforces the same contract as the RedisVL backend: real TTL expiry (via an injectable
    monotonic clock so tests can advance time), a cosine similarity threshold, strict
    subject+tenant scoping, and a ``max_entries`` size ceiling that evicts the oldest entry.
    """

    label = BACKEND_MEMORY
    is_redis = False

    def __init__(
        self,
        *,
        ttl_seconds: int,
        distance_threshold: float,
        max_entries: int,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._min_sim = 1.0 - distance_threshold
        self._max_entries = max_entries
        self._now = time_fn
        #: Insertion-ordered store keyed by ``(subject, tenant, normalised-query)``.
        self._entries: dict[tuple[str, str, str], _Entry] = {}
        self.evicted = 0  # cumulative TTL + size evictions (honest metric)

    def _sweep_expired(self) -> None:
        now = self._now()
        dead = [k for k, e in self._entries.items() if e.expires_at <= now]
        for k in dead:
            del self._entries[k]
            self.evicted += 1

    async def check(
        self, *, subject_id: str, query_vec: list[float], tenant_id: int | None
    ) -> MemoryCacheHit | None:
        self._sweep_expired()
        tag = _scope_tag(tenant_id)
        best: _Entry | None = None
        best_sim = self._min_sim
        for (subj, ttag, _), entry in self._entries.items():
            if subj != subject_id or ttag != tag:
                continue  # strict subject+tenant isolation — never cross scopes
            sim = cosine_similarity(query_vec, entry.embedding)
            if sim >= best_sim:
                best_sim = sim
                best = entry
        if best is None:
            return None
        return MemoryCacheHit(
            value=best.value,
            query=best.query,
            cached_at=best.cached_at,
            similarity=best_sim,
            subject_id=best.subject_id,
            tenant_id=best.tenant_id,
            backend=self.label,
        )

    async def store(
        self,
        *,
        subject_id: str,
        query: str,
        query_vec: list[float],
        value: dict[str, Any],
        cached_at: str,
        tenant_id: int | None,
    ) -> None:
        self._sweep_expired()
        key = (subject_id, _scope_tag(tenant_id), _normalise(query))
        deadline = self._now() + self._ttl if self._ttl > 0 else float("inf")
        # Re-insert at the end so recency ordering drives size eviction.
        self._entries.pop(key, None)
        self._entries[key] = _Entry(
            query=query,
            embedding=list(query_vec),
            value=value,
            cached_at=cached_at,
            subject_id=subject_id,
            tenant_id=tenant_id,
            expires_at=deadline,
        )
        while len(self._entries) > self._max_entries:
            self._entries.pop(next(iter(self._entries)))  # drop oldest
            self.evicted += 1

    async def invalidate(self, *, subject_id: str, tenant_id: int | None) -> int:
        tag = _scope_tag(tenant_id)
        victims = [
            k for k in self._entries if k[0] == subject_id and k[1] == tag
        ]
        for k in victims:
            del self._entries[k]
        return len(victims)

    async def clear(self) -> None:
        self._entries.clear()

    async def aclose(self) -> None:  # symmetry with the redis backend
        self._entries.clear()


class _RedisVLBackend:
    """Production backend over :class:`redisvl.extensions.cache.llm.SemanticCache`.

    Subject/tenant scoping uses RedisVL filterable tag fields, so a hit under one subject
    can never be returned for another. Vectors are supplied precomputed (recall already
    embeds the query); the injected embedder is wired as the index vectorizer so RedisVL
    knows the dimensionality and can embed on the rare path where no vector is passed.
    """

    label = BACKEND_REDIS
    is_redis = True

    def __init__(
        self,
        *,
        embedder: EmbedFn,
        redis_url: str,
        ttl_seconds: int,
        distance_threshold: float,
        dims: int,
        name: str = "aegis_memory_cache",
    ) -> None:
        # Lazy import: keeps the offline path (and non-redis installs) free of redisvl.
        from redisvl.extensions.cache.llm import SemanticCache
        from redisvl.query.filter import Tag
        from redisvl.utils.vectorize import CustomTextVectorizer

        self._Tag = Tag

        async def _aembed(text: str) -> list[float]:
            return (await embedder([text]))[0]

        async def _aembed_many(texts: list[str], **_: object) -> list[list[float]]:
            return await embedder(list(texts))

        # The sync ``embed`` is never used for real vectors (the wrapper always supplies a
        # precomputed vector, or embeds via the async seam before calling the backend), but
        # RedisVL validates it on construction and derives the index dims from it — so it
        # must return a correctly-sized, non-empty vector.
        vectorizer = CustomTextVectorizer(
            embed=lambda _text, _d=dims: [0.0] * _d,
            aembed=_aembed,
            aembed_many=_aembed_many,
            dtype="float32",
        )

        # Fail loud if Redis-Stack/RediSearch is unreachable — parity with Qdrant/Postgres.
        self._cache = SemanticCache(
            name=name,
            distance_threshold=distance_threshold,
            ttl=ttl_seconds or None,
            vectorizer=vectorizer,
            filterable_fields=[
                {"name": "subject_id", "type": "tag"},
                {"name": "tenant_id", "type": "tag"},
            ],
            redis_url=redis_url,
        )

    def _filter(self, subject_id: str, tenant_id: int | None) -> object:
        return (self._Tag("subject_id") == subject_id) & (
            self._Tag("tenant_id") == _scope_tag(tenant_id)
        )

    async def check(
        self, *, subject_id: str, query_vec: list[float], tenant_id: int | None
    ) -> MemoryCacheHit | None:
        hits = await self._cache.acheck(
            vector=query_vec,
            num_results=1,
            return_fields=["response", "prompt", "inserted_at"],
            filter_expression=self._filter(subject_id, tenant_id),
        )
        if not hits:
            return None
        hit = hits[0]
        value = json.loads(hit.get("response") or "{}")
        distance = float(hit.get("vector_distance", 0.0) or 0.0)
        return MemoryCacheHit(
            value=value,
            query=hit.get("prompt", ""),
            cached_at=str(hit.get("inserted_at", "")),
            similarity=1.0 - distance,
            subject_id=subject_id,
            tenant_id=tenant_id,
            backend=self.label,
        )

    async def store(
        self,
        *,
        subject_id: str,
        query: str,
        query_vec: list[float],
        value: dict[str, Any],
        cached_at: str,
        tenant_id: int | None,
    ) -> None:
        await self._cache.astore(
            prompt=query,
            response=json.dumps(value),
            vector=query_vec,
            filters={"subject_id": subject_id, "tenant_id": _scope_tag(tenant_id)},
        )

    async def invalidate(self, *, subject_id: str, tenant_id: int | None) -> int:
        # No native drop-by-filter: enumerate the subject's entries (distance_threshold=1.0
        # matches all), then drop their keys. Bounded by a large num_results.
        hits = await self._cache.acheck(
            vector=[0.0],
            num_results=10_000,
            return_fields=["key"],
            filter_expression=self._filter(subject_id, tenant_id),
            distance_threshold=1.0,
        )
        keys = [h["key"] for h in hits if h.get("key")]
        if keys:
            await self._cache.adrop(keys=keys)
        return len(keys)

    async def clear(self) -> None:
        await self._cache.aclear()

    async def aclose(self) -> None:
        await self._cache.adisconnect()


# --------------------------------------------------------------------------- wrapper


class MemorySemanticCache:
    """Semantic cache over expensive recall/assembly, keyed by ``(subject, query)``.

    Dispatches to a RedisVL backend (production) or the explicit in-memory fallback
    (offline). Construct via :meth:`from_config` (picks by ``redis_url`` + ``require_redis``)
    or the explicit :meth:`in_memory` / :meth:`redis` constructors.
    """

    def __init__(
        self, backend: _InMemoryBackend | _RedisVLBackend, *, embedder: EmbedFn
    ) -> None:
        """Wrap an already-built backend. Prefer the classmethod constructors."""
        self._backend = backend
        self._embedder = embedder

    # -- constructors ------------------------------------------------------------------

    @classmethod
    def in_memory(
        cls,
        config: MemoryConfig,
        *,
        embedder: EmbedFn,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> MemorySemanticCache:
        """Build the explicit, labeled in-memory fallback (dev/offline/tests).

        Args:
            config: A :class:`~aegis.memory.config.MemoryConfig` (reads the ``cache_*`` knobs).
            embedder: Async batched embedder used when a query vector is not supplied.
            time_fn: Monotonic clock (injectable so tests can drive TTL expiry).
        """
        backend = _InMemoryBackend(
            ttl_seconds=config.cache_ttl_seconds,
            distance_threshold=config.cache_distance_threshold,
            max_entries=config.cache_max_entries,
            time_fn=time_fn,
        )
        return cls(backend, embedder=embedder)

    @classmethod
    def redis(
        cls,
        config: MemoryConfig,
        *,
        embedder: EmbedFn,
        redis_url: str,
        dims: int,
        name: str = "aegis_memory_cache",
    ) -> MemorySemanticCache:
        """Build the production RedisVL-backed cache (fails loud if Redis is unreachable)."""
        backend = _RedisVLBackend(
            embedder=embedder,
            redis_url=redis_url,
            ttl_seconds=config.cache_ttl_seconds,
            distance_threshold=config.cache_distance_threshold,
            dims=dims,
            name=name,
        )
        return cls(backend, embedder=embedder)

    @classmethod
    def from_config(
        cls,
        config: MemoryConfig,
        *,
        embedder: EmbedFn,
        redis_url: str | None = None,
        require_redis: bool = False,
        dims: int,
        name: str = "aegis_memory_cache",
    ) -> MemorySemanticCache:
        """Pick the backend from deployment context.

        * ``require_redis=True`` (full mode): a real Redis is **required** — with no
          ``redis_url`` this raises; with one it builds the RedisVL backend and lets any
          connection error propagate (fail loud, like Qdrant/Postgres).
        * otherwise: if a ``redis_url`` is given, use RedisVL; else the explicit in-memory
          fallback (a labeled, non-silent degrade).
        """
        if require_redis:
            if not redis_url:
                msg = (
                    "MemorySemanticCache: full mode requires a Redis URL for the semantic "
                    "cache (like Qdrant/Postgres); none was provided."
                )
                raise RuntimeError(msg)
            return cls.redis(
                config, embedder=embedder, redis_url=redis_url, dims=dims, name=name
            )
        if redis_url:
            return cls.redis(
                config, embedder=embedder, redis_url=redis_url, dims=dims, name=name
            )
        return cls.in_memory(config, embedder=embedder)

    # -- properties --------------------------------------------------------------------

    @property
    def backend_label(self) -> str:
        """``"redisvl"`` (production) or ``"in-memory"`` (explicit fallback)."""
        return self._backend.label

    @property
    def is_redis(self) -> bool:
        """Whether this cache is backed by the production RedisVL path."""
        return self._backend.is_redis

    # -- operations --------------------------------------------------------------------

    async def _vector_for(self, query: str, query_vec: list[float] | None) -> list[float]:
        if query_vec is not None:
            return query_vec
        return (await self._embedder([query]))[0]

    async def check(
        self,
        *,
        subject_id: str,
        query: str,
        query_vec: list[float] | None = None,
        tenant_id: int | None = None,
    ) -> MemoryCacheHit | None:
        """Return a cached recall result for ``(subject, ~query)`` within threshold, or None.

        Never raises on a miss. Embeds ``query`` itself only when ``query_vec`` is absent.
        """
        vec = await self._vector_for(query, query_vec)
        return await self._backend.check(
            subject_id=subject_id, query_vec=vec, tenant_id=tenant_id
        )

    async def store(
        self,
        *,
        subject_id: str,
        query: str,
        value: dict[str, Any],
        query_vec: list[float] | None = None,
        tenant_id: int | None = None,
        cached_at: str | None = None,
    ) -> None:
        """Write a recall result under ``(subject, query)`` with the configured TTL."""
        vec = await self._vector_for(query, query_vec)
        await self._backend.store(
            subject_id=subject_id,
            query=query,
            query_vec=vec,
            value=value,
            cached_at=cached_at or datetime.now(UTC).isoformat(),
            tenant_id=tenant_id,
        )

    async def invalidate(self, *, subject_id: str, tenant_id: int | None = None) -> int:
        """Drop every cached entry for a subject (call on any write to that subject).

        Returns the number of entries evicted (0 if none).
        """
        return await self._backend.invalidate(subject_id=subject_id, tenant_id=tenant_id)

    async def clear(self) -> None:
        """Drop all entries (test/maintenance)."""
        await self._backend.clear()

    async def aclose(self) -> None:
        """Release backend resources (Redis connection / in-memory store)."""
        await self._backend.aclose()


__all__ = [
    "BACKEND_MEMORY",
    "BACKEND_REDIS",
    "EmbedFn",
    "MemoryCacheHit",
    "MemorySemanticCache",
]
