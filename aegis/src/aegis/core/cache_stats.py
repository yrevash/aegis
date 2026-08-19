"""What the caches actually did in this process — counted at the point of truth.

**Why this exists.** Every cache in this repo decided hit-or-miss and then threw the
verdict away. The retrieval pipeline stamped ``cache_hit`` onto one result, the agent
stream emitted a per-run ``retrieval_cache`` event that died with the socket, and the
guardrail and web-search caches said nothing at all. So the only durable statement the
platform could make about caching was the shape of the code — which is how a cache page
ends up rendering a hand-written table of configuration under a heading that reads like a
measurement.

This module is the smallest thing that fixes that: a **counter next to each decision**.
:func:`record_hit` / :func:`record_miss` are called on the exact branch that returned a
cached value or did not, so a number on the page is the same event the cache acted on.
Nothing is inferred, nothing is sampled, and no cache changes behaviour because it is
being counted.

**Registration carries the live configuration, not the module default.** A cache calls
:func:`register_cache` from its constructor with the TTL, threshold and backend *that
instance* was built with. A page therefore reports what the running process chose, and a
cache class nobody constructed reports ``registered=False`` — "this process has not built
one" — rather than a plausible default nobody is using.

**Honest caveats, and they are on the record itself.** Like
:mod:`aegis.observability.latency`, this is a **per-process, in-RAM** tally that resets on
restart. It is not a metrics store, it is not merged across workers, and
:attr:`CacheReport.hit_rate` is ``None`` — never ``0.0`` — before the first lookup.
:attr:`CacheReport.evictions` is ``None`` for a cache that has no eviction this process
can count (a Redis TTL expiry happens in the store, with nobody here to see it), which is
a different fact from zero evictions and is reported as one.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace

__all__ = [
    "CACHE_ANSWER",
    "CACHE_INJECTION",
    "CACHE_KEYS",
    "CACHE_RETRIEVAL_EXACT",
    "CACHE_RETRIEVAL_SEMANTIC",
    "CACHE_WEB_SEARCH",
    "CacheReport",
    "CacheSpec",
    "cache_reports",
    "note_size",
    "record_eviction",
    "record_hit",
    "record_miss",
    "record_store",
    "register_cache",
    "reset_cache_stats",
    "spec_for",
]

#: The retrieval semantic cache's cheap tier: one deterministic ``GET`` on a sha256 of
#: the scope partition plus the normalised query.
CACHE_RETRIEVAL_EXACT = "retrieval_exact"

#: The retrieval semantic cache's second tier: cosine nearest-neighbour over the
#: scope's own index of stored query embeddings.
CACHE_RETRIEVAL_SEMANTIC = "retrieval_semantic"

#: The final-answer cache, keyed by embedding similarity within one opaque scope.
CACHE_ANSWER = "answer"

#: The prompt-injection classifier verdict cache.
CACHE_INJECTION = "injection"

#: The web-search provider cache — the only one here with a countable eviction.
CACHE_WEB_SEARCH = "web_search"


@dataclass(frozen=True, slots=True)
class CacheSpec:
    """The code facts about one cache: what it holds and how a hit is decided.

    Attributes:
        key: The stable identifier the counters are filed under.
        name: A human name for the surface.
        holds: What the cached *value* is.
        method: How a hit is decided — the thing a reader has to know to trust a hit
            rate at all, since an exact-hash cache and a cosine cache are not
            measuring the same event.
        evicts: Whether this cache performs an eviction **this process can count**. A
            store-side TTL expiry is not one: nothing in this process observes it, so a
            zero would be a claim nobody measured. See :attr:`CacheReport.evictions`.
    """

    key: str
    name: str
    holds: str
    method: str
    evicts: bool


#: One row per cache, declared here rather than in a UI so the surface cannot describe a
#: cache the code does not have. Keyed by the same constants the counters use.
CACHE_SPECS: dict[str, CacheSpec] = {
    CACHE_RETRIEVAL_EXACT: CacheSpec(
        key=CACHE_RETRIEVAL_EXACT,
        name="Retrieval — exact tier",
        holds="A whole RetrievalResult, in its scope's partition",
        method="sha256(scope partition + normalised query) — one GET, exact",
        evicts=False,
    ),
    CACHE_RETRIEVAL_SEMANTIC: CacheSpec(
        key=CACHE_RETRIEVAL_SEMANTIC,
        name="Retrieval — semantic tier",
        holds="The same entries, reached by embedding rather than by key",
        method="Cosine nearest neighbour over the scope's own index set",
        evicts=False,
    ),
    CACHE_ANSWER: CacheSpec(
        key=CACHE_ANSWER,
        name="Answer cache",
        holds="A generated answer and its citations, per scope",
        method="Cosine nearest neighbour over indexed query embeddings",
        evicts=False,
    ),
    CACHE_INJECTION: CacheSpec(
        key=CACHE_INJECTION,
        name="Injection verdicts",
        holds="One prompt-injection classifier verdict per text",
        method="sha256 of the redacted text — exact key, one stable verdict",
        evicts=False,
    ),
    CACHE_WEB_SEARCH: CacheSpec(
        key=CACHE_WEB_SEARCH,
        name="Web search",
        holds="A provider's raw public hits — no query text, no tenant verdict",
        method="sha256(provider + normalised query + max_results) — exact key",
        evicts=True,
    ),
}

#: The declared caches, in the order they sit on the read path.
CACHE_KEYS: tuple[str, ...] = tuple(CACHE_SPECS)


def spec_for(key: str) -> CacheSpec:
    """Return the :class:`CacheSpec` for ``key``.

    Raises:
        KeyError: If ``key`` is not a declared cache. A counter filed under an
            undeclared key is a typo, and failing here is how it gets found.
    """
    return CACHE_SPECS[key]


@dataclass(frozen=True, slots=True)
class CacheReport:
    """Everything this process can honestly say about one cache.

    Attributes:
        key: The cache's stable identifier; :func:`spec_for` gives its description.
        registered: Whether an instance of this cache was constructed in this process.
            ``False`` means the configuration fields below are unknown, **not** that
            they are the module defaults.
        backend: The backend the live instance chose (``"redis"`` / ``"in_memory"`` /
            ``"none"``), or ``None`` when nothing registered.
        ttl_seconds: The TTL that instance writes on entries, or ``None`` when the
            cache writes none (the injection cache) or nothing registered.
        threshold: The similarity a semantic hit must clear, or ``None`` for an
            exact-key cache.
        capacity: The instance's entry cap, or ``None`` when it has none.
        entries: The entry count last observed, for a backend that can be counted
            without a round trip. ``None`` for a store-side backend — asking Redis for
            its size on every page load is a cost the page has not earned.
        lookups: Reads that reached this cache.
        hits: Reads served from it.
        misses: Reads that fell through. ``hits + misses == lookups`` by construction.
        stores: Values written into it.
        evictions: Entries this cache dropped to stay inside its cap. ``None`` when
            :attr:`CacheSpec.evicts` is false — this cache performs no eviction this
            process can observe, which is not the same statement as zero.
    """

    key: str
    registered: bool = False
    backend: str | None = None
    ttl_seconds: int | None = None
    threshold: float | None = None
    capacity: int | None = None
    entries: int | None = None
    lookups: int = 0
    hits: int = 0
    misses: int = 0
    stores: int = 0
    evictions: int | None = None

    @property
    def hit_rate(self) -> float | None:
        """Hits over lookups, or ``None`` before the first lookup.

        ``None`` rather than ``0.0``: a cache nobody has read is not a cache that
        missed everything, and rendering the second is the fabricated-zero this
        module exists to avoid.
        """
        if self.lookups == 0:
            return None
        return self.hits / self.lookups


_lock = threading.Lock()
_reports: dict[str, CacheReport] = {}


def _mutate(key: str, **changes: object) -> None:
    """Apply ``changes`` to ``key``'s record under the lock, creating it if needed."""
    spec = spec_for(key)  # raises on an undeclared key, before any state is touched
    with _lock:
        current = _reports.get(key) or CacheReport(
            key=key, evictions=0 if spec.evicts else None
        )
        _reports[key] = replace(current, **changes)  # type: ignore[arg-type]


