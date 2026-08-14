"""AG-UI streaming for the ML spine — emits its work à la carte over the emitter.

Two flows, both à la carte over the shared emitter so the MLOps UI can render each
piece as soon as it is ready:

* :func:`stream_model_card` — announces *which* model is serving, streaming its
  honest measured metadata (ensemble members, target/features, conformal coverage,
  calibration/training split sizes, data provenance) as one ``ML_MODEL`` event.
* :func:`stream_predict_explain` — wraps a single ``predict_explain`` call in a
  ``STEP_STARTED``/``STEP_FINISHED`` bracket and, within it, emits the calibrated
  conformal interval (or classification set size) followed by the SHAP attribution
  as two ``CUSTOM`` events; it can optionally lead with the model card.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aegis.core import stream_names
from aegis.core.events import SpanKind
from aegis.ml import get_model

if TYPE_CHECKING:
    from aegis.core.stream import AegisEmitter
    from aegis.ml.model import TrustworthyModel
    from aegis.ml.types import MLExplainResponse, ModelCard

_STEP_NAME = "ml_predict"
_MODEL_STEP_NAME = "ml_model"


async def stream_model_card(
    emitter: AegisEmitter,
    *,
    model: TrustworthyModel | None = None,
) -> ModelCard:
    """Emit the serving model's honest metadata as one ``ML_MODEL`` event.

    Brackets a ``STEP_STARTED("ml_model")`` / ``STEP_FINISHED`` and, within it,
    emits ``CUSTOM(ml_model)`` carrying the measured :class:`ModelCard` — the event
    the MLOps UI consumes to show *which* model (real domain-trained vs synthetic
    fallback) is loaded and what guarantees it carries.

    Args:
        emitter: The AG-UI emitter for streaming events.
        model: Optional model to describe; defaults to :func:`aegis.ml.get_model`.

    Returns:
        The :class:`~aegis.ml.types.ModelCard` that was streamed.
    """
    card = (model or get_model()).model_card()
    async with emitter.step(_MODEL_STEP_NAME, SpanKind.CHAIN):
        await emitter.custom(stream_names.ML_MODEL, card.model_dump())
    return card


async def stream_predict_explain(
    features: dict[str, Any],
    emitter: AegisEmitter,
    *,
    model: TrustworthyModel | None = None,
    emit_model_card: bool = False,
) -> MLExplainResponse:
    """Predict, conformalise and explain ``features``, streaming the evidence.

    Emits (optional ``ML_MODEL`` lead-in →) ``STEP_STARTED("ml_predict")`` →
    ``CUSTOM(conformal_interval)`` → ``CUSTOM(shap_explanation)`` →
    ``STEP_FINISHED("ml_predict")`` via the shared emitter, bracketing one call to
    the (possibly injected) model's ``predict_explain``.

    Args:
        features: ``feature name → value`` for a single prediction.
        emitter: The AG-UI emitter for streaming events.
        model: Optional model to serve the prediction; defaults to
            :func:`aegis.ml.get_model` (the process-wide singleton).
        emit_model_card: When ``True``, lead with a :func:`stream_model_card`
            ``ML_MODEL`` event so the UI knows which model produced the prediction.

    Returns:
        The full :class:`~aegis.ml.types.MLExplainResponse`.
    """
    served = model or get_model()
    if emit_model_card:
        await stream_model_card(emitter, model=served)
    async with emitter.step(_STEP_NAME, SpanKind.CHAIN):
        resp = served.predict_explain(features)

        lower, upper = resp.conformal_interval if resp.conformal_interval else (None, None)
        await emitter.custom(
            stream_names.CONFORMAL_INTERVAL,
            {
                "prediction": resp.prediction,
                "lower": lower,
                "upper": upper,
                # `confidence` is the level that was REQUESTED, not one measured on
                # this row; the achieved rate lives on the model card.
                "confidence": resp.conformal_confidence,
                "interval_width": resp.interval_width,
                "prediction_set_size": resp.prediction_set_size,
                # Honesty signals travel with the number so the UI can discount it.
                "data_source": resp.data_source,
                "imputed_features": list(resp.imputed_features),
                "unknown_features": list(resp.unknown_features),
            },
        )
        await emitter.custom(
            stream_names.SHAP_EXPLANATION,
            {
                "prediction": resp.prediction,
                "features": [
                    {
                        "feature": f.feature,
                        "value": f.value,
                        "value_label": f.value_label,
                        "contribution": f.contribution,
                    }
                    for f in resp.shap_attribution
                ],
            },
        )
    return resp
