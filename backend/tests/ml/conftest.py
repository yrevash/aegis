"""Pytest fixtures for the ML spine: put ``src`` on the path, no live infra."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Make the `app` package importable without an editable install.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from app.ml.spec import ResolvedSpec  # noqa: E402


@pytest.fixture
def features() -> list[str]:
    return ["f0", "f1", "f2"]


@pytest.fixture
def regression_spec(features: list[str]) -> ResolvedSpec:
    return ResolvedSpec(features=features, target="y", task="regression")


@pytest.fixture
def classification_spec(features: list[str]) -> ResolvedSpec:
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
    return _make_frame(features, "y", classify=False, seed=42)


@pytest.fixture
def classification_frame(features: list[str]) -> pd.DataFrame:
    return _make_frame(features, "label", classify=True, seed=42)