def register_cache(
    key: str,
    *,
    backend: str,
    ttl_seconds: int | None = None,
    threshold: float | None = None,
    capacity: int | None = None,
) -> None:
    """Record that an instance of ``key`` was constructed, and how.

    Called from the cache's own constructor (or its factory, where the factory is what
    chooses the backend), so the configuration on the page is the configuration the
    running instance holds. Counters already accumulated are **kept**: re-constructing a
    cache does not un-count the lookups the previous instance served.

    Args:
        key: One of the ``CACHE_*`` constants.
        backend: ``"redis"``, ``"in_memory"`` or ``"none"`` — the honest backend label,
            never a guess from configuration.
        ttl_seconds: The TTL written on entries, or ``None`` when the cache writes none.
        threshold: The similarity a semantic hit must clear, or ``None`` for exact keys.
        capacity: The entry cap, or ``None`` when uncapped.
    """
    _mutate(
        key,
        registered=True,
        backend=backend,
        ttl_seconds=ttl_seconds,
        threshold=threshold,
        capacity=capacity,
    )


def record_hit(key: str) -> None:
    """Count one read of ``key`` that was served from the cache."""
    spec = spec_for(key)
    with _lock:
        current = _reports.get(key) or CacheReport(
            key=key, evictions=0 if spec.evicts else None
        )
        _reports[key] = replace(
            current, lookups=current.lookups + 1, hits=current.hits + 1
        )


