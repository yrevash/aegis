"""The client-facing domain series, read through the adapter seam.

This is the retarget point. The forecaster itself knows nothing about support
requests — it is handed ``(timestamp, value)`` pairs. Everything domain-specific
lives in :func:`domain_series`, which reads the adapter's own records through
``app.adapter``'s public exports and nothing else. On the day the blind problem
drops, ``adapter/schema.py`` and ``adapter/generator.py`` change, this function's
one field reference follows, and the whole forecast surface retargets with the rest
of the platform.

The series is deliberately built from :attr:`ServiceRequest.created_at` — *arrivals*,
not resolutions. Arrival volume is the quantity a client actually plans capacity
against, and it is complete: a request that is still open still arrived, whereas a
resolution series silently truncates the recent end and would bias the trend
downwards for no reason a reader could see.

The dataset here is the adapter's synthetic world, so the timestamps are its epoch
rather than today's date, and ``data_source`` says ``adapter`` so nothing downstream
can mistake it for live client data.
"""

from __future__ import annotations

from datetime import datetime

from aegis.forecast import SeriesPoint, bucket_events

__all__ = ["DOMAIN_SERIES_LABEL", "domain_series", "reset_domain_cache"]

#: What the domain series measures, in the client's language.
DOMAIN_SERIES_LABEL = "Service requests opened per day"

#: How many records to fabricate for the series. Large enough that a daily bucket
#: over the generator's ~120-day span is a countable volume rather than a sparse
#: 0/1 rattle, which no model (and no reader) could learn anything from.
_NUM_RECORDS = 1400

#: Seed for a stable, reproducible demo series across processes and reloads.
_SEED = 11

_CACHE: list[SeriesPoint] | None = None


def reset_domain_cache() -> None:
    """Drop the memoised domain series (used by tests to force a rebuild)."""
    global _CACHE
    _CACHE = None


def _events() -> list[tuple[datetime, float]]:
    """Return one ``(created_at, 1.0)`` arrival event per adapter record.

    The single domain-coupled function in this module: it names the record type and
    the timestamp field, and nothing else.

    Returns:
        Arrival events, unordered.
    """
    from app.adapter import GeneratorConfig, generate_synthetic_sync

    dataset = generate_synthetic_sync(
        GeneratorConfig(num_requests=_NUM_RECORDS, seed=_SEED, use_llm=False)
    )
    return [(r.created_at, 1.0) for r in dataset.requests]


def domain_series(*, freq: str = "D") -> list[SeriesPoint]:
    """Return the client's domain demand series, memoised for the process lifetime.

    Args:
        freq: Bucket width — one of the aliases in :data:`aegis.forecast.FREQ_SEASON`.

    Returns:
        Arrival counts per bucket, oldest first, with empty buckets filled as ``0.0``
        (no requests arrived that day — a real zero, not a missing reading).
    """
    global _CACHE
    if freq != "D":
        return bucket_events(_events(), freq, fill_gaps=True)
    if _CACHE is None:
        _CACHE = bucket_events(_events(), freq, fill_gaps=True)
    return _CACHE
