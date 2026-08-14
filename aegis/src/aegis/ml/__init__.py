"""Trustworthy-ML spine — public interface.

Exposes the module-level contract other modules depend on
(``predict_explain``) plus the training/loading entrypoints and the underlying
:class:`TrustworthyModel`. See :mod:`aegis.ml.model` for the design and the exact
XGBoost / MAPIE / SHAP versions targeted.

This package's ``__init__`` deliberately imports nothing heavy at module load
time — :mod:`aegis.ml.model` (xgboost/sklearn/mapie/shap/pandas) is imported
lazily, inside each function body and via :func:`__getattr__` for the
``TrustworthyModel`` re-export. That keeps ``import aegis.ml.types`` (and any
other light submodule) free of the heavy ML stack; see
``tests/ml/test_types_is_dep_free.py``.

Typical lifecycle (default / module-level singleton)::

    from aegis.ml import train, predict_explain

    train(spec, frame, path="aegis/ml/artifacts/ml_spine.joblib")  # offline, once
    resp = predict_explain({"priority": "urgent", "queue_depth_at_open": 12})
    resp.conformal_interval      # calibrated bounds (requested coverage)
    resp.conformal_confidence    # the coverage rate that was *requested*, e.g. 0.9
    resp.shap_attribution        # signed per-feature contributions
    resp.data_source             # 'provided' | 'spec_provider' | 'synthetic'
    resp.imputed_features        # what the caller did NOT supply

There is **no silent fallback**: :func:`predict_explain` raises
:class:`~aegis.ml.types.MLModelUnavailableError` when no model has been trained or
persisted, rather than fitting one on the built-in noise synthesiser and serving
its interval as if it were calibrated evidence. Synthetic models are never
auto-persisted for the same reason.

Bring-your-own spec (the hackathon path — inject *what* to predict)::

    from aegis.ml import ResolvedSpec, TrustworthyModel

    # A custom spec: features + target + task (+ optional categoricals / frame provider).
    spec = ResolvedSpec(
        features=["age", "region", "tenure"],
        target="churned",
        task="classification",
        categorical_features=["region"],
    )
    model = TrustworthyModel.train(spec, frame=my_dataframe, path=None)
    resp = model.predict_explain({"age": 41, "region": "emea", "tenure": 3})
    card = model.model_card()    # honest, measured metadata for the MLOps UI

``ResolvedSpec`` is the concrete, constructible spec; :class:`MLSpec` is the
structural Protocol any adapter-shaped object can satisfy instead (read leniently
by :func:`resolve_spec`).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from aegis.ml.spec import FALLBACK_SPEC, MLSpec, ResolvedSpec, TaskType, resolve_spec
from aegis.ml.types import (
    EnsembleMember,
    MLExplainResponse,
    MLModelUnavailableError,
    ModelCard,
    ShapFeature,
)

if TYPE_CHECKING:
    import pandas as pd

    from aegis.ml.model import TrustworthyModel
    from aegis.ml.spec import MLSpec as _MLSpec

__all__ = [
    "DEFAULT_ARTIFACT_PATH",
    "FALLBACK_SPEC",
    "EnsembleMember",
    "MLExplainResponse",
    "MLModelUnavailableError",
    "MLSpec",
    "ModelCard",
    "ResolvedSpec",
    "ShapFeature",
    "TaskType",
    "TrustworthyModel",
    "get_model",
    "load",
    "predict_explain",
    "resolve_spec",
    "train",
]

DEFAULT_ARTIFACT_PATH = Path(__file__).resolve().parent / "artifacts" / "ml_spine.joblib"
"""Portable, package-relative default location for the persisted model."""

_MODEL: TrustworthyModel | None = None


def __getattr__(name: str) -> object:
    """Lazily resolve :class:`TrustworthyModel` without an eager heavy import.

    Args:
        name: Attribute name being accessed on this module.

    Returns:
        The resolved attribute value.

    Raises:
        AttributeError: If ``name`` is not a recognised lazy export.
    """
    if name == "TrustworthyModel":
        from aegis.ml.model import TrustworthyModel

        return TrustworthyModel
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


def train(
    spec: _MLSpec | None = None,
    frame: pd.DataFrame | None = None,
    *,
    confidence_level: float = 0.9,
    calibration_size: float = 0.25,
    test_size: float = 0.2,
    random_state: int = 0,
    path: Path | str | None = DEFAULT_ARTIFACT_PATH,
) -> TrustworthyModel:
    """Train, calibrate, evaluate and persist the spine, caching it for serving.

    Thin wrapper over :meth:`~aegis.ml.model.TrustworthyModel.train` that also
    updates the process-wide singleton returned by :func:`predict_explain`.

    A model trained on the built-in synthesiser is **never** written to ``path``
    (only a warning is logged), so a noise artifact can never be reloaded later as
    a real one; call ``model.save(path)`` explicitly if that is genuinely wanted.

    Args:
        spec: Domain spec; resolved to :data:`FALLBACK_SPEC` when ``None``.
        frame: Explicit training frame; synthesised when ``None``.
        confidence_level: Requested target coverage of the conformal predictor.
        calibration_size: Fraction of the non-test rows reserved for calibration.
        test_size: Fraction of rows held out to measure accuracy and empirical
            coverage; ``0`` skips the measurement.
        random_state: Seed for determinism.
        path: Where to persist the artifact; pass ``None`` to skip persistence.

    Returns:
        The freshly trained :class:`~aegis.ml.model.TrustworthyModel`.
    """
    from aegis.ml.model import TrustworthyModel

    global _MODEL
    _MODEL = TrustworthyModel.train(
        spec,
        frame,
        confidence_level=confidence_level,
        calibration_size=calibration_size,
        test_size=test_size,
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
    from aegis.ml.model import TrustworthyModel

    global _MODEL
    _MODEL = TrustworthyModel.load(path)
    return _MODEL


def get_model() -> TrustworthyModel:
    """Return the cached spine, loading a persisted artifact on first use.

    Resolution order: the in-process singleton, then a persisted artifact at
    :data:`DEFAULT_ARTIFACT_PATH`. There is deliberately **no third step**. The
    previous fallback trained a model on the built-in noise synthesiser and served
    its point prediction, its "90% coverage" interval and its ``feature_0…3``
    drivers as if they were calibrated evidence — a caller had no way to tell that
    apart from a real model. Refusing is the honest answer: the agent's ML node is
    best-effort and simply omits the evidence, which is strictly better than citing
    a number with no signal in it. To serve, train explicitly on real data
    (:func:`train`) or assign a model via :func:`load`.

    Returns:
        A ready-to-serve :class:`~aegis.ml.model.TrustworthyModel`.

    Raises:
        MLModelUnavailableError: If no model is cached and no artifact exists.
    """
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    try:
        return load(DEFAULT_ARTIFACT_PATH)
    except FileNotFoundError as exc:
        msg = (
            f"No trained ML artifact at {DEFAULT_ARTIFACT_PATH}. The spine will not "
            "silently fall back to a model fitted on synthetic noise and serve it as "
            "calibrated evidence — train one on real data first, e.g. "
            "aegis.ml.train(spec, frame)."
        )
        raise MLModelUnavailableError(msg) from exc


def predict_explain(features: dict[str, Any]) -> MLExplainResponse:
    """Predict, conformalise and explain one input (module-level contract).

    Args:
        features: ``feature name → value`` for a single prediction.

    Returns:
        An :class:`~aegis.ml.types.MLExplainResponse` with the prediction, the
        calibrated conformal interval, the requested coverage rate, the signed
        SHAP attributions and the ``data_source`` / imputation honesty signals.

    Raises:
        MLModelUnavailableError: If no trained model is available to serve.
    """
    return get_model().predict_explain(features)
