"""AG-UI streaming for the ML spine — emits its work à la carte over the emitter.

Wraps a single ``predict_explain`` call in a ``STEP_STARTED``/``STEP_FINISHED``
bracket and, within it, emits the calibrated conformal interval followed by the
SHAP attribution as two ``CUSTOM`` events, so the frontend can render each piece
of evidence as soon as it is ready.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aegis.core import stream_names
from aegis.core.events import SpanKind
from aegis.ml import get_model

if TYPE_CHECKING:
    from aegis.core.stream import AegisEmitter
    from aegis.ml.model import TrustworthyModel
    from aegis.ml.types import MLExplainResponse

_STEP_NAME = "ml_predict"


async def stream_predict_explain(
    features: dict[str, Any],
    emitter: AegisEmitter,
    *,
    model: TrustworthyModel | None = None,
) -> MLExplainResponse:
    """Predict, conformalise and explain ``features``, streaming the evidence.

    Emits ``STEP_STARTED("ml_predict")`` → ``CUSTOM(conformal_interval)`` →
    ``CUSTOM(shap_explanation)`` → ``STEP_FINISHED("ml_predict")`` via the shared
    emitter, bracketing one call to the (possibly injected) model's
    ``predict_explain``.

    Args:
        features: ``feature name → value`` for a single prediction.
        emitter: The AG-UI emitter for streaming events.
        model: Optional model to serve the prediction; defaults to
            :func:`aegis.ml.get_model` (the process-wide singleton).

    Returns:
        The full :class:`~aegis.ml.types.MLExplainResponse`.
    """
    async with emitter.step(_STEP_NAME, SpanKind.CHAIN):
        resp = (model or get_model()).predict_explain(features)

        lower, upper = resp.conformal_interval if resp.conformal_interval else (None, None)
        await emitter.custom(
            stream_names.CONFORMAL_INTERVAL,
            {
                "prediction": resp.prediction,
                "lower": lower,
                "upper": upper,
                "confidence": resp.conformal_confidence,
                "interval_width": resp.interval_width,
                "prediction_set_size": resp.prediction_set_size,
            },
        )
        await emitter.custom(
            stream_names.SHAP_EXPLANATION,
            {
                "prediction": resp.prediction,
                "features": [
                    {"feature": f.feature, "value": f.value, "contribution": f.contribution}
                    for f in resp.shap_attribution
                ],
            },
        )
    return resp
