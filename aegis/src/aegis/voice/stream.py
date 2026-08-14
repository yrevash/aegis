"""AG-UI streaming for ``aegis.voice`` — the transcription made visible as it happens.

A long recording is several requests over several seconds, so the console should
not stare at a spinner until the last chunk lands. This module brackets the work
in a ``STEP_STARTED``/``STEP_FINISHED`` pair and emits, à la carte over the shared
:class:`~aegis.core.stream.AegisEmitter`:

* ``CUSTOM(voice_chunk)`` — once per chunk as it comes back, carrying the running
  transcript so the UI can render speech arriving in order;
* ``CUSTOM(voice_transcript)`` — the finished transcription (segments, detected
  language, duration, chunking note, cost);
* ``CUSTOM(guardrail_media)`` — the rail verdict on the transcript, with the
  itemised list of which controls ran and which did not.

The verdict event reuses the media guardrail name rather than inventing a voice
one, because it *is* a media verdict: the console's existing verdict renderer
already understands it, and a second name would let the two drift.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from aegis.core import stream_names
from aegis.core.events import SpanKind
from aegis.core.types import GuardResult
from aegis.media import AudioPayload, MediaLimits
from aegis.voice.chunking import AudioChunk, ChunkPolicy
from aegis.voice.transcribe import TranscribeCallable, transcribe_and_guard
from aegis.voice.types import VoiceResult, VoiceTranscription

if TYPE_CHECKING:  # pragma: no cover - typing only
    from aegis.core.stream import AegisEmitter

_STEP_NAME = "voice_transcribe"


def transcript_event(transcription: VoiceTranscription) -> dict:
    """Build the ``voice_transcript`` payload for ``transcription``.

    Split out so the backend can emit the same shape over its own SSE contract
    without importing the AG-UI emitter.

    Args:
        transcription: The finished transcription.

    Returns:
        A JSON-safe dict. ``hasConfidence`` is the honesty flag the UI keys on:
        when it is false the console shows "not reported" for every segment
        instead of a fabricated score.
    """
    return {
        "text": transcription.text,
        "language": transcription.language,
        "durationSeconds": transcription.duration_seconds,
        "model": transcription.model,
        "chunkCount": transcription.chunk_count,
        "chunking": transcription.chunking,
        "costUsd": transcription.cost_usd,
        "audioSecondsBilled": transcription.audio_seconds_billed,
        "hasConfidence": transcription.has_confidence,
        "segments": [
            {
                "index": s.index,
                "start": s.start,
                "end": s.end,
                "text": s.text,
                "confidence": s.confidence,
                "chunk": s.chunk,
            }
            for s in transcription.segments
        ],
    }


def verdict_event(result: VoiceResult) -> dict:
    """Build the ``guardrail_media`` payload for a voice result.

    Args:
        result: The guarded transcription.

    Returns:
        A JSON-safe dict carrying the verdict, the layer that produced it, the
        redaction kinds (kinds only — never raw values) and the itemised coverage.
    """
    guard: GuardResult = result.guard
    return {
        "kind": "audio",
        "verdict": guard.verdict.value,
        "reason": guard.reason,
        "layer": guard.layer,
        "redactions": list(guard.redactions),
        "railsRun": list(result.controls_run),
        "railsSkipped": list(result.controls_skipped),
        "agentReady": result.agent_input is not None,
    }


async def stream_transcribe_and_guard(
    payload: AudioPayload,
    emitter: AegisEmitter,
    *,
    text_check: Callable[[str], Awaitable[GuardResult]] | None,
    transcriber: TranscribeCallable | None = None,
    language: str | None = None,
    limits: MediaLimits | None = None,
    policy: ChunkPolicy | None = None,
) -> VoiceResult:
    """Transcribe and guard ``payload``, streaming the work as it happens.

    Emits ``STEP_STARTED("voice_transcribe")`` → ``CUSTOM(voice_chunk)`` per chunk
    → ``CUSTOM(voice_transcript)`` → ``CUSTOM(guardrail_media)`` →
    ``STEP_FINISHED("voice_transcribe")``. The verdict event is emitted on every
    path, including the fail-closed ones, so a blocked recording is *visible* in
    the stream rather than being a stream that simply stops.

    Args:
        payload: The audio payload.
        emitter: The AG-UI emitter for this run.
        text_check: The full text rail stack; ``None`` blocks (fail-closed).
        transcriber: The gateway call to use; the default when omitted.
        language: Optional ISO-639-1 hint.
        limits: Hygiene thresholds.
        policy: Chunking thresholds.

    Returns:
        The :class:`~aegis.voice.types.VoiceResult`.
    """

    async def _on_chunk(chunk: AudioChunk, running: VoiceTranscription) -> None:
        """Emit one ``voice_chunk`` event for a chunk that just came back."""
        await emitter.custom(
            stream_names.VOICE_CHUNK,
            {
                "index": chunk.index,
                "of": running.chunk_count,
                "startSeconds": chunk.start_seconds,
                "durationSeconds": chunk.duration_seconds,
                "splitOnSilence": chunk.split_on_silence,
                "text": running.text,
            },
        )

    async with emitter.step(_STEP_NAME, SpanKind.CHAIN):
        result = await transcribe_and_guard(
            payload,
            text_check=text_check,
            transcriber=transcriber,
            language=language,
            limits=limits,
            policy=policy,
            on_chunk=_on_chunk,
        )
        if result.transcription is not None:
            await emitter.custom(
                stream_names.VOICE_TRANSCRIPT, transcript_event(result.transcription)
            )
        await emitter.custom(stream_names.GUARDRAIL_MEDIA, verdict_event(result))
    return result
