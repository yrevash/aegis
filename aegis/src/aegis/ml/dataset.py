"""Training-data acquisition for the ML spine.

The spine needs a labelled ``DataFrame`` to fit and calibrate on. Data comes,
in priority order, from an explicit frame, the injected spec's own
``training_frame`` provider, or a deterministic built-in synthesiser. The
synthesiser lets the whole spine train, calibrate, explain and round-trip
through joblib with **no domain code, no network and no external files** —
essential for portable CPU-only deployment and offline unit tests.

Targeted libraries (verified 2026-08-05): pandas 2.3 (capped <2.4 for nemoguardrails
co-existence), numpy 2.4.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from aegis.ml.spec import ResolvedSpec


def synthesise_frame(
    spec: ResolvedSpec,
    *,
    n_rows: int = 600,
    random_state: int = 0,
) -> pd.DataFrame:
    """Generate a deterministic synthetic training frame matching ``spec``.

    Features are standard-normal columns. The target is a fixed linear
    combination of the features plus mild Gaussian noise; for classification
    it is thresholded at its median into two balanced classes. The mapping is
    learnable, so a fitted tree model produces meaningful SHAP attributions and
    a non-degenerate conformal interval.

    Args:
        spec: Resolved spec providing feature names, target name and task.
        n_rows: Number of rows to generate.
        random_state: Seed for reproducibility.

    Returns:
        A ``DataFrame`` with one column per ``spec.features`` plus the target
        column named ``spec.target``.
    """
    rng = np.random.default_rng(random_state)
    n_features = len(spec.features)
    x = rng.normal(size=(n_rows, n_features))

    # Deterministic, alternating-sign weights so every feature contributes.
    weights = np.array([(-1.0) ** i * (1.0 + i * 0.5) for i in range(n_features)])
    signal = x @ weights + rng.normal(scale=0.1, size=n_rows)

    frame = pd.DataFrame(x, columns=spec.features)
    if spec.task == "classification":
        frame[spec.target] = (signal > np.median(signal)).astype(int)
    else:
        frame[spec.target] = signal
    return frame


def resolve_training_frame(
    spec: ResolvedSpec,
    frame: pd.DataFrame | None = None,
    *,
    n_rows: int = 600,
    random_state: int = 0,
) -> pd.DataFrame:
    """Obtain a training frame for ``spec`` from the best available source.

    Args:
        spec: The resolved ML spec.
        frame: An explicit frame supplied by the caller; used verbatim if given.
        n_rows: Row count for the synthetic fallback.
        random_state: Seed for the synthetic fallback.

    Returns:
        A labelled training ``DataFrame`` containing every feature column and
        the target column.

    Raises:
        ValueError: If a supplied ``frame`` is missing required columns.
    """
    if frame is not None:
        missing = [c for c in (*spec.features, spec.target) if c not in frame.columns]
        if missing:
            raise ValueError(f"Training frame is missing required columns: {missing}")
        return frame

    if spec.frame_provider is not None:
        return spec.frame_provider()

    return synthesise_frame(spec, n_rows=n_rows, random_state=random_state)
