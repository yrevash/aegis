"""Tests for streaming the gateway's per-call `model_call` event."""

from __future__ import annotations

import json
import sys

import pytest

from aegis.core import stream_names
from aegis.core.models import ModelRole
from aegis.core.stream import AegisEmitter
from aegis.gateway.stream import stream_complete

from .test_llm import FakeLiteLLM, _make_response


class CaptureSink:
    """Sink that captures encoded event frames."""

    def __init__(self) -> None:
        """Initialize the capture sink."""
        self.frames: list[str] = []

    async def __call__(self, frame: str) -> None:
        """Capture one encoded SSE frame."""
        self.frames.append(frame)


def _payloads(frames: list[str]) -> list[dict]:
    """Extract JSON payloads from SSE frames."""
    return [json.loads(f[len("data: ") :].strip()) for f in frames]


@pytest.fixture
def fake_litellm(monkeypatch):
    fake = FakeLiteLLM(response=_make_response(content="hi", model="genailab-maas-gpt-4o-mini"))
    monkeypatch.setitem(sys.modules, "litellm", fake)
    return fake


async def test_stream_complete_emits_step_then_model_call_then_finished(fake_litellm):
    sink = CaptureSink()
    emitter = AegisEmitter(thread_id="t", run_id="r", sink=sink)

    result = await stream_complete(
        ModelRole.CHEAP, [{"role": "user", "content": "hi"}], emitter
    )

    payloads = _payloads(sink.frames)
    assert [p["type"] for p in payloads] == ["STEP_STARTED", "CUSTOM", "STEP_FINISHED"]
    assert payloads[0]["stepName"] == "llm"
    assert payloads[2]["stepName"] == "llm"

    event = payloads[1]
    assert event["name"] == stream_names.MODEL_CALL
    value = event["value"]
    assert value["model"] == result.model
    assert value["role"] == "cheap"
    assert value["prompt_tokens"] == result.usage.prompt_tokens
    assert value["completion_tokens"] == result.usage.completion_tokens
    assert value["cost_usd"] == result.usage.cost_usd
    assert value["cost_saved_usd"] >= 0.0
    assert value["small_model"] is True  # "genailab-maas-gpt-4o-mini" -> small
    # The served model equals the role's primary → no fallback fired.
    assert value["primary_model"] == "genailab-maas-gpt-4o-mini"
    assert value["fallback_fired"] is False


async def test_stream_complete_flags_fallback_when_served_differs(monkeypatch):
    """When the responding deployment differs from the role's primary, the event
    reports a fallback fired — measured from the real ``response.model``."""
    # role=CHEAP has primary "…gpt-4o-mini"; the gateway responds on the frontier
    # deployment, i.e. LiteLLM fell back to a different tier.
    fake = FakeLiteLLM(response=_make_response(content="hi", model="genailab-maas-gpt-4o"))
    monkeypatch.setitem(sys.modules, "litellm", fake)

    sink = CaptureSink()
    emitter = AegisEmitter(thread_id="t", run_id="r", sink=sink)

    await stream_complete(ModelRole.CHEAP, [{"role": "user", "content": "hi"}], emitter)

    value = _payloads(sink.frames)[1]["value"]
    assert value["primary_model"] == "genailab-maas-gpt-4o-mini"
    assert value["model"] == "genailab-maas-gpt-4o"
    assert value["fallback_fired"] is True


async def test_stream_complete_returns_the_full_result(fake_litellm):
    sink = CaptureSink()
    emitter = AegisEmitter(thread_id="t", run_id="r", sink=sink)

    result = await stream_complete(
        ModelRole.GENERATION, [{"role": "user", "content": "hi"}], emitter
    )

    assert result.content == "hi"


async def test_stream_complete_forwards_kwargs(fake_litellm):
    sink = CaptureSink()
    emitter = AegisEmitter(thread_id="t", run_id="r", sink=sink)

    await stream_complete(
        ModelRole.CHEAP,
        [{"role": "user", "content": "hi"}],
        emitter,
        max_tokens=256,
    )

    assert fake_litellm.calls[0]["max_tokens"] == 256


async def test_stream_complete_large_model_reports_no_saving(monkeypatch):
    """A GENERATION-role call has no small-model saving (baseline == self)."""
    fake = FakeLiteLLM(
        response=_make_response(content="hi", model="genailab-maas-gpt-4o")
    )
    monkeypatch.setitem(sys.modules, "litellm", fake)

    sink = CaptureSink()
    emitter = AegisEmitter(thread_id="t", run_id="r", sink=sink)

    await stream_complete(
        ModelRole.GENERATION, [{"role": "user", "content": "hi"}], emitter
    )

    event = _payloads(sink.frames)[1]
    assert event["value"]["small_model"] is False
