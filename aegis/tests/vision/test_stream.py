"""AG-UI streaming for aegis.vision — registered names and honest payloads."""

from __future__ import annotations

from aegis.core import stream_names
from aegis.core.events import SpanKind
from aegis.vision import (
    ScreenVerdict,
    analyse_image,
    analysis_payload,
    emit_analysis,
    emit_screen_verdict,
    screen_payload,
)

from .conftest import FakeScreen, RecordingAnalyst, png_payload


class _FakeStepScope:
    def __init__(self, log: list, name: str, kind: SpanKind) -> None:
        self._log, self._name, self._kind = log, name, kind

    async def __aenter__(self) -> _FakeStepScope:
        self._log.append(("step_start", self._name, self._kind))
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._log.append(("step_end", self._name, self._kind))


class _FakeEmitter:
    """Captures emitter calls without a real AG-UI transport."""

    def __init__(self) -> None:
        self.log: list = []

    def step(self, name: str, kind: SpanKind) -> _FakeStepScope:
        return _FakeStepScope(self.log, name, kind)

    async def custom(self, name: str, value: dict) -> None:
        self.log.append(("custom", name, value))


async def test_vision_stream_names_are_registered():
    """Both names are in the canonical registry, so `emitter.custom` will accept them."""
    assert stream_names.VISION_SCREEN == "vision_screen"
    assert stream_names.VISION_ANALYSIS == "vision_analysis"
    assert stream_names.is_known(stream_names.VISION_SCREEN)
    assert stream_names.is_known(stream_names.VISION_ANALYSIS)


async def test_screen_verdict_streams_as_a_guardrail_step():
    """The screen verdict is a GUARDRAIL span — the same rail the text verdicts use."""
    emitter = _FakeEmitter()
    await emit_screen_verdict(
        emitter, ScreenVerdict(injection=True, contains_text=True, reason="rendered SYSTEM: text")
    )
    assert [row[0] for row in emitter.log] == ["step_start", "custom", "step_end"]
    assert emitter.log[0][1:] == ("vision_screen", SpanKind.GUARDRAIL)
    assert emitter.log[1][1] == stream_names.VISION_SCREEN
    assert emitter.log[1][2]["injection"] is True


async def test_fail_closed_screen_payload_carries_screened_false():
    """A payload that lost `screened` would let a UI render a fail-closed block as clean."""
    payload = screen_payload(
        ScreenVerdict(injection=True, screened=False, reason="no completer configured")
    )
    assert payload["screened"] is False and payload["injection"] is True


async def test_analysis_payload_carries_controls_cost_and_coverage():
    """The wire payload is the whole audit record, not a summary of it."""
    analyst = RecordingAnalyst()
    result = await analyse_image(
        png_payload(),
        "What is this?",
        screen_completer=FakeScreen(injection=False),
        analyst=analyst,
    )
    payload = analysis_payload(result)
    assert payload["outcome"] == "answered"
    assert [c["stage"] for c in payload["controls"]] == [
        "hygiene",
        "injection_screen",
        "image_pii",
        "vision_model",
        "output_rails",
    ]
    assert payload["usage"]["cost_usd"] > 0
    assert "Did NOT run" in payload["coverage"]
    assert payload["screen"]["screened"] is True


async def test_emit_analysis_returns_the_analysis_for_chaining():
    """`emit_analysis` streams and forwards, so a caller can do both in one expression."""
    emitter = _FakeEmitter()
    result = await analyse_image(
        png_payload(),
        "",
        screen_completer=FakeScreen(injection=False),
        analyst=RecordingAnalyst(),
    )
    assert await emit_analysis(emitter, result) is result
    assert emitter.log[0][2] is SpanKind.LLM
    assert emitter.log[1][1] == stream_names.VISION_ANALYSIS
