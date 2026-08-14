"""Split a long recording into transcribable chunks — on silence, never mid-word.

**Why chunking exists at all.** A hosted speech-to-text deployment has a request
size/duration ceiling, and one 40-minute request is also one 40-minute failure
domain: a timeout loses the whole transcript. Splitting into bounded chunks makes
a lecture-length recording work, keeps each request's latency (and each request's
share of the budget) bounded, and turns a transient failure into a retryable
fraction of the job.

**Why on silence.** Cutting at a fixed offset lands mid-word roughly always, and
speech-to-text has no context across the cut, so the two halves of the word come
back as two wrong words. Cutting in a pause costs nothing. So the splitter aims
for a target boundary and then searches *backwards* from it for the quietest
stretch, cutting in the middle of it.

**Why this is pure stdlib.** Policy for this platform is fleet models only and no
new media toolchain — no ffmpeg, no torch, no local Whisper. So the splitter
works on what Python itself can parse: uncompressed PCM RIFF/WAVE, via
:mod:`wave` and :mod:`array`. That is a real limitation and it is reported rather
than hidden: a container this module cannot decode (MP3, OGG, FLAC, M4A) is
transcribed **whole**, in one request, and :attr:`ChunkPlan.note` says exactly
that. It never silently returns a "chunked" plan that is really one chunk.
"""

from __future__ import annotations

import array
import io
import sys
import wave
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from aegis.media import AudioPayload

#: Window used for the loudness envelope. 20 ms is the standard speech frame — long
#: enough to be a stable RMS estimate, short enough to locate a pause precisely.
_WINDOW_SECONDS = 0.02

#: Sample widths (bytes) this module can read. WAV also allows 3-byte samples,
#: which ``array`` has no type code for; those are reported unsupported, not guessed.
_TYPECODE: dict[int, str] = {1: "B", 2: "h", 4: "i"}


class ChunkPolicy(BaseModel):
    """Thresholds for the splitter.

    Attributes:
        max_chunk_seconds: Hard ceiling on one chunk. The default keeps a single
            request short enough that a stall is cheap to retry, and well inside
            any hosted deployment's own duration ceiling.
        min_chunk_seconds: A split is never made this close to the start of the
            current chunk — it stops a long silence from producing a stream of
            near-empty requests.
        search_seconds: How far back from the target boundary to hunt for a pause.
            Beyond this the splitter gives up and cuts on time (and says so).
        silence_ratio: A window counts as silence when its RMS is below this
            fraction of the whole clip's RMS. Relative, not absolute, because
            recording levels vary by two orders of magnitude between a headset
            and a laptop microphone.
        min_silence_seconds: A pause must last at least this long to be treated as
            a sentence boundary rather than a stop consonant.
    """

    model_config = ConfigDict(frozen=True)

    max_chunk_seconds: float = Field(default=120.0, gt=0)
    min_chunk_seconds: float = Field(default=5.0, gt=0)
    search_seconds: float = Field(default=20.0, gt=0)
    silence_ratio: float = Field(default=0.18, ge=0)
    min_silence_seconds: float = Field(default=0.25, gt=0)


class AudioChunk(BaseModel):
    """One transcribable slice of the recording.

    Attributes:
        index: 0-based position in the plan.
        start_seconds: Offset of this chunk within the whole recording — added
            back to every segment timestamp so the transcript's timeline is the
            recording's timeline, not the chunk's.
        duration_seconds: Length of this chunk, or ``None`` when unknown (the
            single-chunk plan for a container this module cannot parse).
        data: The chunk's bytes, as a self-contained file of ``mime_type``.
        mime_type: The chunk's container type.
        split_on_silence: Whether the *end* of this chunk landed in a detected
            pause. ``False`` means the splitter ran out of search window and cut
            on time — a caller can surface that a word may straddle the boundary.
    """

    model_config = ConfigDict(frozen=True)

    index: int
    start_seconds: float = 0.0
    duration_seconds: float | None = None
    data: bytes
    mime_type: str = "audio/wav"
    split_on_silence: bool = False


class ChunkPlan(BaseModel):
    """The result of :func:`plan_chunks`.

    Attributes:
        chunks: The chunks to transcribe, in order. Never empty.
        duration_seconds: Total duration read from the container header, or
            ``None`` when this module could not parse it.
        note: One honest line about what the splitter did and why — carried into
            :attr:`aegis.voice.types.VoiceTranscription.chunking` and shown in the
            console, so "one chunk" is never mistaken for "chunking worked".
        splittable: Whether this module could actually decode the container's
            timeline. ``False`` means the single chunk is the whole file.
    """

    model_config = ConfigDict(frozen=True)

    chunks: list[AudioChunk]
    duration_seconds: float | None = None
    note: str = ""
    splittable: bool = True


