"""Answer-level semantic cache (skip the generation call, not the retrieval call).

This is the Bifrost/Portkey **semantic caching** value made native: it caches the
*final generated answer* so a semantically-equivalent question returns instantly
without paying for the expensive generation (LLM) call. It sits at a different layer
than :class:`app.retrieval.cache.SemanticCache`, which caches *retrieval* results —
this one caches the answer the model produced on top of them.

Design (mirrors the proven, RediSearch-free pattern in ``cache.py`` so it stays
portable — local Redis / Memurai, no vector-index module):

* **Scope partitioning** — ``scope`` is an opaque caller-supplied string (the
  orchestrator builds it from tenant + persona + role). Every entry is indexed under a
  **per-scope** Redis SET (``{namespace}:idx:{scope}``) and the scope is folded into
  each entry's key, so a hit under scope A can **never** be returned for scope B. This
  is a correctness + security requirement, not an optimisation: answers must never
  cross tenants/roles.
* **Semantic tier** — ``get`` receives only an embedding + scope, so it does a cosine
  nearest-neighbour search over the scope's index and returns the best entry at
  ``cosine ≥ similarity_threshold``. A query is always ≥ threshold similar to itself, so
  this also covers the exact-repeat case without needing the raw query at read time.
* **Honest misses** — no client, an empty index, an expired entry, or nothing clearing
  the threshold all return ``None``. A cache miss never raises.

The Redis client is injected (``RedisLike``) so unit tests run against an in-memory
fake with no network.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from app.retrieval.cache import RedisLike
from app.retrieval.vectors import cosine_similarity

_WHITESPACE = re.compile(r"\s+")


def _normalise(query: str) -> str:
    """Collapse whitespace and lowercase a query for stable, exact-match keying."""
    return _WHITESPACE.sub(" ", query).strip().lower()


@dataclass(frozen=True)
class AnswerCacheHit:
    """A served-from-cache answer with honest provenance.

    Attributes:
        answer: The cached generated answer.
        query: The original query the answer was generated for.
        cached_at: ISO8601 timestamp of when the answer was written.
        similarity: Cosine similarity between the asked and stored query embeddings.
        sources: Citation payloads carried alongside the answer (may be empty).
    """

    answer: str
    query: str
    cached_at: str
    similarity: float
    sources: list[dict]


class AnswerCache:
    """Cosine nearest-neighbour cache of final answers, partitioned per ``scope``."""

    def __init__(
        self,
        client: RedisLike,
        *,
        ttl_seconds: int = 1800,
        similarity_threshold: float = 0.97,
        namespace: str = "ans:cache",
    ) -> None:
        """Initialise the answer cache.

        Args:
            client: An async Redis-like client (injected for testability). ``None`` is
                tolerated — the cache then degrades to always-miss.
            ttl_seconds: Time-to-live written on every cached answer.
            similarity_threshold: Minimum cosine similarity for a semantic hit.
            namespace: Key prefix isolating this cache's keys.
        """
        self._client = client
        self._ttl = ttl_seconds
        self._threshold = similarity_threshold
        self._ns = namespace

    def _index_key(self, scope: str) -> str:
        """Return the per-scope index SET key (partitions entries by scope)."""
        digest = hashlib.sha256(scope.encode()).hexdigest()
        return f"{self._ns}:idx:{digest}"

    def _entry_key(self, scope: str, query: str) -> str:
        """Return the deterministic entry key for a ``scope`` + normalised ``query``.

        The scope is folded into the digest so the same query under two scopes never
        collides on one key, and re-setting the same query in a scope overwrites in place.
        """
        digest = hashlib.sha256(f"{scope}\x00{_normalise(query)}".encode()).hexdigest()
        return f"{self._ns}:e:{digest}"

    async def get(self, embedding: list[float], *, scope: str) -> AnswerCacheHit | None:
        """Return the nearest cached answer within the threshold for ``scope``.

        Only entries indexed under ``scope`` are eligible, so an answer stored under a
        different scope is never returned. Returns ``None`` on no client, an empty index,
        or nothing clearing the similarity threshold — never raises on a miss.

        Args:
            embedding: The query embedding to match against indexed answers.
            scope: The opaque partition key (tenant + persona + role, etc.).

        Returns:
            The best-matching :class:`AnswerCacheHit`, or ``None`` on a miss.
        """
        if self._client is None:
            return None
        members = await self._client.smembers(self._index_key(scope))
        best_entry: dict | None = None
        best_score = self._threshold
        for key in members:
            raw = await self._client.get(key)
            if raw is None:
                continue  # entry expired; index membership is best-effort
            entry = json.loads(raw)
            if entry.get("scope") != scope:
                continue  # defence in depth: never cross scopes even on a key collision
            score = cosine_similarity(embedding, entry.get("embedding", []))
            if score >= best_score:
                best_score = score
                best_entry = entry
        if best_entry is None:
            return None
        return AnswerCacheHit(
            answer=best_entry.get("answer", ""),
            query=best_entry.get("query", ""),
            cached_at=best_entry.get("cached_at", ""),
            similarity=best_score,
            sources=best_entry.get("sources", []) or [],
        )

    async def set(
        self,
        *,
        query: str,
        embedding: list[float],
        answer: str,
        scope: str,
        sources: list[dict] | None = None,
    ) -> None:
        """Write a generated answer to the cache under ``scope`` with the configured TTL.

        Args:
            query: The user query the answer was generated for (stored for provenance).
            embedding: The query embedding, stored for the semantic tier.
            answer: The generated answer to cache.
            scope: The opaque partition key; the entry is only ever visible under it.
            sources: Optional citation payloads to carry alongside the answer.
        """
        if self._client is None:
            return
        key = self._entry_key(scope, query)
        entry = {
            "scope": scope,
            "query": query,
            "cached_at": datetime.now(UTC).isoformat(),
            "embedding": embedding,
            "answer": answer,
            "sources": sources or [],
        }
        await self._client.set(key, json.dumps(entry), ex=self._ttl)
        await self._client.sadd(self._index_key(scope), key)

    @classmethod
    def from_url(cls, url: str, **kwargs: object) -> AnswerCache:
        """Build an answer cache backed by a real Redis connection (lazy import).

        Args:
            url: A ``redis://`` connection URL (e.g. ``settings.redis_url``).
            **kwargs: Forwarded to :meth:`__init__` (ttl, threshold, namespace).

        Returns:
            An :class:`AnswerCache` over an async ``redis.asyncio`` client.
        """
        import redis.asyncio as redis  # lazy: keeps unit tests infra-free

        client = redis.from_url(url, decode_responses=True)
        return cls(client, **kwargs)  # type: ignore[arg-type]
