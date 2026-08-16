"""Per-tenant corpus version — the seam that makes ingestion invalidate the caches.

Both caches in front of the agent (the retrieval cache in
:mod:`aegis.retrieval.cache` and the answer cache in
:mod:`aegis.retrieval.answer_cache`) hold entries for up to an hour. Neither can know
that a tenant's corpus changed underneath them, so without a version in the key
"upload a document, then ask about it" keeps serving the pre-upload answer until the
TTL expires. Folding a corpus version into the key fixes that *by construction*: a bump
makes every prior entry for that tenant unreachable, with no scan and no eviction.

**Nothing increments this yet.** Document ingestion lands in a later phase; this module
is the single place that phase has to change — call :func:`bump_corpus_version` once
per successful ingest and both caches invalidate for that tenant alone. The counter is
deliberately built now, while the key format is being changed anyway, because retro-
fitting a field into a cache key means invalidating every entry a second time.

The counter is **process-local** and starts at zero. That is honest and sufficient for
its current job (a single API process, and a version that never moves), and it is
explicitly not durable: a restart resets to zero, and two processes do not agree. The
later phase replaces the store here — the *keying* it feeds is already correct.
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

    Call this **after** a successful write to the tenant's corpus. Every cache key built
    from the old version becomes unreachable, so the next request recomputes against the
    new corpus. Only the given tenant is affected — one tenant's upload never costs
    another tenant its cache.

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
