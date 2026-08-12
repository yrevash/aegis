"""Pydantic-only response types for the trustworthy-ML spine.

Deliberately imports nothing but ``pydantic`` — no xgboost, sklearn, mapie, shap,
pandas or numpy — so any module (including the light backend API schema layer)
can depend on these shapes without pulling the heavy ML stack transitively.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["EnsembleMember", "MLExplainResponse", "ModelCard", "ShapFeature"]


class ShapFeature(BaseModel):
    """One feature's signed SHAP contribution to a prediction."""

    feature: str
    value: float
    contribution: float = Field(description="Signed SHAP attribution.")


class MLExplainResponse(BaseModel):
    """Prediction, calibrated conformal interval and SHAP attribution."""

    prediction: float | str
    conformal_interval: tuple[float, float] | None = None
    conformal_confidence: float | None = None
    interval_width: float | None = None
    prediction_set_size: int | None = None
    shap_attribution: list[ShapFeature] = Field(default_factory=list)


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
    synthetic-fallback model is never mistaken for a real domain-trained one.
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
    conformal_coverage: float = Field(description="Guaranteed marginal coverage rate.")
    calibration_size: int = Field(description="Rows in the disjoint calibration split.")
    training_size: int = Field(description="Rows the ensemble was fitted on.")
    data_source: str = Field(
        description="'provided' | 'spec_provider' | 'synthetic' — how data was sourced.",
    )
