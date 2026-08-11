"""Pydantic-only response types for the trustworthy-ML spine.

Deliberately imports nothing but ``pydantic`` — no xgboost, sklearn, mapie, shap,
pandas or numpy — so any module (including the light backend API schema layer)
can depend on these shapes without pulling the heavy ML stack transitively.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["MLExplainResponse", "ShapFeature"]


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
