"""Deterministic, dependency-light series fixtures for the forecast tests."""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

import pytest

_ORIGIN = datetime(2026, 1, 1)


def make_daily(n: int, *, seed: int = 0, level: float = 50.0) -> list[tuple[datetime, float]]:
    """Build a daily series with a trend, a weekly cycle and reproducible noise.

    Args:
        n: Number of daily observations.
        seed: RNG seed (``random``, so the fixture needs no numpy).
        level: Base level of the series.

    Returns:
        ``(timestamp, value)`` pairs, oldest first.
    """
    rng = random.Random(seed)
    return [
        (
            _ORIGIN + timedelta(days=i),
            level + 0.3 * i + 8.0 * math.sin(i * 2 * math.pi / 7) + rng.gauss(0, 2),
        )
        for i in range(n)
    ]


@pytest.fixture
def daily_series() -> list[tuple[datetime, float]]:
    """A 140-point daily series: long enough to fit, calibrate AND backtest at h=14."""
    return make_daily(140)


@pytest.fixture
def short_series() -> list[tuple[datetime, float]]:
    """A 20-point daily series: far too short for an honest h=14 forecast."""
    return make_daily(20)
