"""Tests for the AG-UI stream surface of aegis.voice."""

from __future__ import annotations

import json

from aegis.core import stream_names
from aegis.core.stream import AegisEmitter
from aegis.core.types import GuardResult, GuardVerdict
from aegis.voice import ChunkPolicy
from aegis.voice.stream import stream_transcribe_and_guard

from .conftest import FakeTranscriber, make_wav, payload


class CaptureSink:
    """Sink that captures encoded event frames."""

    def __init__(self) -> None:
        """Start with no frames."""
        self.frames: list[str] = []

    async def __call__(self, frame: str) -> None:
        """Capture one encoded frame.

        Args:
            frame: The encoded SSE frame.
        """
        self.frames.append(frame)


def _payloads(frames: list[str]) -> list[dict]:
    """Extract the JSON payload of every captured frame."""
    return [json.loads(f[len("data: ") :].strip()) for f in frames]


async def _pass(text: str) -> GuardResult:
    """A text rail stack that clears everything."""
    return GuardResult(verdict=GuardVerdict.PASS, reason="clean", text=text, layer="pipeline")


async def _block(_text: str) -> GuardResult:
    """A text rail stack that refuses everything."""
    return GuardResult(verdict=GuardVerdict.BLOCK, reason="nope", text="", layer="injection")


def test_the_voice_event_names_are_registered():
    """A CustomEvent name the emitter does not know raises — these must be known."""
    assert stream_names.is_known(stream_names.VOICE_TRANSCRIPT)
    assert stream_names.is_known(stream_names.VOICE_CHUNK)


async def test_stream_emits_step_chunks_transcript_then_verdict():
    """The console sees the work arrive in order, bracketed by the step."""
    sink = CaptureSink()
    emitter = AegisEmitter(thread_id="t", run_id="r", sink=sink)

    await stream_transcribe_and_guard(
        payload(make_wav(seconds=200.0)),
        emitter,
        text_check=_pass,
        transcriber=FakeTranscriber(texts=["one", "two", "three", "four"]),
        policy=ChunkPolicy(max_chunk_seconds=60.0),
    )

    events = _payloads(sink.frames)
    kinds = [e.get("name") or e["type"] for e in events]
    assert kinds[0] == "STEP_STARTED"
    assert kinds[-1] == "STEP_FINISHED"
    assert kinds.count(stream_names.VOICE_CHUNK) > 1
    assert kinds.index(stream_names.VOICE_TRANSCRIPT) < kinds.index(stream_names.GUARDRAIL_MEDIA)


async def test_transcript_event_never_implies_a_confidence_nobody_reported():
    """`hasConfidence` is false and every segment confidence is null."""
    sink = CaptureSink()
    emitter = AegisEmitter(thread_id="t", run_id="r", sink=sink)

    await stream_transcribe_and_guard(
        payload(), emitter, text_check=_pass, transcriber=FakeTranscriber()
    )

    transcript = next(
        e for e in _payloads(sink.frames) if e.get("name") == stream_names.VOICE_TRANSCRIPT
    )
    assert transcript["value"]["hasConfidence"] is False
    assert all(s["confidence"] is None for s in transcript["value"]["segments"])


async def test_a_blocked_run_still_emits_its_verdict():
    """A refused recording is visible in the stream, not a stream that simply stops."""
    sink = CaptureSink()
    emitter = AegisEmitter(thread_id="t", run_id="r", sink=sink)

    result = await stream_transcribe_and_guard(
        payload(), emitter, text_check=_block, transcriber=FakeTranscriber()
    )

    verdict = next(
        e for e in _payloads(sink.frames) if e.get("name") == stream_names.GUARDRAIL_MEDIA
    )
    assert verdict["value"]["verdict"] == "block"
    assert verdict["value"]["agentReady"] is False
    assert result.agent_input is None
