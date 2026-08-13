"""Two-tier semantic cache in front of retrieval (Redis).

`docs/architecture/backend.md` §4: "Semantic cache in front — exact-match tier first,
semantic tier on
top; hit returns instantly, miss runs retrieval then writes back with TTL." This is a
first-class part of the cost story surfaced on the metrics dashboard (cache-hit rate).

Tiers, cheapest first:

1. **Exact** — a deterministic key (sha256 of normalised query + persona). One `GET`.
2. **Semantic (near-exact)** — cosine nearest-neighbour over indexed query embeddings;
   a hit at `cosine ≥ threshold` (same persona) returns the stored result. The threshold
   is deliberately high (near-identity, ≥ 0.985; §4.3, decision D4) so the cache is a
   conservative front layer, not a broad quality shortcut: below it, the caller runs full
   retrieval and treats the match only as a prefetch hint.

Every hit carries **honest provenance** — the original query, the write timestamp, and a
`cache-exact` / `cache-near` kind — so the UI/audit can show "answered from cache of query
X at T" and never launders a stale answer. The stored result's own origins + RRF fusion
are preserved; `cache` is added on top.

No RediSearch/vector-index module is required, keeping the deployment portable
(local Redis / Memurai on Windows). The Redis client is injected so unit tests run
against a fake with no network.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Protocol

from aegis.retrieval.models import CacheProvenance, RetrievalResult
from aegis.retrieval.types import RetrievalOrigin
from aegis.retrieval.vectors import cosine_similarity

_WHITESPACE = re.compile(r"\s+")

#: Cache-provenance `kind` values (mirror `ProvenanceEvent.cache_kind` vocabulary).
_KIND_EXACT = "cache-exact"
_KIND_NEAR = "cache-near"


class RedisLike(Protocol):
    """The async Redis surface the cache uses (a subset of `redis.asyncio.Redis`)."""

    async def get(self, key: str) -> str | None:
        """Return the value at `key`, or None."""
        ...

    async def set(self, key: str, value: str, *, ex: int | None = None) -> object:
        """Set `key` to `value` with optional TTL `ex` (seconds)."""
        ...

    async def sadd(self, key: str, *values: str) -> object:
        """Add members to the set at `key`."""
        ...

    async def smembers(self, key: str) -> set[str]:
        """Return all members of the set at `key`."""
        ...


def _normalise(query: str) -> str:
    """Collapse whitespace and lowercase a query for stable cache keying."""
    return _WHITESPACE.sub(" ", query).strip().lower()


class SemanticCache:
    """Exact + embedding-nearest-neighbour cache over a Redis-like store."""

    def __init__(
        self,
        client: RedisLike,
        *,
        ttl_seconds: int = 3600,
        similarity_threshold: float = 0.985,
        namespace: str = "retr:cache",
    ) -> None:
        """Initialise the cache.

        Args:
            client: An async Redis-like client (injected for testability).
            ttl_seconds: Time-to-live written on every cached entry.
            similarity_threshold: Minimum cosine similarity for a semantic hit.
            namespace: Key prefix isolating this cache's keys.
        """
        self._client = client
        self._ttl = ttl_seconds
        self._threshold = similarity_threshold
        self._ns = namespace
        self._index_key = f"{namespace}:index"

    def _entry_key(self, query: str, persona: str | None) -> str:
        """Return the deterministic exact-match key for a query+persona."""
        digest = hashlib.sha256(f"{persona or ''}\x00{_normalise(query)}".encode()).hexdigest()
        return f"{self._ns}:e:{digest}"

    async def get_exact(self, query: str, persona: str | None) -> RetrievalResult | None:
        """Return a cached result for an exact query+persona match, if present.

        Args:
            query: The user query.
            persona: The active persona (part of the key; scopes the cache).

        Returns:
            The stored `RetrievalResult` (with `cache_hit=True`) or None on a miss.
        """
        raw = await self._client.get(self._entry_key(query, persona))
        if raw is None:
            return None
        entry = json.loads(raw)
        return self._load_result(entry, kind=_KIND_EXACT)

    async def get_semantic(
        self, embedding: list[float], persona: str | None
    ) -> RetrievalResult | None:
        """Return the nearest cached result within the similarity threshold.

        Args:
            embedding: The query embedding to match against indexed entries.
            persona: The active persona; only same-persona entries are eligible.

        Returns:
            The best-matching stored `RetrievalResult` (`cache_hit=True`, tagged
            `cache-near`) or None when nothing clears the near-exact threshold — in which
            case the caller runs full retrieval (the match is only a prefetch hint).
        """
        members = await self._client.smembers(self._index_key)
        best_entry: dict | None = None
        best_score = self._threshold
        for key in members:
            raw = await self._client.get(key)
            if raw is None:
                continue  # entry expired; index membership is best-effort
            entry = json.loads(raw)
            if entry.get("persona") != persona:
                continue
            score = cosine_similarity(embedding, entry.get("embedding", []))
            if score >= best_score:
                best_score = score
                best_entry = entry
        if best_entry is None:
            return None
        return self._load_result(best_entry, kind=_KIND_NEAR)

    async def set(
        self,
        query: str,
        persona: str | None,
        embedding: list[float],
        result: RetrievalResult,
    ) -> None:
        """Write a result to both tiers with the configured TTL.

        Args:
            query: The user query.
            persona: The active persona (scopes exact + semantic tiers).
            embedding: The query embedding, stored for the semantic tier.
            result: The retrieval result to cache.
        """
        key = self._entry_key(query, persona)
        entry = {
            "persona": persona,
            "query": query,
            "cached_at": datetime.now(UTC).isoformat(),
            "embedding": embedding,
            "result": result.model_dump(mode="json"),
        }
        await self._client.set(key, json.dumps(entry), ex=self._ttl)
        await self._client.sadd(self._index_key, key)

    @staticmethod
    def _load_result(entry: dict, *, kind: str) -> RetrievalResult:
        """Rehydrate a stored entry into a cache-hit `RetrievalResult` with provenance.

        Preserves the stored result's own origins and fusion method and layers honest
        cache lineage on top: `cache` provenance (kind + original query + timestamp) and
        a leading ``cache`` origin, so the UI can show "served from cache" without hiding
        where the answer was originally fused from.

        Args:
            entry: The stored cache entry (query, cached_at, embedding, result).
            kind: The cache-hit kind (``cache-exact`` or ``cache-near``).
        """
        result = RetrievalResult.model_validate(entry["result"])
        prov = result.provenance
        origins = [
            RetrievalOrigin.CACHE,
            *(o for o in prov.origins if o != RetrievalOrigin.CACHE),
        ]
        cache_prov = CacheProvenance(
            kind=kind,
            original_query=entry.get("query"),
            cached_at=entry.get("cached_at"),
        )
        new_prov = prov.model_copy(update={"origins": origins, "cache": cache_prov})
        return result.model_copy(update={"cache_hit": True, "provenance": new_prov})

    @classmethod
    def from_url(cls, url: str, **kwargs: object) -> SemanticCache:
        """Build a cache backed by a real Redis connection (lazy import).

        Args:
            url: A `redis://` connection URL (e.g. `settings.redis_url`).
            **kwargs: Forwarded to `SemanticCache.__init__` (ttl, threshold, namespace).

        Returns:
            A `SemanticCache` over an async `redis.asyncio` client.
        """
        import redis.asyncio as redis  # lazy: keeps unit tests infra-free

        client = redis.from_url(url, decode_responses=True)
        return cls(client, **kwargs)  # type: ignore[arg-type]
