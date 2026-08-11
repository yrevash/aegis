"""Trustworthy-ML spine — strangler shim over ``aegis.ml``.

The ensemble + MAPIE conformal + SHAP spine itself now lives in
:mod:`aegis.ml` (domain-agnostic, importable standalone). This module is the
thin backend-specific layer that wires the real domain spec
(``app.adapter.ml_spec``) into training and serving, so the module-level
contract other backend code depends on (``predict_explain``) is unchanged and
keeps predicting on genuine domain data.

Typical lifecycle::

    from app.ml import train, predict_explain

    train(path="app/ml/artifacts/ml_spine.joblib")  # offline, once
    resp = predict_explain({"priority": "urgent", ...})
    resp.conformal_interval      # calibrated bounds (guaranteed coverage)
    resp.conformal_confidence    # the guaranteed coverage rate, e.g. 0.9
    resp.shap_attribution        # signed per-feature contributions
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from aegis.ml import DEFAULT_ARTIFACT_PATH, FALLBACK_SPEC, TaskType
from aegis.ml.model import TrustworthyModel
from aegis.ml.spec import MLSpec, ResolvedSpec, resolve_spec

if TYPE_CHECKING:
    import pandas as pd

    from app.api.schemas import MLExplainResponse
    from app.ml.spec import MLSpec as _MLSpec

__all__ = [
    "DEFAULT_ARTIFACT_PATH",
    "FALLBACK_SPEC",
    "MLSpec",
    "ResolvedSpec",
    "TaskType",
    "TrustworthyModel",
    "get_model",
    "load",
    "predict_explain",
    "resolve_spec",
    "train",
]

_MODEL: TrustworthyModel | None = None


def _domain_spec() -> MLSpec | None:
    """Return the domain adapter's ``ml_spec`` module, or ``None`` if unavailable.

    Returns:
        The ``app.adapter.ml_spec`` module object, or ``None`` if the domain
        adapter cannot be imported (keeps the spine trainable in isolation).
    """
    try:
        from app.adapter import ml_spec
    except (ImportError, AttributeError):
        return None
    return ml_spec


def train(
    spec: _MLSpec | None = None,
    frame: pd.DataFrame | None = None,
    *,
    confidence_level: float = 0.9,
    calibration_size: float = 0.25,
    random_state: int = 0,
    path: Path | str | None = DEFAULT_ARTIFACT_PATH,
) -> TrustworthyModel:
    """Train, calibrate and persist the spine on the real domain spec.

    Thin wrapper over :meth:`aegis.ml.model.TrustworthyModel.train` that injects
    ``app.adapter.ml_spec`` when no explicit spec is given, and updates the
    process-wide singleton returned by :func:`predict_explain`.

    Args:
        spec: Domain spec; resolved from ``app.adapter.ml_spec`` when ``None``.
        frame: Explicit training frame; the spec's own frame provider (or the
            built-in synthesiser) is used when ``None``.
        confidence_level: Guaranteed target coverage of the conformal predictor.
        calibration_size: Fraction of rows reserved for calibration.
        random_state: Seed for determinism.
        path: Where to persist the artifact; pass ``None`` to skip persistence.

    Returns:
        The freshly trained :class:`~aegis.ml.model.TrustworthyModel`.
    """
    global _MODEL
    _MODEL = TrustworthyModel.train(
        spec if spec is not None else _domain_spec(),
        frame,
        confidence_level=confidence_level,
        calibration_size=calibration_size,
        random_state=random_state,
        path=path,
    )
    return _MODEL


def load(path: Path | str = DEFAULT_ARTIFACT_PATH) -> TrustworthyModel:
    """Load a persisted spine and cache it for serving.

    Args:
        path: Artifact produced by :func:`train`.

    Returns:
        The loaded :class:`~aegis.ml.model.TrustworthyModel`.
    """
    global _MODEL
    _MODEL = TrustworthyModel.load(path)
    return _MODEL


def get_model() -> TrustworthyModel:
    """Return the cached spine, loading or training one (on the domain spec) on first use.

    Resolution order: the in-process singleton, then a persisted artifact at
    :data:`DEFAULT_ARTIFACT_PATH`, then a freshly trained model on the real
    domain spec (so the endpoint always answers, even before an artifact exists).

    Returns:
        A ready-to-serve :class:`~aegis.ml.model.TrustworthyModel`.
    """
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    try:
        return load()
    except FileNotFoundError:
        return train()


def predict_explain(features: dict[str, Any]) -> MLExplainResponse:
    """Predict, conformalise and explain one input (module-level contract).

    Args:
        features: ``feature name → value`` for a single prediction.

    Returns:
        An :class:`~app.api.schemas.MLExplainResponse` with the prediction, the
        calibrated conformal interval, the guaranteed coverage rate and the
        signed SHAP attributions.
    """
    return get_model().predict_explain(features)
