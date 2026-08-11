"""Pytest fixtures for the ML spine: small, deterministic, offline datasets."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aegis.ml.spec import ResolvedSpec


@pytest.fixture
def features() -> list[str]:
    """Feature-column names shared by the regression/classification fixtures."""
    return ["f0", "f1", "f2"]


@pytest.fixture
def regression_spec(features: list[str]) -> ResolvedSpec:
    """A minimal regression :class:`ResolvedSpec` over ``features``."""
    return ResolvedSpec(features=features, target="y", task="regression")


@pytest.fixture
def classification_spec(features: list[str]) -> ResolvedSpec:
    """A minimal classification :class:`ResolvedSpec` over ``features``."""
    return ResolvedSpec(features=features, target="label", task="classification")


def _make_frame(features: list[str], target: str, *, classify: bool, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(400, len(features)))
    signal = x @ np.array([2.0, -1.5, 0.75]) + rng.normal(scale=0.1, size=400)
    frame = pd.DataFrame(x, columns=features)
    frame[target] = (signal > np.median(signal)).astype(int) if classify else signal
    return frame


@pytest.fixture
def regression_frame(features: list[str]) -> pd.DataFrame:
    """A deterministic synthetic regression training frame."""
    return _make_frame(features, "y", classify=False, seed=42)


@pytest.fixture
def classification_frame(features: list[str]) -> pd.DataFrame:
    """A deterministic synthetic classification training frame."""
    return _make_frame(features, "label", classify=True, seed=42)