def _whole(payload: AudioPayload, note: str, *, duration: float | None = None) -> ChunkPlan:
    """Return the one-chunk plan (the file, untouched) with an honest ``note``."""
    return ChunkPlan(
        chunks=[
            AudioChunk(
                index=0,
                start_seconds=0.0,
                duration_seconds=duration,
                data=payload.data or b"",
                mime_type=payload.mime_type,
            )
        ],
        duration_seconds=duration,
        note=note,
        splittable=False,
    )


def _samples(frames: bytes, sampwidth: int) -> array.array:
    """Decode raw little-endian PCM ``frames`` into a signed sample array.

    Args:
        frames: Interleaved PCM frame bytes straight out of the WAV data chunk.
        sampwidth: Bytes per sample (1, 2 or 4).

    Returns:
        An :class:`array.array` of samples. 8-bit WAV is unsigned by definition,
        so it is returned as-is and its DC offset is removed by the RMS routine.
    """
    buf = array.array(_TYPECODE[sampwidth])
    buf.frombytes(frames)
    if sys.byteorder == "big":  # pragma: no cover - WAV is little-endian by spec
        buf.byteswap()
    return buf


def _envelope(samples: Sequence[float], per_window: int) -> list[float]:
    """Compute the mean-square loudness of each fixed-size window of ``samples``.

    Mean-square rather than RMS: the square root is monotonic, so it changes no
    comparison this module makes and costs a call per window.

    Args:
        samples: The decoded samples (interleaved channels are fine — a pause is
            a pause on every channel at once).
        per_window: Samples per window.

    Returns:
        One mean-square value per window, in order.
    """
    out: list[float] = []
    total = len(samples)
    for start in range(0, total, per_window):
        window = samples[start : start + per_window]
        if not window:
            break
        out.append(sum(float(s) * float(s) for s in window) / len(window))
    return out


def _quietest_run(
    envelope: Sequence[float], lo: int, hi: int, threshold: float, min_windows: int
) -> int | None:
    """Find the middle of the longest sub-threshold run inside ``envelope[lo:hi]``.

    Args:
        envelope: Per-window loudness.
        lo: First window index of the search band (inclusive).
        hi: Last window index of the search band (exclusive).
        threshold: A window at or below this counts as silence.
        min_windows: Minimum run length to accept as a real pause.

    Returns:
        The window index at the centre of the longest qualifying run, or ``None``
        when the band holds no pause long enough.
    """
    best_len = 0
    best_mid: int | None = None
    run_start: int | None = None
    for i in range(max(lo, 0), min(hi, len(envelope)) + 1):
        quiet = i < len(envelope) and envelope[i] <= threshold
        if quiet and run_start is None:
            run_start = i
        elif not quiet and run_start is not None:
            length = i - run_start
            if length >= min_windows and length > best_len:
                best_len, best_mid = length, run_start + length // 2
            run_start = None
    return best_mid


