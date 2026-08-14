"""Aegis Voice — speech to text, guarded before it can reach an agent.

A thin, honest layer over :func:`aegis.gateway.transcribe`. The gateway already
owns the model call (``ModelRole.VOICE`` → the fleet's hosted Whisper deployment),
the budget check *before* the spend, per-audio-second billing and its own OTel
span. This package adds the three things that sit either side of that call:

* **payload hygiene before spend** — a lying MIME type, an unreadable payload or
  one over the cap is refused before a paid request is made;
* **chunking** — a recording past the single-request ceiling is split on silence
  (pure stdlib, PCM WAV; other containers are transcribed whole and *say so*),
  and the chunk transcripts are stitched back onto one timeline;
* **the security ordering** — transcribe first, then run the caller's **entire**
  text rail stack over the transcript, and expose only what the rails returned.

Nothing here bypasses the rails, and nothing here fails open: no transcriber, no
rail stack, a hygiene refusal or a transcription error each end in a BLOCK whose
reason names the control that did not run.

Standalone usage::

    from aegis.media import AudioPayload, MediaSource, Provenance
    from aegis.voice import transcribe_and_guard

    payload = AudioPayload(
        data=wav_bytes,
        mime_type="audio/wav",
        provenance=Provenance(source=MediaSource.USER_UPLOAD, origin="note.wav"),
    )
    result = await transcribe_and_guard(payload, text_check=guards.check_input)
    result.transcription.text        # evidence for the console
    result.transcription.segments    # time-aligned, on the recording's timeline
    result.guard.verdict             # the full text stack's verdict
    result.agent_input               # None unless the rails cleared it

Wiring speech into the media rail chain (audio is blocked without this)::

    from aegis.guardrails.media import MediaScreen
    from aegis.voice import make_transcriber

    screen = MediaScreen(transcriber=make_transcriber())

**Not verified against the live fleet.** The gateway credential in this repo is a
placeholder, so every behaviour here is proven against injected fakes. What the
live ``azure/genailab-maas-whisper`` deployment returns for segments, language
detection and duration is asserted nowhere in this package.
"""

from __future__ import annotations

from aegis.voice.chunking import AudioChunk, ChunkPlan, ChunkPolicy, plan_chunks
from aegis.voice.stream import (
    stream_transcribe_and_guard,
    transcript_event,
    verdict_event,
)
from aegis.voice.transcribe import (
    NO_DIARISATION,
    AudioRejected,
    TranscribeCallable,
    make_transcriber,
    transcribe_and_guard,
    transcribe_audio,
)
from aegis.voice.types import VoiceResult, VoiceSegment, VoiceTranscription

__all__ = [
    "NO_DIARISATION",
    "AudioChunk",
    "AudioRejected",
    "ChunkPlan",
    "ChunkPolicy",
    "TranscribeCallable",
    "VoiceResult",
    "VoiceSegment",
    "VoiceTranscription",
    "make_transcriber",
    "plan_chunks",
    "stream_transcribe_and_guard",
    "transcribe_and_guard",
    "transcribe_audio",
    "transcript_event",
    "verdict_event",
]
