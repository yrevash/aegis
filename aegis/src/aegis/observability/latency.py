"""Real per-node / per-run latency aggregation over completed agent runs.

Every graph node is timed with a wall-clock ``time.perf_counter()`` delta in
:func:`aegis.agent.graph._timed` and surfaced as the ``duration_ms`` on each
``node_finished`` event (and, folded, on ``run_summary()['nodes'][*]`` and
``totals.duration_ms``). Those are the **real, measured** durations — nothing here
fabricates a figure.

This module turns those measured per-run node timings into the honest figures a
latency dashboard needs — per-node p50/p95/max/count, whole-run duration
percentiles, and the slowest node — without inventing a single number:

- :func:`record_run_latency` folds one completed run's node timings into a bounded,
  **in-process** rolling window (a ``deque(maxlen=...)``). It is side-effect-only
  telemetry: safe to call from anywhere, defensive against malformed input, and a
  no-op for a run that recorded no timed nodes. It never affects run behaviour.
- :func:`latency_summary` computes the percentiles from that window (or from a
  caller-supplied list of runs, for a pure/deterministic computation). ``p95`` is a
  **real percentile of real samples**, never a constant.

Honest caveats, surfaced on the summary itself (``source`` / ``window_capacity``):
the window is **per-process** and **resets on restart** — it is not a persistent
metrics store. An empty window returns an honest *empty* summary (``empty=True``,
no zeros pretending to be data), not fabricated latencies.

Pure stdlib only (no OTel/Phoenix), so importing it stays leaf-clean.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "DEFAULT_WINDOW_CAPACITY",
    "LatencySummary",
    "NodeLatency",
    "latency_summary",
    "percentile",
    "record_run_latency",
    "reset_latency_window",
]

#: Source label stamped on a summary computed from the in-process rolling window.
#: Made explicit so a consumer never mistakes it for a durable metrics store.
_SOURCE_WINDOW = "in_process_rolling_window"
#: Source label when the caller supplies the runs directly (a pure computation).
_SOURCE_SUPPLIED = "supplied_runs"

#: Default bound on the rolling window (number of completed runs retained).
DEFAULT_WINDOW_CAPACITY = 512


def percentile(values: Sequence[float], q: float) -> float:
    """Return the ``q``-th percentile (0–100) of ``values`` by linear interpolation.

    Uses the standard linear-interpolation-between-closest-ranks method (numpy's
    default / Excel ``PERCENTILE.INC`` / ``statistics.quantiles(method="inclusive")``):
    the fractional rank is ``(q/100) * (n - 1)`` over the *sorted* values, and the
    result interpolates between the two straddling samples. Deterministic and exact,
    so a known set has a known percentile.

    Args:
        values: A non-empty sequence of samples (need not be sorted).
        q: The percentile in ``[0, 100]``.

    Returns:
        The percentile as a float.

    Raises:
        ValueError: If ``values`` is empty.
    """
    if not values:
        raise ValueError("percentile() requires at least one value")
    ordered = sorted(values)
    n = len(ordered)
    if n == 1:
        return float(ordered[0])
    rank = (q / 100.0) * (n - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return float(ordered[lo])
    frac = rank - lo
    return float(ordered[lo] + (ordered[hi] - ordered[lo]) * frac)


@dataclass(frozen=True)
class NodeLatency:
    """Aggregated latency for one node across the recorded runs."""

    node: str
    count: int
    p50_ms: float
    p95_ms: float
    max_ms: float
    total_ms: float

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly view for the metrics API / dashboard."""
        return {
            "node": self.node,
            "count": self.count,
            "p50_ms": self.p50_ms,
            "p95_ms": self.p95_ms,
            "max_ms": self.max_ms,
            "total_ms": self.total_ms,
        }


