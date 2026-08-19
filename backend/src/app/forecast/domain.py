"""The client-facing domain series, read through the adapter seam.

This module is **mechanism only**: it buckets and memoises. Everything the series
*means* — what a record is, when it arrived, what the chart's title says and what its
values are counted in — comes from ``app.adapter`` and nothing else.

It did not always. Until the Phase 8 retarget rehearsal this file reached into the
shipped domain's record collection and timestamp field by name, and owned the chart's
client-facing title as a module constant, so a retarget got two failures for free:
``/forecast`` raised ``AttributeError`` the moment the collection was renamed, and —
worse, because it never raised anything — a correctly retargeted deployment charted the
shipped domain's sentence over its own data forever. Both are now the adapter's to
answer (``DOMAIN_SERIES_LABEL``, ``DOMAIN_SERIES_UNIT``, ``domain_series_events``), and
``aegis.conformance``'s vocabulary check fails if either creeps back.

The dataset behind the series is the adapter's synthetic world, so the timestamps are
its epoch rather than today's date, and ``data_source`` says ``adapter`` so nothing
downstream can mistake it for live client data.
"""

from __future__ import annotations

from aegis.forecast import SeriesPoint, bucket_events

from app.adapter import DOMAIN_SERIES_LABEL, DOMAIN_SERIES_UNIT, domain_series_events

__all__ = [
    "DOMAIN_SERIES_LABEL",
    "DOMAIN_SERIES_UNIT",
    "domain_series",
    "reset_domain_cache",
]

_CACHE: list[SeriesPoint] | None = None


def reset_domain_cache() -> None:
    """Drop the memoised domain series (used by tests to force a rebuild)."""
    global _CACHE
    _CACHE = None


def domain_series(*, freq: str = "D") -> list[SeriesPoint]:
    """Return the client's domain demand series, memoised for the process lifetime.

    Args:
        freq: Bucket width — one of the aliases in :data:`aegis.forecast.FREQ_SEASON`.

    Returns:
        Event counts per bucket, oldest first, with empty buckets filled as ``0.0``
        (nothing arrived that day — a real zero, not a missing reading).
    """
    global _CACHE
    if freq != "D":
        return bucket_events(domain_series_events(), freq, fill_gaps=True)
    if _CACHE is None:
        _CACHE = bucket_events(domain_series_events(), freq, fill_gaps=True)
    return _CACHE
