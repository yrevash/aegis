"""Tests for streaming ML prediction + explanation via the AG-UI emitter."""

from __future__ import annotations

import json

import pytest

from aegis.core import stream_names
from aegis.core.stream import AegisEmitter
from aegis.ml.model import TrustworthyModel
from aegis.ml.stream import stream_model_card, stream_predict_explain


class CaptureSink:
    """Sink that captures encoded event frames."""

    def __init__(self) -> None:
        """Initialize the capture sink."""
        self.frames: list[str] = []

    async def __call__(self, frame: str) -> None:
        """Capture a frame.

        Args:
            frame: The encoded event frame.
        """
        self.frames.append(frame)


def _payloads(frames: list[str]) -> list[dict]:
    """Extract JSON payloads from SSE frames.

    Args:
        frames: List of encoded event frames.

    Returns:
        List of parsed JSON payload dictionaries.
    """
    return [json.loads(f[len("data: "):].strip()) for f in frames]


@pytest.mark.asyncio
async def test_stream_predict_explain_emits_step_then_interval_then_shap(
    regression_spec, regression_frame
):
    """STEP_STARTED -> CUSTOM(conformal_interval) -> CUSTOM(shap_explanation) -> STEP_FINISHED."""
    model = TrustworthyModel.train(
        regression_spec, regression_frame, confidence_level=0.9, path=None
    )
    sink = CaptureSink()
    em = AegisEmitter(thread_id="t", run_id="r", sink=sink)

    resp = await stream_predict_explain(
        {"f0": 1.0, "f1": -1.0, "f2": 0.5}, em, model=model
    )

    payloads = _payloads(sink.frames)
    assert [p["type"] for p in payloads] == [
        "STEP_STARTED",
        "CUSTOM",
        "CUSTOM",
        "STEP_FINISHED",
    ]

    interval_event = payloads[1]
    assert interval_event["name"] == stream_names.CONFORMAL_INTERVAL
    interval_value = interval_event["value"]
    assert interval_value["prediction"] == resp.prediction
    assert interval_value["lower"] <= interval_value["upper"]
    assert interval_value["confidence"] == 0.9
    assert interval_value["interval_width"] is not None
    assert interval_value["prediction_set_size"] is None
    # The honesty signals ride with the number the UI renders.
    assert interval_value["data_source"] == "provided"
    assert interval_value["imputed_features"] == []
    assert interval_value["unknown_features"] == []

    shap_event = payloads[2]
    assert shap_event["name"] == stream_names.SHAP_EXPLANATION
    shap_value = shap_event["value"]
    assert shap_value["prediction"] == resp.prediction
    assert {f["feature"] for f in shap_value["features"]} == set(regression_spec.features)
    for f in shap_value["features"]:
        assert set(f) == {"feature", "value", "value_label", "contribution"}

    assert resp.conformal_interval is not None


@pytest.mark.asyncio
async def test_stream_predict_explain_classification_has_no_interval(
    classification_spec, classification_frame
):
    """Classification: prediction_set_size is surfaced, lower/upper stay None."""
    model = TrustworthyModel.train(
        classification_spec, classification_frame, confidence_level=0.9, path=None
    )
    sink = CaptureSink()
    em = AegisEmitter(thread_id="t", run_id="r", sink=sink)

    await stream_predict_explain({"f0": 1.5, "f1": -1.2, "f2": 0.3}, em, model=model)

    interval_value = _payloads(sink.frames)[1]["value"]
    assert interval_value["lower"] is None
    assert interval_value["upper"] is None
    assert interval_value["prediction_set_size"] is not None


@pytest.mark.asyncio
async def test_stream_model_card_emits_ml_model_event(regression_spec, regression_frame):
    """STEP_STARTED('ml_model') -> CUSTOM(ml_model, card) -> STEP_FINISHED."""
    model = TrustworthyModel.train(regression_spec, regression_frame, path=None)
    sink = CaptureSink()
    em = AegisEmitter(thread_id="t", run_id="r", sink=sink)

    card = await stream_model_card(em, model=model)

    payloads = _payloads(sink.frames)
    assert [p["type"] for p in payloads] == ["STEP_STARTED", "CUSTOM", "STEP_FINISHED"]

    event = payloads[1]
    assert event["name"] == stream_names.ML_MODEL
    value = event["value"]
    # The streamed payload is the measured card verbatim.
    assert value == card.model_dump()
    assert value["target"] == "y"
    assert value["conformal_coverage"] == 0.9
    assert {m["name"] for m in value["ensemble_members"]} == {"xgboost", "hist_gbr"}
    assert value["data_source"] == "provided"
    # The measured half of the coverage story is streamed alongside the request.
    assert value["conformal_coverage_empirical"] is not None
    assert value["metric_name"] == "r2"


@pytest.mark.asyncio
async def test_stream_predict_explain_can_lead_with_model_card(
    regression_spec, regression_frame
):
    """emit_model_card=True prepends the ML_MODEL bracket before the predict step."""
    model = TrustworthyModel.train(regression_spec, regression_frame, path=None)
    sink = CaptureSink()
    em = AegisEmitter(thread_id="t", run_id="r", sink=sink)

    await stream_predict_explain(
        {"f0": 1.0, "f1": -1.0, "f2": 0.5}, em, model=model, emit_model_card=True
    )

    types = [p["type"] for p in _payloads(sink.frames)]
    assert types == [
        "STEP_STARTED",  # ml_model
        "CUSTOM",  # ml_model card
        "STEP_FINISHED",
        "STEP_STARTED",  # ml_predict
        "CUSTOM",  # conformal_interval
        "CUSTOM",  # shap_explanation
        "STEP_FINISHED",
    ]
    names = [p["name"] for p in _payloads(sink.frames) if p["type"] == "CUSTOM"]
    assert names == [
        stream_names.ML_MODEL,
        stream_names.CONFORMAL_INTERVAL,
        stream_names.SHAP_EXPLANATION,
    ]