@dataclass(frozen=True)
class LatencySummary:
    """The honest latency figures a dashboard renders — all from real samples.

    ``empty`` is ``True`` when no runs have been recorded yet; the per-node list is
    then empty and the run percentiles are ``None`` (an honest empty state, never
    zeros posing as measurements). ``source`` / ``window_capacity`` document where
    the numbers came from — for the rolling window, that it is per-process and
    resets on restart.
    """

    run_count: int
    per_node: list[NodeLatency]
    run_p50_ms: float | None
    run_p95_ms: float | None
    run_max_ms: float | None
    slowest_node: str | None
    source: str
    window_capacity: int | None = None
    empty: bool = field(default=False)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly view for the metrics API / dashboard."""
        return {
            "run_count": self.run_count,
            "per_node": [n.as_dict() for n in self.per_node],
            "run_p50_ms": self.run_p50_ms,
            "run_p95_ms": self.run_p95_ms,
            "run_max_ms": self.run_max_ms,
            "slowest_node": self.slowest_node,
            "source": self.source,
            "window_capacity": self.window_capacity,
            "empty": self.empty,
        }


# ── In-process rolling window ─────────────────────────────────────────────────
#
# One entry per completed run: a tuple of (node, duration_ms) pairs. Bounded by
# ``maxlen`` so memory is capped; oldest runs fall off. Guarded by a lock because a
# host may record from concurrent request tasks. This is deliberately *not* a
# durable store — it is a per-process, resets-on-restart telemetry buffer.

_lock = threading.Lock()
_window: deque[tuple[tuple[str, float], ...]] = deque(maxlen=DEFAULT_WINDOW_CAPACITY)


def _coerce_run(nodes: Iterable[Any]) -> tuple[tuple[str, float], ...]:
    """Extract ``(node_name, duration_ms)`` pairs from a run's node records.

    Accepts the shape :func:`aegis.agent.run_summary` emits (a list of dicts with
    ``node`` / ``duration_ms``), an attribute-bearing model, or a raw ``(name, ms)``
    pair. Nodes with a missing/``None``/non-numeric duration (e.g. a paused
    ``approval`` node that never finished) are skipped — only real measured timings
    are recorded. Malformed entries are ignored rather than raised, so recording can
    never disturb a run.
    """
    pairs: list[tuple[str, float]] = []
    for entry in nodes:
        name: Any
        dur: Any
        if isinstance(entry, Mapping):
            name = entry.get("node")
            dur = entry.get("duration_ms")
        elif isinstance(entry, (tuple, list)) and len(entry) == 2:
            name, dur = entry
        else:
            name = getattr(entry, "node", None)
            dur = getattr(entry, "duration_ms", None)
        if name is None or dur is None:
            continue
        try:
            dur_f = float(dur)
        except (TypeError, ValueError):
            continue
        if math.isnan(dur_f) or math.isinf(dur_f):
            continue
        pairs.append((str(name), dur_f))
    return tuple(pairs)


def record_run_latency(nodes: Iterable[Any]) -> int:
    """Fold one completed run's per-node timings into the rolling window.

    Side-effect-only telemetry — it records and returns; it never affects run
    behaviour and never raises on malformed input. A run that produced no timed
    nodes records nothing.

    Args:
        nodes: The run's node records — e.g. ``run_summary(events)["nodes"]``.

    Returns:
        The number of timed nodes recorded for this run (``0`` if none).
    """
    run = _coerce_run(nodes)
    if not run:
        return 0
    with _lock:
        _window.append(run)
    return len(run)


def reset_latency_window() -> None:
    """Clear the in-process rolling window (test/maintenance helper)."""
    with _lock:
        _window.clear()


def _snapshot_window() -> list[tuple[tuple[str, float], ...]]:
    """Return a consistent copy of the current window (under the lock)."""
    with _lock:
        return list(_window)


def latency_summary(
    runs: Iterable[Iterable[Any]] | None = None,
) -> LatencySummary:
    """Compute per-node + per-run latency percentiles from real samples.

    Args:
        runs: An explicit iterable of runs (each a list of node records, as
            :func:`aegis.agent.run_summary` emits under ``"nodes"``) to compute from —
            a pure, deterministic computation. When ``None`` (the default), the
            in-process rolling window fed by :func:`record_run_latency` is used, and
            the summary is labelled as the per-process window.

    Returns:
        A :class:`LatencySummary`. When there are no samples it is an honest *empty*
        summary (``empty=True``, no per-node rows, ``None`` run percentiles) — never
        fabricated zeros.
    """
    if runs is None:
        window = _snapshot_window()
        source = _SOURCE_WINDOW
        capacity: int | None = _window.maxlen
    else:
        window = [_coerce_run(r) for r in runs]
        window = [r for r in window if r]
        source = _SOURCE_SUPPLIED
        capacity = None

    if not window:
        return LatencySummary(
            run_count=0,
            per_node=[],
            run_p50_ms=None,
            run_p95_ms=None,
            run_max_ms=None,
            slowest_node=None,
            source=source,
            window_capacity=capacity,
            empty=True,
        )

    # Per-node samples across every recorded run (a node may appear more than once
    # in a run — e.g. plan re-run in a self-repair loop — and each occurrence is a
    # distinct sample). Insertion order preserves first-seen node order.
    per_node_samples: dict[str, list[float]] = {}
    run_durations: list[float] = []
    for run in window:
        run_total = 0.0
        for name, dur in run:
            per_node_samples.setdefault(name, []).append(dur)
            run_total += dur
        # A run's duration is the sum of its node durations — identical to the
        # ``totals.duration_ms`` that ``run_summary`` reports, so the two never diverge.
        run_durations.append(run_total)

    per_node: list[NodeLatency] = []
    for name, samples in per_node_samples.items():
        per_node.append(
            NodeLatency(
                node=name,
                count=len(samples),
                p50_ms=percentile(samples, 50),
                p95_ms=percentile(samples, 95),
                max_ms=float(max(samples)),
                total_ms=float(sum(samples)),
            )
        )

    # Slowest node = highest p95 (typical tail latency), tie-broken by max then total.
    slowest = max(
        per_node,
        key=lambda n: (n.p95_ms, n.max_ms, n.total_ms),
    ).node

    return LatencySummary(
        run_count=len(window),
        per_node=per_node,
        run_p50_ms=percentile(run_durations, 50),
        run_p95_ms=percentile(run_durations, 95),
        run_max_ms=float(max(run_durations)),
        slowest_node=slowest,
        source=source,
        window_capacity=capacity,
        empty=False,
    )
