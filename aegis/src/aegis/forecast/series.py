"""Pure-Python series preparation: bucketing, gap-filling and frequency inference.

Deliberately dependency-free (stdlib + :mod:`aegis.forecast.types` only). Everything
here happens *before* the forecasting stack is imported, so a series can be shaped,
validated and — crucially — **refused** without paying for statsforecast at all.

Gap-filling is explicit and lossless in intent: a bucket with no events is a real
zero for a count/spend series (nothing happened), not a missing observation. Callers
that mean "unknown" must not route through here.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta

from aegis.forecast.types import SeriesPoint

__all__ = [
    "DEFAULT_BACKTEST_WINDOWS",
    "DEFAULT_CONFORMAL_WINDOWS",
    "FREQ_SEASON",
    "bucket_events",
    "infer_freq",
    "minimum_history",
    "normalise_points",
    "season_length_for",
    "step_delta",
]

#: Rolling-origin cutoffs used to MEASURE accuracy and coverage. Two is the floor for
#: a coverage rate to mean anything at all; three is the shipped default.
DEFAULT_BACKTEST_WINDOWS = 3
#: Rolling windows ``ConformalIntervals`` calibrates the band on (inside training only).
DEFAULT_CONFORMAL_WINDOWS = 3

#: Seasonal period per supported pandas frequency alias. Hourly data repeats daily,
#: daily data repeats weekly, weekly data repeats yearly, monthly data repeats yearly.
FREQ_SEASON: dict[str, int] = {"h": 24, "D": 7, "W": 52, "MS": 12}

#: Bucket width per supported frequency, used for gap-filling and horizon stamping.
_FREQ_DELTA: dict[str, timedelta] = {
    "h": timedelta(hours=1),
    "D": timedelta(days=1),
    "W": timedelta(weeks=1),
    "MS": timedelta(days=30),
}


def minimum_history(
    horizon: int,
    season_length: int,
    *,
    backtest_windows: int = DEFAULT_BACKTEST_WINDOWS,
    conformal_windows: int = DEFAULT_CONFORMAL_WINDOWS,
) -> int:
    """Return the observations needed to fit, calibrate AND backtest honestly.

    Kept here, in the dependency-free layer, so a caller can decide whether a series
    is even worth forecasting before paying to import statsforecast. The arithmetic,
    spelled out, is the whole justification for refusing a short series::

        need = backtest_windows * horizon        # every held-out scoring window
             + max(
                   (conformal_windows - 1) * horizon + 1,  # conformal calibration
                   2 * season_length + 1,                  # two full seasonal cycles
                   2 * horizon,                            # train longer than you predict
               )

    The second term is what must still remain *before the earliest cutoff*: the model
    fitted at that cutoff has to be trainable and calibratable on that slice alone.

    Args:
        horizon: Steps to forecast ahead.
        season_length: Seasonal period of the series' frequency.
        backtest_windows: Rolling-origin cutoffs to score on.
        conformal_windows: Windows the conformal band calibrates on.

    Returns:
        The minimum number of observations required.
    """
    train_floor = max(
        (conformal_windows - 1) * horizon + 1,
        2 * season_length + 1,
        2 * horizon,
    )
    return backtest_windows * horizon + train_floor


def season_length_for(freq: str) -> int:
    """Return the seasonal period assumed for a frequency alias.

    Args:
        freq: A pandas frequency alias supported by this module.

    Returns:
        The seasonal period (e.g. ``7`` for daily data).

    Raises:
        ValueError: If ``freq`` is not one of the supported aliases.
    """
    try:
        return FREQ_SEASON[freq]
    except KeyError as exc:
        supported = ", ".join(sorted(FREQ_SEASON))
        raise ValueError(f"unsupported frequency {freq!r}; supported: {supported}") from exc


def step_delta(freq: str) -> timedelta:
    """Return the nominal spacing of one step at ``freq``.

    Args:
        freq: A pandas frequency alias supported by this module.

    Returns:
        The nominal :class:`~datetime.timedelta` between consecutive observations.

    Raises:
        ValueError: If ``freq`` is not one of the supported aliases.
    """
    try:
        return _FREQ_DELTA[freq]
    except KeyError as exc:
        supported = ", ".join(sorted(FREQ_SEASON))
        raise ValueError(f"unsupported frequency {freq!r}; supported: {supported}") from exc


def _as_naive_utc(ts: datetime) -> datetime:
    """Render a timestamp as naive UTC (the ledger's own convention).

    Args:
        ts: Aware or naive timestamp; naive is already assumed to be UTC.

    Returns:
        The equivalent naive-UTC timestamp.
    """
    if ts.tzinfo is None:
        return ts
    return ts.astimezone(UTC).replace(tzinfo=None)


def normalise_points(points: Iterable[tuple[datetime, float] | SeriesPoint]) -> list[SeriesPoint]:
    """Coerce raw points to sorted, de-duplicated, naive-UTC :class:`SeriesPoint`s.

    Duplicate timestamps are **summed**, not overwritten: two ledger rows in the same
    bucket are two real events whose costs add up.

    Args:
        points: ``(timestamp, value)`` pairs or :class:`SeriesPoint` instances.

    Returns:
        The points, oldest first, one per distinct timestamp.
    """
    merged: dict[datetime, float] = {}
    for p in points:
        ts, value = (p.ts, p.value) if isinstance(p, SeriesPoint) else (p[0], float(p[1]))
        key = _as_naive_utc(ts)
        merged[key] = merged.get(key, 0.0) + float(value)
    return [SeriesPoint(ts=ts, value=merged[ts]) for ts in sorted(merged)]


def infer_freq(points: Sequence[SeriesPoint]) -> str:
    """Infer the sampling frequency from the observed spacing.

    Uses the **modal** gap rather than the mean so a single long outage does not
    reclassify an hourly series as daily.

    Args:
        points: At least two normalised points, oldest first.

    Returns:
        One of the supported pandas frequency aliases.

    Raises:
        ValueError: If fewer than two points are supplied.
    """
    if len(points) < 2:
        raise ValueError("frequency inference needs at least two observations")
    gaps = Counter(
        (points[i + 1].ts - points[i].ts).total_seconds() for i in range(len(points) - 1)
    )
    modal = min(g for g, _ in gaps.most_common(1))
    if modal <= 90 * 60:
        return "h"
    if modal <= 36 * 3600:
        return "D"
    if modal <= 10 * 86400:
        return "W"
    return "MS"


def _floor_to(ts: datetime, freq: str) -> datetime:
    """Truncate a timestamp to the start of its bucket at ``freq``.

    Args:
        ts: Naive-UTC timestamp.
        freq: Supported frequency alias.

    Returns:
        The bucket-start timestamp.
    """
    if freq == "h":
        return ts.replace(minute=0, second=0, microsecond=0)
    day = ts.replace(hour=0, minute=0, second=0, microsecond=0)
    if freq == "D":
        return day
    if freq == "W":
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)


def _advance(ts: datetime, freq: str) -> datetime:
    """Return the start of the bucket after ``ts``.

    Args:
        ts: A bucket-start timestamp.
        freq: Supported frequency alias.

    Returns:
        The next bucket-start timestamp (calendar-correct for months).
    """
    if freq != "MS":
        return ts + _FREQ_DELTA[freq]
    return (ts.replace(day=28) + timedelta(days=4)).replace(day=1)


def bucket_events(
    events: Iterable[tuple[datetime, float]],
    freq: str,
    *,
    fill_gaps: bool = True,
) -> list[SeriesPoint]:
    """Aggregate raw timestamped events into a regular series at ``freq``.

    Args:
        events: ``(timestamp, value)`` pairs; values are summed per bucket.
        freq: Supported frequency alias to bucket into.
        fill_gaps: When True, buckets with no events are emitted as ``0.0`` — the
            honest reading for a count/spend series, where "no rows" means "no
            spend", not "unknown". Set False for series where a gap is unknown.

    Returns:
        One :class:`SeriesPoint` per bucket, oldest first. Empty when ``events`` is.

    Raises:
        ValueError: If ``freq`` is not one of the supported aliases.
    """
    step_delta(freq)  # validates freq
    totals: dict[datetime, float] = {}
    for ts, value in events:
        key = _floor_to(_as_naive_utc(ts), freq)
        totals[key] = totals.get(key, 0.0) + float(value)
    if not totals:
        return []
    if not fill_gaps:
        return [SeriesPoint(ts=ts, value=totals[ts]) for ts in sorted(totals)]

    out: list[SeriesPoint] = []
    cursor, last = min(totals), max(totals)
    while cursor <= last:
        out.append(SeriesPoint(ts=cursor, value=totals.get(cursor, 0.0)))
        cursor = _advance(cursor, freq)
    return out