def record_miss(key: str) -> None:
    """Count one read of ``key`` that fell through to the real work."""
    spec = spec_for(key)
    with _lock:
        current = _reports.get(key) or CacheReport(
            key=key, evictions=0 if spec.evicts else None
        )
        _reports[key] = replace(
            current, lookups=current.lookups + 1, misses=current.misses + 1
        )


def record_store(key: str) -> None:
    """Count one value written into ``key``."""
    spec = spec_for(key)
    with _lock:
        current = _reports.get(key) or CacheReport(
            key=key, evictions=0 if spec.evicts else None
        )
        _reports[key] = replace(current, stores=current.stores + 1)


def record_eviction(key: str, count: int = 1) -> None:
    """Count ``count`` entries dropped from ``key`` to hold its cap.

    Raises:
        ValueError: If ``key``'s :class:`CacheSpec` says it does not evict. A cache
            that starts evicting has to say so in its spec, otherwise the page would
            keep reporting ``None`` while the number climbed.
    """
    spec = spec_for(key)
    if not spec.evicts:
        raise ValueError(
            f"cache {key!r} is declared as not evicting, so an eviction cannot be "
            "counted against it; set CacheSpec.evicts if that has changed."
        )
    if count <= 0:
        return
    with _lock:
        current = _reports.get(key) or CacheReport(key=key, evictions=0)
        _reports[key] = replace(current, evictions=(current.evictions or 0) + count)


def note_size(key: str, entries: int) -> None:
    """Record the entry count of ``key`` as last observed by the cache itself.

    Only a backend that already knows its size calls this — an in-process dict does,
    a Redis store does not without a round trip nobody asked for.
    """
    _mutate(key, entries=int(entries))


def cache_reports() -> tuple[CacheReport, ...]:
    """Return one report per declared cache, in read-path order.

    A cache no instance registered and nothing has read still gets a row, carrying
    ``registered=False`` and ``lookups=0`` — the surface must be able to say "this
    cache exists and did nothing here" rather than omit it.
    """
    with _lock:
        snapshot = dict(_reports)
    return tuple(
        snapshot.get(key)
        or CacheReport(key=key, evictions=0 if CACHE_SPECS[key].evicts else None)
        for key in CACHE_KEYS
    )


def reset_cache_stats() -> None:
    """Drop every counter and registration. For tests, and for nothing else."""
    with _lock:
        _reports.clear()
