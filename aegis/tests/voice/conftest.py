"""Shared fixtures for the aegis.voice tests — no network, no litellm, no codec.

The gateway credential in this repo is a placeholder, so every test here drives a
**fake** transcriber matching the ``aegis.gateway.transcribe`` signature. Nothing
in this directory is verified against the live ``azure/genailab-maas-whisper``
deployment.
"""

from __future__ import annotations

import array
import io
import math
import wave

import pytest

from aegis.gateway.types import TranscriptionResult, TranscriptionSegment, Usage
from aegis.media import AudioPayload, MediaSource, Provenance


def make_wav(
    seconds: float = 3.0,
    *,
    rate: int = 8000,
    pause_every: float = 10.0,
    pause_len: float = 1.5,
    sampwidth: int = 2,
    channels: int = 1,
) -> bytes:
    """Build an uncompressed PCM WAV of a tone punctuated by silent pauses.

    Args:
        seconds: Total duration.
        rate: Sample rate.
        pause_every: Period of the speech/pause cycle.
        pause_len: Silent tail of each cycle.
        sampwidth: Bytes per sample.
        channels: Channel count (the same signal is written to each).

    Returns:
        The WAV file bytes.
    """
    frames = int(seconds * rate)
    buf = array.array("h")
    for i in range(frames):
        t = i / rate
        quiet = (t % pause_every) > (pause_every - pause_len)
        amp = 0 if quiet else int(8000 * math.sin(2 * math.pi * 220 * t))
        for _ in range(channels):
            buf.append(amp)
    raw = buf.tobytes()
    if sampwidth == 1:
        raw = bytes(128 + (s >> 8) for s in buf)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sampwidth)
        w.setframerate(rate)
        w.writeframes(raw)
    return out.getvalue()


def payload(data: bytes | None = None, *, mime_type: str = "audio/wav") -> AudioPayload:
    """Build an ``AudioPayload`` around ``data`` (a short WAV by default)."""
    return AudioPayload(
        data=make_wav() if data is None else data,
        mime_type=mime_type,
        provenance=Provenance(source=MediaSource.USER_UPLOAD, origin="note.wav"),
    )


class FakeTranscriber:
    """A stand-in for ``aegis.gateway.transcribe``.

    Records every call (including the bytes it was handed and the filename on the
    handle) and returns a scripted transcript per chunk.
    """

    def __init__(
        self,
        texts: list[str] | None = None,
        *,
        language: str | None = "en",
        segments: bool = True,
        raises: Exception | None = None,
    ) -> None:
        """Configure the fake.

        Args:
            texts: One transcript per call, cycled if exhausted.
            language: Language to report.
            segments: Whether to report time-aligned segments.
            raises: If set, every call raises this instead of answering.
        """
        self.texts = texts or ["hello there"]
        self.language = language
        self.segments = segments
        self.raises = raises
        self.calls: list[dict] = []

    async def __call__(
        self,
        audio: object,
        *,
        language: str | None = None,
        prompt: str | None = None,
        response_format: str = "verbose_json",
        duration_seconds: float | None = None,
    ) -> TranscriptionResult:
        """Answer one transcription call, recording exactly what it was given."""
        data = audio.read() if hasattr(audio, "read") else b""
        self.calls.append(
            {
                "bytes": data,
                "name": getattr(audio, "name", None),
                "language": language,
                "prompt": prompt,
                "response_format": response_format,
                "duration_seconds": duration_seconds,
            }
        )
        if self.raises is not None:
            raise self.raises
        text = self.texts[min(len(self.calls) - 1, len(self.texts) - 1)]
        dur = duration_seconds if duration_seconds is not None else 3.0
        segs = (
            [TranscriptionSegment(id=0, start=0.0, end=dur, text=text)] if self.segments else []
        )
        return TranscriptionResult(
            text=text,
            language=self.language,
            duration_seconds=dur,
            segments=segs,
            usage=Usage(cost_usd=0.0001, audio_seconds=dur),
            model="genailab-maas-whisper",
        )


@pytest.fixture
def fake_transcriber() -> FakeTranscriber:
    """A fake gateway transcriber returning one scripted transcript."""
    return FakeTranscriber()
