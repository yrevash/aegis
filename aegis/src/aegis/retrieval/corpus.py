"""Per-tenant corpus version — the seam that makes ingestion invalidate the caches.

Both caches in front of the agent (the retrieval cache in
:mod:`aegis.retrieval.cache` and the answer cache in
:mod:`aegis.retrieval.answer_cache`) hold entries for up to an hour. Neither can know
that a tenant's corpus changed underneath them, so without a version in the key
"upload a document, then ask about it" keeps serving the pre-upload answer until the
TTL expires. Folding a corpus version into the key fixes that *by construction*: a bump
makes every prior entry for that tenant unreachable, with no scan and no eviction.

**What increments it.** Two callers, both in the host's ingestion path and both landed
with Phase 4:

* ``app.jobs.activities.finish_ingest`` — once per ingest run that reaches a terminal
  state having written chunks. Deliberately *not* at upload and *not* mid-pipeline: a
  bump while the ingest is still running would invalidate the caches and then have the
  next request answered from a corpus that is half built.
* ``app.ingestion.reindex.reindex_corpus`` — once per re-index, for the same reason a
  re-index exists at all. A rebuild under a new embedder, chunker or prefix produces
  different answers over the same documents, and an answer cached before it is exactly
  as stale as one cached before an upload.

The counter is **process-local** and starts at zero, which is a property to state rather
than to discover. It is correct for the posture this platform ships in — the ingest
worker runs as an ``asyncio`` task inside the API process, so the bump and the cache
lookup share a counter — and it is *not* durable: a restart resets to zero (harmless; a
lower version is a different key, so the pre-restart entries are simply unreachable), and
a worker started as a separate process would bump a counter the API cannot see. Splitting
the worker out therefore requires replacing the store here with a shared one; the
*keying* it feeds is already correct either way.
"""

from __future__ import annotations

import threading

__all__ = ["bump_corpus_version", "corpus_version", "reset_corpus_versions"]

#: Guards ``_VERSIONS``. Bumping is a read-modify-write, and ingestion may run from a
#: worker thread, so the increment is taken under a lock rather than assuming the GIL
#: makes it atomic.
_LOCK = threading.Lock()

#: ``tenant_id -> version``. A missing tenant means version 0; ``None`` (the unscoped /
#: shared corpus) is a legitimate key, not an absence.
_VERSIONS: dict[int | None, int] = {}


def corpus_version(tenant_id: int | None) -> int:
    """Return the current corpus version for ``tenant_id`` (0 if it never changed).

    Args:
        tenant_id: The governance tenant, or ``None`` for the shared/unscoped corpus.

    Returns:
        The tenant's corpus version — fold this into any cache key whose value depends
        on what documents the tenant holds.
    """
    with _LOCK:
        return _VERSIONS.get(tenant_id, 0)


def bump_corpus_version(tenant_id: int | None) -> int:
    """Advance ``tenant_id``'s corpus version, invalidating its cached results.

    Call this **after** a write to the tenant's corpus has finished — not while one is in
    flight. Every cache key built from the old version becomes unreachable, so the next
    request recomputes against the new corpus. Only the given tenant is affected — one
    tenant's ingest never costs another tenant its cache.

    Over-bumping is cheap (a cache miss) and under-bumping is not (a stale answer served
    silently), so a caller that is unsure whether the corpus changed should bump.

    Args:
        tenant_id: The governance tenant whose corpus changed, or ``None`` for the
            shared/unscoped corpus.

    Returns:
        The new version.
    """
    with _LOCK:
        version = _VERSIONS.get(tenant_id, 0) + 1
        _VERSIONS[tenant_id] = version
        return version


def reset_corpus_versions() -> None:
    """Clear every recorded corpus version (test isolation).

    Exists so a test that bumps a version cannot leak that state into the next test via
    this module-level dict. Production code never calls it.
    """
    with _LOCK:
        _VERSIONS.clear()
