"""Two-tier semantic cache in front of retrieval (Redis).

`docs/architecture/backend.md` §4: "Semantic cache in front — exact-match tier first,
semantic tier on
top; hit returns instantly, miss runs retrieval then writes back with TTL." This is a
first-class part of the cost story surfaced on the metrics dashboard (cache-hit rate).

Tiers, cheapest first:

1. **Exact** — a deterministic key (sha256 of the :class:`~aegis.retrieval.types.RetrievalScope`
   partition + the normalised query). One `GET`.
2. **Semantic (near-exact)** — cosine nearest-neighbour over indexed query embeddings;
   a hit at `cosine ≥ threshold` returns the stored result. The threshold
   is deliberately high (near-identity, ≥ 0.985; §4.3, decision D4) so the cache is a
   conservative front layer, not a broad quality shortcut: below it, the caller runs full
   retrieval and treats the match only as a prefetch hint.

**Every tier is partitioned by scope, never filtered by it.** Both the exact key and the
semantic tier's *index* key carry the scope digest, so a lookup for tenant A can only
ever load tenant A's entries — the candidate set is scoped before any comparison
happens. That distinction is the whole point: a cosine search over a shared index that
discards foreign matches afterwards has still read another tenant's stored passages into
this process, and one missing `continue` turns it back into a leak. Partitioning makes
the leak unreachable rather than merely unlikely. The stored scope is re-checked on the
way out purely as a corruption tripwire, and it *raises* rather than skipping quietly.

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
from aegis.retrieval.types import RetrievalOrigin, RetrievalScope
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

    def _partition(self, scope: RetrievalScope) -> str:
        """Return the keyspace segment that isolates ``scope``'s entries.

        A digest of the scope's canonical
        :meth:`~aegis.retrieval.types.RetrievalScope.partition_key` rather than the key
        itself: the persona is caller-supplied text and would otherwise put arbitrary
        bytes (including the NUL separators) into a Redis key. The digest is truncated
        only for key length — 128 bits of a SHA-256 leaves no realistic collision risk,
        and any collision is caught by the stored-scope tripwire on read.
        """
        return hashlib.sha256(scope.partition_key().encode()).hexdigest()[:32]

    def _index_key(self, scope: RetrievalScope) -> str:
        """Return the semantic tier's index key **for this scope only**.

        One index set per scope is what makes the semantic tier a partition rather than a
        filter: the nearest-neighbour scan iterates a set that structurally cannot
        contain another tenant's entry.
        """
        return f"{self._ns}:{self._partition(scope)}:index"

    def _entry_key(self, query: str, scope: RetrievalScope) -> str:
        """Return the deterministic exact-match key for a query within ``scope``.

        The scope is folded into the digest *and* carried as a key segment: the digest is
        what makes two tenants asking the identical question miss each other, and the
        segment keeps the partition legible in the keyspace (and shared with the semantic
        index).
        """
        digest = hashlib.sha256(
            f"{scope.partition_key()}\x00{_normalise(query)}".encode()
        ).hexdigest()
        return f"{self._ns}:{self._partition(scope)}:e:{digest}"

    @staticmethod
    def _verify_scope(entry: dict, scope: RetrievalScope, key: str) -> None:
        """Fail loud if an entry found in ``scope``'s partition does not belong to it.

        Reaching an entry whose stored scope differs from the one asked for means the
        keyspace is corrupt (a digest collision, or a writer using a different key
        format). Serving it would be exactly the cross-tenant hit this partitioning
        exists to prevent, and quietly skipping it would hide a broken cache, so this
        raises. There is no correct silent branch here.

        Args:
            entry: The decoded cache entry.
            scope: The scope the lookup was made under.
            key: The Redis key the entry came from, for the error message.

        Raises:
            RuntimeError: If the entry was written under a different scope.
        """
        stored = entry.get("scope")
        if stored != scope.partition_key():
            raise RuntimeError(
                f"retrieval cache entry {key!r} is in the partition for scope "
                f"{scope!r} but was written under a different scope; refusing to serve it"
            )

    async def get_exact(
        self, query: str, scope: RetrievalScope
    ) -> RetrievalResult | None:
        """Return a cached result for an exact query match within ``scope``, if present.

        Args:
            query: The user query.
            scope: The tenant/persona/corpus-version partition to look in. Entries
                written under any other scope are unreachable from here.

        Returns:
            The stored `RetrievalResult` (with `cache_hit=True`) or None on a miss.

        Raises:
            RuntimeError: If the stored entry does not carry ``scope`` (a corrupt
                keyspace — see :meth:`_verify_scope`).
        """
        key = self._entry_key(query, scope)
        raw = await self._client.get(key)
        if raw is None:
            return None
        entry = json.loads(raw)
        self._verify_scope(entry, scope, key)
        return self._load_result(entry, kind=_KIND_EXACT)

    async def get_semantic(
        self, embedding: list[float], scope: RetrievalScope
    ) -> RetrievalResult | None:
        """Return the nearest cached result within the similarity threshold.

        The scan runs over ``scope``'s **own** index set, so no other tenant's embedding
        is ever compared against, let alone loaded.

        Args:
            embedding: The query embedding to match against indexed entries.
            scope: The tenant/persona/corpus-version partition to search.

        Returns:
            The best-matching stored `RetrievalResult` (`cache_hit=True`, tagged
            `cache-near`) or None when nothing clears the near-exact threshold — in which
            case the caller runs full retrieval (the match is only a prefetch hint).

        Raises:
            RuntimeError: If an indexed entry does not carry ``scope`` (a corrupt
                keyspace — see :meth:`_verify_scope`).
        """
        members = await self._client.smembers(self._index_key(scope))
        best_entry: dict | None = None
        best_score = self._threshold
        for key in members:
            raw = await self._client.get(key)
            if raw is None:
                continue  # entry expired; index membership is best-effort
            entry = json.loads(raw)
            self._verify_scope(entry, scope, key)
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
        scope: RetrievalScope,
        embedding: list[float],
        result: RetrievalResult,
    ) -> None:
        """Write a result into ``scope``'s partition of both tiers, with the configured TTL.

        Args:
            query: The user query.
            scope: The partition to write into (tenant + persona + corpus version).
            embedding: The query embedding, stored for the semantic tier.
            result: The retrieval result to cache.
        """
        key = self._entry_key(query, scope)
        entry = {
            # The scope this entry was written under, re-checked on every read as a
            # corruption tripwire (it is never used to *select* an entry — selection is
            # already partitioned by key).
            "scope": scope.partition_key(),
            "persona": scope.persona,
            "query": query,
            "cached_at": datetime.now(UTC).isoformat(),
            "embedding": embedding,
            "result": result.model_dump(mode="json"),
        }
        await self._client.set(key, json.dumps(entry), ex=self._ttl)
        await self._client.sadd(self._index_key(scope), key)

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
