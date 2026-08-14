"""Unit tests for ``aegis.voice.transcribe`` — the gateway-driving layer.

Everything is asserted against an injected fake matching the gateway's
``transcribe`` signature. The live fleet deployment is never called.
"""

from __future__ import annotations

import pytest

from aegis.core.types import GuardResult, GuardVerdict
from aegis.media import AudioPayload, MediaLimits
from aegis.voice import (
    AudioRejected,
    ChunkPolicy,
    make_transcriber,
    transcribe_and_guard,
    transcribe_audio,
)

from .conftest import FakeTranscriber, make_wav, payload


async def _pass(text: str) -> GuardResult:
    """A text rail stack that clears everything."""
    return GuardResult(verdict=GuardVerdict.PASS, reason="clean", text=text, layer="pipeline")


# ── Transcription ───────────────────────────────────────────────────────────


async def test_transcribes_a_short_clip_in_one_request(fake_transcriber):
    """A clip inside the ceiling is one hosted call, and the result is typed."""
    result = await transcribe_audio(payload(), transcriber=fake_transcriber)

    assert len(fake_transcriber.calls) == 1
    assert result.text == "hello there"
    assert result.language == "en"
    assert result.model == "genailab-maas-whisper"
    assert result.chunk_count == 1
    assert result.duration_seconds == pytest.approx(3.0)


async def test_sends_a_named_handle_so_the_provider_knows_the_container(fake_transcriber):
    """The upload carries a filename with the right extension, not a bare buffer."""
    await transcribe_audio(payload(), transcriber=fake_transcriber)

    call = fake_transcriber.calls[0]
    assert call["name"] == "chunk-000.wav"
    assert call["bytes"].startswith(b"RIFF")
    assert call["response_format"] == "verbose_json"


async def test_long_audio_is_chunked_and_stitched_onto_one_timeline():
    """Chunk transcripts join in order and segment timestamps are rebased."""
    fake = FakeTranscriber(texts=["one", "two", "three", "four"])
    result = await transcribe_audio(
        payload(make_wav(seconds=200.0)),
        transcriber=fake,
        policy=ChunkPolicy(max_chunk_seconds=60.0),
    )

    assert len(fake.calls) == result.chunk_count > 1
    assert result.text.startswith("one two")
    assert [s.index for s in result.segments] == list(range(len(result.segments)))
    # Segment starts are on the recording's timeline, so they only ever increase.
    starts = [s.start for s in result.segments]
    assert starts == sorted(starts)
    assert starts[-1] > 60.0


async def test_cost_and_billed_seconds_accumulate_across_chunks():
    """Chunking must not lose the ledger — every chunk's spend is summed."""
    fake = FakeTranscriber()
    result = await transcribe_audio(
        payload(make_wav(seconds=200.0)),
        transcriber=fake,
        policy=ChunkPolicy(max_chunk_seconds=60.0),
    )

    assert result.cost_usd == pytest.approx(0.0001 * result.chunk_count)
    assert result.audio_seconds_billed == pytest.approx(200.0, abs=1.0)


async def test_language_hint_is_forwarded_and_omitted_when_absent(fake_transcriber):
    """`language=None` means auto-detect; the hint is passed straight through."""
    await transcribe_audio(payload(), transcriber=fake_transcriber, language="hi")
    assert fake_transcriber.calls[0]["language"] == "hi"

    await transcribe_audio(payload(), transcriber=fake_transcriber)
    assert fake_transcriber.calls[1]["language"] is None


async def test_confidence_is_never_invented(fake_transcriber):
    """The gateway carries no per-segment confidence, so every segment reports None."""
    result = await transcribe_audio(payload(), transcriber=fake_transcriber)

    assert result.segments
    assert all(s.confidence is None for s in result.segments)
    assert result.has_confidence is False


async def test_hygiene_refuses_before_a_single_request_is_made():
    """A payload whose declared type is a lie costs nothing — no call is placed."""
    fake = FakeTranscriber()
    lying = AudioPayload(data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, mime_type="audio/wav")

    with pytest.raises(AudioRejected):
        await transcribe_audio(lying, transcriber=fake)
    assert fake.calls == []


async def test_size_cap_is_enforced_before_spend():
    """An oversize payload is refused by hygiene, not paid for."""
    fake = FakeTranscriber()
    big = payload(make_wav(seconds=30.0))

    with pytest.raises(AudioRejected):
        await transcribe_audio(big, transcriber=fake, limits=MediaLimits(max_bytes=1024))
    assert fake.calls == []


# ── The MediaScreen wiring seam ─────────────────────────────────────────────


async def test_make_transcriber_returns_the_transcript_text(fake_transcriber):
    """The adapter the media rail chain wants is an AudioPayload -> str callable."""
    transcriber = make_transcriber(transcriber=fake_transcriber)

    assert await transcriber(payload()) == "hello there"


async def test_make_transcriber_raises_rather_than_returning_an_empty_string():
    """A silent '' would sail through the text rails; a raise fails closed instead."""
    transcriber = make_transcriber(transcriber=FakeTranscriber(raises=RuntimeError("upstream")))

    with pytest.raises(RuntimeError):
        await transcriber(payload())


# ── The guarded path ────────────────────────────────────────────────────────


async def test_guarded_result_reports_which_controls_ran(fake_transcriber):
    """The verdict reason is generated from the coverage lists, so it cannot lie."""
    result = await transcribe_and_guard(
        payload(), text_check=_pass, transcriber=fake_transcriber
    )

    assert result.guard.verdict is GuardVerdict.PASS
    assert "hosted transcription" in " ".join(result.controls_run)
    assert "full text rail stack over the transcript" in result.controls_run
    assert any("diarisation" in s for s in result.controls_skipped)
    assert "Controls run:" in result.guard.reason


async def test_a_redacting_rail_hands_the_agent_the_redacted_text(fake_transcriber):
    """The agent gets what the rails returned, never the raw transcript."""

    async def redact(text: str) -> GuardResult:
        return GuardResult(
            verdict=GuardVerdict.REDACT,
            reason="masked an email",
            text=text.replace("hello", "[REDACTED]"),
            layer="pii",
            redactions=["email"],
        )

    result = await transcribe_and_guard(
        payload(), text_check=redact, transcriber=fake_transcriber
    )

    assert result.transcription is not None
    assert result.transcription.text == "hello there"
    assert result.agent_input == "[REDACTED] there"
    assert result.agent_input != result.transcription.text
