"""Tests for the silence-aware chunk splitter (pure stdlib, no codec)."""

from __future__ import annotations

import io
import wave

from aegis.media import AudioPayload
from aegis.voice import ChunkPolicy, plan_chunks

from .conftest import make_wav, payload


def test_short_recording_is_one_chunk_and_says_why():
    """Inside the ceiling there is nothing to split — and the note says so."""
    plan = plan_chunks(payload(make_wav(seconds=5.0)))

    assert len(plan.chunks) == 1
    assert plan.splittable is True
    assert "within the" in plan.note
    assert plan.duration_seconds == 5.0


def test_long_recording_splits_in_the_pauses():
    """A boundary lands inside a real pause, not mid-word."""
    data = make_wav(seconds=300.0, pause_every=10.0, pause_len=1.5)
    plan = plan_chunks(
        payload(data), policy=ChunkPolicy(max_chunk_seconds=60.0, search_seconds=15.0)
    )

    assert len(plan.chunks) > 1
    assert all(c.split_on_silence for c in plan.chunks[:-1]), plan.note
    # Every cut sits in the 8.5s-10s silent tail of its cycle.
    for chunk in plan.chunks[1:]:
        assert (chunk.start_seconds % 10.0) > 8.0


def test_chunks_tile_the_recording_without_gaps_or_overlap():
    """Concatenated chunk durations equal the recording; starts are contiguous."""
    data = make_wav(seconds=300.0)
    plan = plan_chunks(payload(data), policy=ChunkPolicy(max_chunk_seconds=60.0))

    total = sum(c.duration_seconds or 0.0 for c in plan.chunks)
    assert abs(total - 300.0) < 0.05
    cursor = 0.0
    for chunk in plan.chunks:
        assert abs(chunk.start_seconds - cursor) < 1e-6
        cursor += chunk.duration_seconds or 0.0


def test_each_chunk_is_a_standalone_playable_wav():
    """A chunk is a whole file, not a slice of one — the provider gets a real WAV."""
    plan = plan_chunks(
        payload(make_wav(seconds=200.0)), policy=ChunkPolicy(max_chunk_seconds=60.0)
    )

    for chunk in plan.chunks:
        with wave.open(io.BytesIO(chunk.data), "rb") as handle:
            assert handle.getframerate() == 8000
            assert handle.getnchannels() == 1
            assert handle.getnframes() > 0


def test_no_pause_in_range_cuts_on_time_and_admits_it():
    """Continuous speech cannot be cut politely — the plan says the cut was forced."""
    data = make_wav(seconds=200.0, pause_every=1000.0, pause_len=0.0)
    plan = plan_chunks(
        payload(data), policy=ChunkPolicy(max_chunk_seconds=60.0, search_seconds=10.0)
    )

    assert len(plan.chunks) > 1
    assert not plan.chunks[0].split_on_silence
    assert "cut on time" in plan.note


def test_stereo_and_8bit_wav_are_handled():
    """Sample formats the stdlib parser reads are split, not refused."""
    for sampwidth, channels in ((1, 1), (2, 2)):
        data = make_wav(seconds=200.0, sampwidth=sampwidth, channels=channels)
        plan = plan_chunks(payload(data), policy=ChunkPolicy(max_chunk_seconds=60.0))
        assert len(plan.chunks) > 1, (sampwidth, channels)
        assert plan.splittable is True


def test_undecodable_container_is_one_chunk_and_never_claims_otherwise():
    """No ffmpeg means no timeline for MP3/OGG — stated, not silently 'chunked'."""
    plan = plan_chunks(AudioPayload(data=b"ID3\x04\x00" + b"\x00" * 400, mime_type="audio/mpeg"))

    assert len(plan.chunks) == 1
    assert plan.splittable is False
    assert plan.duration_seconds is None
    assert "not uncompressed PCM WAV" in plan.note
    assert "no ffmpeg" in plan.note


def test_empty_payload_returns_one_chunk_not_a_crash():
    """Hygiene refuses empty audio upstream; the splitter still must not explode."""
    plan = plan_chunks(AudioPayload(data=b"x", mime_type="audio/wav"))

    assert len(plan.chunks) == 1
    assert plan.splittable is False
