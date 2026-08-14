"""Tests for streaming the gateway's per-call `model_call` event."""

from __future__ import annotations

import asyncio
import json
import sys

import pytest

import aegis.gateway.llm as llm_mod
from aegis.core import stream_names
from aegis.core.models import ModelRole
from aegis.core.stream import AegisEmitter
from aegis.gateway.llm import call_saving_usd
from aegis.gateway.stream import stream_complete
from aegis.gateway.types import Usage

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


# ── Per-call savings are exact under concurrency ────────────────────────────
#
# ``cost_saved_usd`` used to be a before/after delta over the process-global
# ``usage_tally()`` taken across the ``await`` of ``complete``. Two concurrent
# ``stream_complete`` calls therefore attributed each other's savings: whichever
# finished second saw the first one's spend inside its own "after" snapshot.


async def test_per_call_saving_matches_the_calls_own_usage(fake_litellm, monkeypatch):
    # The custom deployment is unmapped, so the cheap call is priced from its own
    # tokens — the case where routing to a small model really did save money.
    monkeypatch.setattr(fake_litellm, "completion_cost", lambda *, completion_response: 0.0)
    sink = CaptureSink()
    emitter = AegisEmitter(thread_id="t", run_id="r", sink=sink)

    result = await stream_complete(
        ModelRole.CHEAP, [{"role": "user", "content": "hi"}], emitter
    )

    value = _payloads(sink.frames)[1]["value"]
    assert value["cost_saved_usd"] == pytest.approx(call_saving_usd(result.usage))
    assert value["cost_saved_usd"] > 0.0


async def test_concurrent_calls_do_not_attribute_each_others_savings(monkeypatch):
    """Two interleaved small-model calls each report their OWN saving.

    The fake yields control mid-call so the two ``complete`` calls genuinely
    interleave; with the old global-delta approach the second call to finish
    absorbed the first's saving and the first reported ~zero.
    """
    fake = FakeLiteLLM(
        response=_make_response(content="hi", model="genailab-maas-gpt-4o-mini"), cost=0.0
    )

    async def _interleaving(**kwargs):
        fake.calls.append(kwargs)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return fake._response

    monkeypatch.setattr(fake, "acompletion", _interleaving)
    monkeypatch.setitem(sys.modules, "litellm", fake)

    sinks = [CaptureSink(), CaptureSink()]
    results = await asyncio.gather(
        *(
            stream_complete(
                ModelRole.CHEAP,
                [{"role": "user", "content": "hi"}],
                AegisEmitter(thread_id=f"t{i}", run_id=f"r{i}", sink=sink),
            )
            for i, sink in enumerate(sinks)
        )
    )

    savings = [_payloads(s.frames)[1]["value"]["cost_saved_usd"] for s in sinks]
    expected = call_saving_usd(results[0].usage)
    assert expected > 0.0
    # Identical calls → identical savings, and neither is zero or doubled.
    assert savings[0] == pytest.approx(expected)
    assert savings[1] == pytest.approx(expected)


def test_call_saving_is_zero_when_the_call_is_the_baseline():
    """A frontier-priced call has nothing to save against itself."""
    usage = Usage(prompt_tokens=1000, completion_tokens=1000, cost_usd=0.0125)
    assert call_saving_usd(usage) == pytest.approx(0.0)


def test_call_saving_never_goes_negative():
    """A call dearer than the baseline reports zero, never a negative saving."""
    usage = Usage(prompt_tokens=10, completion_tokens=10, cost_usd=99.0)
    assert call_saving_usd(usage) == 0.0


def test_call_saving_reads_no_shared_state():
    """The figure is derived from ``Usage`` alone — the tally cannot perturb it."""
    usage = Usage(prompt_tokens=1000, completion_tokens=1000, cost_usd=0.0002)
    before = call_saving_usd(usage)
    llm_mod.record_call(
        "genailab-maas-gpt-4o", 5.0, prompt_tokens=999_999, role=ModelRole.GENERATION
    )
    assert call_saving_usd(usage) == pytest.approx(before)