def _encode(frames: bytes, params: wave._wave_params) -> bytes:
    """Re-wrap raw PCM ``frames`` as a standalone WAV file with the same format."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(params.nchannels)
        out.setsampwidth(params.sampwidth)
        out.setframerate(params.framerate)
        out.writeframes(frames)
    return buf.getvalue()


def _boundaries(
    envelope: Sequence[float],
    *,
    total_windows: int,
    windows_per_second: float,
    policy: ChunkPolicy,
) -> list[tuple[int, bool]]:
    """Choose the chunk boundaries, in windows, walking the recording once.

    Args:
        envelope: Per-window loudness for the whole recording.
        total_windows: Number of windows in the recording.
        windows_per_second: Windows per second of audio.
        policy: The thresholds in force.

    Returns:
        ``(window index, landed in silence)`` for each cut, in ascending order.
        The final end of the recording is not included.
    """
    overall = (sum(envelope) / len(envelope)) if envelope else 0.0
    # Squared threshold, because the envelope is mean-square.
    threshold = overall * (policy.silence_ratio**2)
    min_silence = max(1, int(policy.min_silence_seconds * windows_per_second))
    max_w = int(policy.max_chunk_seconds * windows_per_second)
    min_w = int(policy.min_chunk_seconds * windows_per_second)
    search_w = int(policy.search_seconds * windows_per_second)

    cuts: list[tuple[int, bool]] = []
    cursor = 0
    while total_windows - cursor > max_w:
        target = cursor + max_w
        floor = max(cursor + min_w, target - search_w)
        mid = _quietest_run(envelope, floor, target, threshold, min_silence)
        if mid is None or mid <= cursor:
            cuts.append((target, False))
            cursor = target
        else:
            cuts.append((mid, True))
            cursor = mid
    return cuts


def plan_chunks(payload: AudioPayload, *, policy: ChunkPolicy | None = None) -> ChunkPlan:
    """Split ``payload`` into transcribable chunks, cutting in pauses where it can.

    Pure and offline: no model call, no network, no decoder beyond the stdlib WAV
    parser. A recording shorter than :attr:`ChunkPolicy.max_chunk_seconds`, or in a
    container this module cannot parse, comes back as a single chunk with a note
    saying which of those two it was.

    Args:
        payload: The audio payload. Only inline bytes can be split — a URI payload
            is bytes this process does not hold (and payload hygiene refuses it
            upstream anyway).
        policy: Thresholds; :class:`ChunkPolicy` defaults when omitted.

    Returns:
        A :class:`ChunkPlan` with at least one chunk.
    """
    policy = policy or ChunkPolicy()
    data = payload.data or b""
    if not data:
        return _whole(payload, "empty payload; nothing to split")

    try:
        with wave.open(io.BytesIO(data), "rb") as src:
            params = src.getparams()
            frames = src.readframes(params.nframes)
    except (wave.Error, EOFError, ValueError) as exc:
        return _whole(
            payload,
            f"container is not uncompressed PCM WAV ({payload.mime_type}; {exc}); "
            "transcribed whole in one request — this module decodes no other "
            "container (fleet-only policy: no ffmpeg, no local codec)",
        )

    if params.sampwidth not in _TYPECODE or params.framerate <= 0 or params.nchannels <= 0:
        return _whole(
            payload,
            f"{params.sampwidth * 8}-bit / {params.nchannels}ch @ {params.framerate}Hz WAV "
            "is outside the sample formats this splitter reads; transcribed whole",
        )

    duration = params.nframes / float(params.framerate)
    if duration <= policy.max_chunk_seconds:
        return ChunkPlan(
            chunks=[
                AudioChunk(
                    index=0,
                    start_seconds=0.0,
                    duration_seconds=duration,
                    data=data,
                    mime_type="audio/wav",
                )
            ],
            duration_seconds=duration,
            note=f"{duration:.1f}s is within the {policy.max_chunk_seconds:.0f}s "
            "single-request ceiling; sent as one request",
            splittable=True,
        )

    samples = _samples(frames, params.sampwidth)
    if params.sampwidth == 1:
        # 8-bit WAV is unsigned with a 128 midpoint; centre it or every window
        # looks equally loud and no pause is ever found.
        samples = array.array("h", (s - 128 for s in samples))
    per_window = max(1, int(_WINDOW_SECONDS * params.framerate) * params.nchannels)
    envelope = _envelope(samples, per_window)
    windows_per_second = params.framerate * params.nchannels / per_window
    cuts = _boundaries(
        envelope,
        total_windows=len(envelope),
        windows_per_second=windows_per_second,
        policy=policy,
    )

    frame_bytes = params.sampwidth * params.nchannels
    frames_per_window = per_window // params.nchannels
    chunks: list[AudioChunk] = []
    on_silence = 0
    starts = [0, *(c[0] for c in cuts)]
    ends = [*(c[0] for c in cuts), len(envelope)]
    flags = [*(c[1] for c in cuts), False]
    for i, (w0, w1, silent) in enumerate(zip(starts, ends, flags, strict=True)):
        f0, f1 = w0 * frames_per_window, min(w1 * frames_per_window, params.nframes)
        chunks.append(
            AudioChunk(
                index=i,
                start_seconds=f0 / float(params.framerate),
                duration_seconds=(f1 - f0) / float(params.framerate),
                data=_encode(frames[f0 * frame_bytes : f1 * frame_bytes], params),
                mime_type="audio/wav",
                split_on_silence=silent,
            )
        )
        on_silence += 1 if silent else 0

    forced = len(cuts) - on_silence
    note = (
        f"{duration:.1f}s exceeds the {policy.max_chunk_seconds:.0f}s single-request "
        f"ceiling; split into {len(chunks)} chunks — {on_silence} cut in a detected "
        f"pause, {forced} cut on time (no pause found within "
        f"{policy.search_seconds:.0f}s of the boundary)"
    )
    return ChunkPlan(chunks=chunks, duration_seconds=duration, note=note, splittable=True)
