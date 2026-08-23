"""Pydantic-only response types for the trustworthy-ML spine.

Deliberately imports nothing but ``pydantic`` — no xgboost, sklearn, mapie, shap,
pandas or numpy — so any module (including the light backend API schema layer)
can depend on these shapes without pulling the heavy ML stack transitively.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = [
    "EnsembleMember",
    "MLExplainResponse",
    "MLModelUnavailableError",
    "ModelCard",
    "ShapFeature",
]


class MLModelUnavailableError(RuntimeError):
    """No trained model is available to serve a prediction.

    Raised instead of silently training and serving a model fitted on synthetic
    noise: a caller that gets no model must be able to tell that apart from a
    caller that got a real one, so downstream code can omit the ML evidence
    entirely rather than cite a meaningless prediction.
    """


class ShapFeature(BaseModel):
    """One feature's signed SHAP contribution to a prediction.

    ``value`` is the numeric value the model saw. For a **categorical** feature
    that is the one-hot active indicator (``1.0``), which names no level on its
    own — ``value_label`` carries the actual level (e.g. ``"emea"``) so a UI can
    render ``region = emea`` rather than ``region = 1.0``.
    """

    feature: str
    value: float
    value_label: str | None = Field(
        default=None,
        description="Human-readable input value; the level name for categoricals.",
    )
    contribution: float = Field(description="Signed SHAP attribution.")


class MLExplainResponse(BaseModel):
    """Prediction, calibrated conformal interval and SHAP attribution.

    The provenance/imputation fields are the machine-readable honesty signal:
    ``data_source == "synthetic"`` means the serving model was fitted on the
    built-in noise synthesiser and carries **no domain signal**, and
    ``imputed_features`` / ``unknown_features`` say how much of the answer came
    from training medians rather than the caller's input. Downstream code (and
    the UI) must be able to discount the evidence on those signals alone.
    """

    prediction: float | str
    conformal_interval: tuple[float, float] | None = None
    conformal_confidence: float | None = None
    interval_width: float | None = None
    prediction_set_size: int | None = None
    shap_attribution: list[ShapFeature] = Field(default_factory=list)
    data_source: str | None = Field(
        default=None,
        description="'provided' | 'spec_provider' | 'synthetic' — training-data origin.",
    )
    imputed_features: list[str] = Field(
        default_factory=list,
        description="Features the caller did not supply, filled from training medians/modes.",
    )
    unknown_features: list[str] = Field(
        default_factory=list,
        description="Keys the caller supplied that are not model features (ignored).",
    )


class EnsembleMember(BaseModel):
    """One fitted member of the soft-voting ensemble, with its voting weight."""

    name: str = Field(description="Member key in the ensemble (e.g. 'xgboost').")
    kind: str = Field(description="Concrete estimator class (e.g. 'XGBRegressor').")
    weight: float = Field(description="Normalised voting weight in [0, 1].")


class ModelCard(BaseModel):
    """Honest, measured metadata for one fitted spine — the MLOps UI's data source.

    Every field is read off the *actual* fitted model (its ensemble members,
    encoded matrix, calibrated conformal predictor and stored split sizes), never
    hardcoded. ``data_source`` labels how the training frame was obtained so a
    synthetic-fallback model is never mistaken for a real domain-trained one, and
    ``dataset_digest`` names *which* frame that was.
    """

    task: str = Field(description="'regression' or 'classification'.")
    target: str = Field(description="Name of the predicted column.")
    features: list[str] = Field(description="Original input feature names.")
    n_features: int = Field(description="Number of original input features.")
    categorical_features: list[str] = Field(description="One-hot-encoded features.")
    numeric_features: list[str] = Field(description="Pass-through numeric features.")
    encoded_feature_count: int = Field(
        description="Column count of the encoded matrix the estimator is fitted on.",
    )
    ensemble_members: list[EnsembleMember] = Field(
        description="The fitted soft-voting members and their weights.",
    )
    conformal_method: str = Field(description="Conformal scheme, e.g. 'split_conformal'.")
    conformal_predictor: str = Field(description="MAPIE class name backing the guarantee.")
    conformal_coverage: float = Field(
        description=(
            "REQUESTED marginal coverage — the level asked for, not a measurement. "
            "See conformal_coverage_empirical for the rate actually achieved."
        ),
    )
    calibration_size: int = Field(description="Rows in the disjoint calibration split.")
    training_size: int = Field(description="Rows the ensemble was fitted on.")
    data_source: str = Field(
        description="'provided' | 'spec_provider' | 'synthetic' — how data was sourced.",
    )
    dataset_digest: str | None = Field(
        default=None,
        description=(
            "'sha256:<hex>' content digest of the exact feature+target columns this "
            "model was fitted on. Provenance and tamper-EVIDENCE, not prevention: it "
            "names which data produced this model so a poisoned fit is attributable "
            "and detectable afterwards; it does not stop a poisoned frame being "
            "supplied. Invariant to column order and to the index; sensitive to any "
            "cell value, to row order, to dtypes and to added/removed columns. None "
            "only on a legacy artifact fitted before digests existed."
        ),
    )
    test_size: int = Field(
        default=0,
        description="Rows in the held-out test split neither fitted nor calibrated on.",
    )
    conformal_coverage_empirical: float | None = Field(
        default=None,
        description=(
            "MEASURED coverage of the conformal interval/set on the held-out test "
            "split; None when no test split was held out."
        ),
    )
    metric_name: str | None = Field(
        default=None,
        description="Held-out accuracy metric: 'r2' (regression) or 'accuracy'.",
    )
    metric_value: float | None = Field(
        default=None,
        description="Measured value of metric_name on the held-out test split.",
    )
