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

SYNTHETIC_LEVELS: tuple[str, ...] = ("alpha", "bravo", "charlie", "delta")
"""String levels drawn for a synthesised **categorical** column.

Categorical features must be synthesised as genuine strings: the spine one-hot
encodes exactly ``spec.categorical_features``, so emitting floats there would fit
the encoder on one degenerate level per row and make every real inference row an
all-zero (``handle_unknown="ignore"``) block the model never saw in training.
"""


def _weight(index: int) -> float:
    """Return the deterministic, alternating-sign weight for feature ``index``."""
    return (-1.0) ** index * (1.0 + index * 0.5)


def synthesise_frame(
    spec: ResolvedSpec,
    *,
    n_rows: int = 600,
    random_state: int = 0,
) -> pd.DataFrame:
    """Generate a deterministic synthetic training frame matching ``spec``.

    Numeric features are standard-normal columns; **categorical** features are
    drawn as real string levels from :data:`SYNTHETIC_LEVELS` so the frame is
    type-compatible with the one-hot preprocessor the spine fits on it. The target
    is a fixed linear combination of the numeric columns and the (centred) level
    codes plus mild Gaussian noise; for classification it is thresholded at its
    median into two balanced classes. The mapping is learnable, so a fitted tree
    model produces meaningful SHAP attributions and a non-degenerate conformal
    interval.

    Args:
        spec: Resolved spec providing feature names, target name, task and the
            categorical subset.
        n_rows: Number of rows to generate.
        random_state: Seed for reproducibility.

    Returns:
        A ``DataFrame`` with one column per ``spec.features`` (in declared order,
        categoricals typed as strings) plus the target column ``spec.target``.
    """
    rng = np.random.default_rng(random_state)
    numeric = spec.numeric_features
    categorical = list(spec.categorical_features)

    columns: dict[str, object] = {}
    signal = np.zeros(n_rows)

    x = rng.normal(size=(n_rows, len(numeric)))
    for i, name in enumerate(numeric):
        columns[name] = x[:, i]
        signal = signal + _weight(i) * x[:, i]

    n_levels = len(SYNTHETIC_LEVELS)
    for j, name in enumerate(categorical):
        codes = rng.integers(0, n_levels, size=n_rows)
        columns[name] = np.asarray(SYNTHETIC_LEVELS, dtype=object)[codes]
        # Centre the level codes so no level is a privileged "zero" baseline.
        signal = signal + _weight(j) * (codes - (n_levels - 1) / 2.0)

    signal = signal + rng.normal(scale=0.1, size=n_rows)

    # Reindex to the spec's declared feature order (numerics were built first).
    frame = pd.DataFrame(columns)[list(spec.features)]
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
