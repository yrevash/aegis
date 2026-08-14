"""Speech to text over the gateway — and the rails that stand between it and the agent.

This module is deliberately thin. It owns no model, no codec and no decoding
policy: :func:`aegis.gateway.transcribe` already routes ``ModelRole.VOICE`` to the
fleet's hosted Whisper deployment, enforces the budget *before* the spend, bills
per audio-second and opens its own OTel span. Re-implementing any of that here
would create a second, weaker copy of it.

What this module adds is the three things the gateway deliberately does not do:

1. **Media hygiene before spend.** :func:`aegis.media.inspect_payload` refuses a
   payload whose declared MIME type is a lie, whose bytes are unreadable, or
   which is over the cap — *before* a paid transcription request is made.
2. **Chunking.** A recording longer than one request's ceiling is split on
   silence (:mod:`aegis.voice.chunking`) and the chunk transcripts are stitched
   back into one timeline.
3. **The security ordering.** Transcribe first, then run the **whole existing
   text rail stack** over the transcript, and hand the agent only what the rails
   returned. This is not a new policy: it is
   :func:`aegis.guardrails.media.guard_audio`, the transcribe-then-guard contract
   the media rails already define, finally given a transcriber. Every attack that
   works in text works when spoken, and the text rails are the mature ones — so
   speech gets signatures, the injection classifier, content safety, PII, schema,
   topical *and* every custom rail the host configured, unchanged.

**Fail closed, always.** No transcriber, no text rail stack, a hygiene refusal, or
a transcription that raises — each of those ends in a BLOCK verdict whose reason
names the control that could not run. There is no path in this module that
returns agent-usable text without the rails having judged it.

Standalone usage::

    from aegis.media import AudioPayload, MediaSource, Provenance
    from aegis.voice import transcribe_and_guard

    payload = AudioPayload(
        data=wav_bytes,
        mime_type="audio/wav",
        provenance=Provenance(source=MediaSource.USER_UPLOAD, origin="note.wav"),
    )
    result = await transcribe_and_guard(payload, text_check=guards.check_input)
    result.transcription.text   # evidence for the operator's console
    result.agent_input          # None unless the rails cleared it
"""

from __future__ import annotations

import io
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol

from aegis.core.types import GuardResult, GuardVerdict
from aegis.guardrails.media.audio import guard_audio
from aegis.media import AudioPayload, MediaLimits, inspect_payload
from aegis.voice.chunking import AudioChunk, ChunkPolicy, plan_chunks
from aegis.voice.types import VoiceResult, VoiceSegment, VoiceTranscription

if TYPE_CHECKING:  # pragma: no cover - typing only
    from aegis.gateway.types import TranscriptionResult

logger = logging.getLogger(__name__)

#: Control labels, used in verdicts and as the ``layer`` a UI renders.
HYGIENE_CONTROL = "payload hygiene"
TRANSCRIPTION_CONTROL = "hosted transcription (ModelRole.VOICE)"
TEXT_RAILS_CONTROL = "full text rail stack over the transcript"
LAYER = "voice"

#: A control this module does **not** provide, named so the coverage line cannot
#: imply it. Speaker diarisation would need either a provider that reports it or a
#: local model; the fleet exposes neither, so it is stated as absent, not faked.
NO_DIARISATION = (
    "speaker diarisation (the fleet's hosted Whisper deployment reports no speaker "
    "labels and policy forbids a local model, so no speaker attribution is produced)"
)


class TranscribeCallable(Protocol):
    """The gateway transcription call this module drives.

    Matches :func:`aegis.gateway.transcribe` exactly. It is a Protocol so a test —
    or a host with its own chokepoint — can inject a fake, the same way the text
    rails take an injected ``ChatCompleter`` rather than importing a provider.
    """

    async def __call__(
        self,
        audio: Any,  # noqa: ANN401 - a binary file handle or a filesystem path
        *,
        language: str | None = None,
        prompt: str | None = None,
        response_format: str = "verbose_json",
        duration_seconds: float | None = None,
    ) -> TranscriptionResult:
        """Transcribe ``audio`` and return the normalised result."""
        ...  # pragma: no cover - Protocol body


class AudioRejected(ValueError):
    """Payload hygiene refused the audio, so nothing was transcribed.

    Attributes:
        summary: The hygiene report's one-line, PII-free reason.
    """

    def __init__(self, summary: str) -> None:
        """Store the hygiene summary and build the message.

        Args:
            summary: The hygiene report's ``summary()``.
        """
        super().__init__(f"Payload hygiene refused this audio: {summary}.")
        self.summary = summary


class _NamedBytesIO(io.BytesIO):
    """A :class:`io.BytesIO` carrying a ``name``.

    OpenAI-compatible upload clients read ``file.name`` to derive the filename (and
    therefore the format) of the multipart part. A bare ``BytesIO`` has no ``name``
    and cannot be given one, so a chunk would be uploaded with no extension and the
    provider would have to guess at the container.
    """

    def __init__(self, data: bytes, name: str) -> None:
        """Wrap ``data`` under the filename ``name``.

        Args:
            data: The chunk's bytes.
            name: Filename to advertise, e.g. ``"chunk-000.wav"``.
        """
        super().__init__(data)
        self.name = name


def _default_transcriber() -> TranscribeCallable:
    """Resolve :func:`aegis.gateway.transcribe`, failing loud if unavailable.

    Imported here rather than at module scope so importing :mod:`aegis.voice`
    stays as cheap as importing :mod:`aegis.media` — the Module Contract's
    isolation rule, and what keeps a light API schema layer free of the gateway.

    Returns:
        The gateway's ``transcribe`` coroutine function.

    Raises:
        ImportError: If the gateway package cannot be imported, naming the extra
            to install. Never a silent no-op transcriber.
    """
    try:
        from aegis.gateway import transcribe as gateway_transcribe
    except ImportError as exc:  # pragma: no cover - defensive; aegis.gateway is in-tree
        raise ImportError(
            "aegis.voice needs the gateway to reach the fleet's hosted speech-to-text "
            "deployment. Run: pip install aegis[gateway]"
        ) from exc
    return gateway_transcribe


def _extension(mime_type: str) -> str:
    """Map an audio MIME type to the file extension a provider expects."""
    return {
        "audio/wav": "wav",
        "audio/mpeg": "mp3",
        "audio/ogg": "ogg",
        "audio/flac": "flac",
        "audio/mp4": "m4a",
    }.get(mime_type, "bin")


def _merge_segments(
    result: TranscriptionResult, chunk: AudioChunk, next_index: int
) -> list[VoiceSegment]:
    """Rebase one chunk's segments onto the whole recording's timeline.

    Args:
        result: The provider's result for this chunk.
        chunk: The chunk it came from (its ``start_seconds`` is the offset).
        next_index: The transcript-wide index the first segment takes.

    Returns:
        The chunk's segments, renumbered and time-shifted.
    """
    offset = chunk.start_seconds
    return [
        VoiceSegment(
            index=next_index + i,
            start=None if seg.start is None else seg.start + offset,
            end=None if seg.end is None else seg.end + offset,
            text=seg.text,
            # Never derived: the gateway's segment parser carries no confidence
            # signal, so this stays None and the console says "not reported".
            confidence=None,
            chunk=chunk.index,
        )
        for i, seg in enumerate(result.segments)
    ]


async def transcribe_audio(
    payload: AudioPayload,
    *,
    transcriber: TranscribeCallable | None = None,
    language: str | None = None,
    prompt: str | None = None,
    limits: MediaLimits | None = None,
    policy: ChunkPolicy | None = None,
    on_chunk: Callable[[AudioChunk, VoiceTranscription], Awaitable[None]] | None = None,
) -> VoiceTranscription:
    """Transcribe ``payload`` on the fleet's hosted voice model.

    Runs media hygiene first (so a hostile or malformed payload never costs a
    request), splits a long recording on silence, transcribes each chunk through
    the gateway, and stitches the chunk transcripts back into one timeline.

    This function returns **evidence, not agent input**. Use
    :func:`transcribe_and_guard` for anything that will reach an agent.

    Args:
        payload: The audio to transcribe.
        transcriber: The gateway call to use; :func:`aegis.gateway.transcribe`
            when omitted.
        language: Optional ISO-639-1 hint. Omit it to let the model auto-detect —
            the detected language comes back on the result.
        prompt: Optional decoding hint (proper nouns, formatting).
        limits: Hygiene thresholds; :class:`~aegis.media.MediaLimits` defaults.
        policy: Chunking thresholds; :class:`~aegis.voice.chunking.ChunkPolicy`
            defaults.
        on_chunk: Optional async callback invoked after each chunk with that
            chunk and the running transcription — the streaming seam.

    Returns:
        The :class:`~aegis.voice.types.VoiceTranscription`.

    Raises:
        AudioRejected: If payload hygiene refused the audio (nothing was spent).
    """
    report = inspect_payload(payload, limits=limits)
    if not report.ok:
        raise AudioRejected(report.summary())

    call = transcriber or _default_transcriber()
    plan = plan_chunks(payload, policy=policy)

    texts: list[str] = []
    segments: list[VoiceSegment] = []
    detected: str | None = None
    model = ""
    cost = 0.0
    billed = 0.0
    reported_total = 0.0
    any_reported = False

    for chunk in plan.chunks:
        handle = _NamedBytesIO(
            chunk.data, f"chunk-{chunk.index:03d}.{_extension(chunk.mime_type)}"
        )
        result = await call(
            handle,
            language=language,
            prompt=prompt,
            response_format="verbose_json",
            duration_seconds=chunk.duration_seconds,
        )
        if result.text.strip():
            texts.append(result.text.strip())
        segments.extend(_merge_segments(result, chunk, len(segments)))
        detected = detected or result.language
        model = model or result.model
        cost += result.usage.cost_usd
        billed += result.usage.audio_seconds
        if result.duration_seconds is not None:
            reported_total += result.duration_seconds
            any_reported = True
        if on_chunk is not None:
            await on_chunk(
                chunk,
                VoiceTranscription(
                    text=" ".join(texts),
                    language=detected,
                    duration_seconds=plan.duration_seconds,
                    segments=segments,
                    model=model,
                    chunk_count=len(plan.chunks),
                    chunking=plan.note,
                    cost_usd=cost,
                    audio_seconds_billed=billed,
                ),
            )

    # Prefer the container's own duration; fall back to what the provider reported.
    # If neither exists it stays None — an unknown duration is never rendered as 0.
    duration = plan.duration_seconds if plan.duration_seconds is not None else None
    if duration is None and any_reported:
        duration = reported_total

    return VoiceTranscription(
        text=" ".join(texts),
        language=detected,
        duration_seconds=duration,
        segments=segments,
        model=model,
        chunk_count=len(plan.chunks),
        chunking=plan.note,
        cost_usd=cost,
        audio_seconds_billed=billed,
    )


def make_transcriber(
    *,
    transcriber: TranscribeCallable | None = None,
    language: str | None = None,
    limits: MediaLimits | None = None,
    policy: ChunkPolicy | None = None,
) -> Callable[[AudioPayload], Awaitable[str]]:
    """Build the ``Transcriber`` :class:`aegis.guardrails.media.MediaScreen` wants.

    The media rail chain fails audio **closed** when no transcriber is wired
    (``transcriber=None`` ⇒ every audio payload is blocked). This is the one-liner
    that wires it, so an audio payload reaching the guardrails is transcribed and
    screened rather than refused::

        screen = MediaScreen(transcriber=make_transcriber())

    Args:
        transcriber: The gateway call to use; the default when omitted.
        language: Optional ISO-639-1 hint.
        limits: Hygiene thresholds.
        policy: Chunking thresholds.

    Returns:
        An async ``AudioPayload -> str`` callable. It raises rather than returning
        an empty string on failure, because :func:`aegis.guardrails.media.guard_audio`
        converts a raising transcriber into a fail-closed BLOCK — whereas an empty
        transcript would sail through the text rails as "nothing to object to".
    """

    async def _transcribe(payload: AudioPayload) -> str:
        """Transcribe ``payload`` and return the transcript text."""
        result = await transcribe_audio(
            payload,
            transcriber=transcriber,
            language=language,
            limits=limits,
            policy=policy,
        )
        return result.text

    return _transcribe


async def transcribe_and_guard(
    payload: AudioPayload,
    *,
    text_check: Callable[[str], Awaitable[GuardResult]] | None,
    transcriber: TranscribeCallable | None = None,
    language: str | None = None,
    prompt: str | None = None,
    limits: MediaLimits | None = None,
    policy: ChunkPolicy | None = None,
    on_chunk: Callable[[AudioChunk, VoiceTranscription], Awaitable[None]] | None = None,
) -> VoiceResult:
    """Transcribe ``payload``, then screen the transcript with the full text stack.

    **The ordering is the security control.** Audio is transcribed first because a
    rail cannot judge what it cannot read; the transcript is then handed to
    ``text_check`` — the caller's *entire* input rail stack — and only what that
    returns is exposed as :attr:`~aegis.voice.types.VoiceResult.agent_input`. Raw
    audio has no path to an agent through this function, and neither does an
    unscreened transcript.

    Every failure direction is closed:

    * hygiene refused the payload → BLOCK, nothing transcribed, nothing spent;
    * no ``text_check`` supplied → BLOCK, because the rails did not run;
    * transcription raised (budget, timeout, provider) → BLOCK, because there is
      no transcript for the rails to judge.

    Args:
        payload: The audio payload.
        text_check: The complete text rail stack — in practice
            :meth:`aegis.guardrails.Guardrails.check_input`. ``None`` blocks.
        transcriber: The gateway call to use; the default when omitted.
        language: Optional ISO-639-1 hint.
        prompt: Optional decoding hint.
        limits: Hygiene thresholds.
        policy: Chunking thresholds.
        on_chunk: Optional per-chunk async callback (the streaming seam).

    Returns:
        A :class:`~aegis.voice.types.VoiceResult` whose ``controls_run`` /
        ``controls_skipped`` itemise exactly which controls executed.
    """
    ran: list[str] = []
    skipped: list[str] = [NO_DIARISATION]

    try:
        transcription = await transcribe_audio(
            payload,
            transcriber=transcriber,
            language=language,
            prompt=prompt,
            limits=limits,
            policy=policy,
            on_chunk=on_chunk,
        )
    except AudioRejected as exc:
        skipped.insert(0, f"{TRANSCRIPTION_CONTROL} and {TEXT_RAILS_CONTROL} (hygiene refused)")
        return VoiceResult(
            transcription=None,
            guard=GuardResult(
                verdict=GuardVerdict.BLOCK,
                reason=f"{exc} Nothing was transcribed and nothing was spent.",
                text="",
                layer=f"{LAYER}:media_hygiene",
            ),
            controls_run=[HYGIENE_CONTROL],
            controls_skipped=skipped,
        )
    except Exception as exc:  # noqa: BLE001 - a failed transcription must fail closed
        logger.warning("Voice transcription failed; blocking (fail-closed).", exc_info=True)
        skipped.insert(0, f"{TEXT_RAILS_CONTROL} (no transcript to screen)")
        return VoiceResult(
            transcription=None,
            guard=GuardResult(
                verdict=GuardVerdict.BLOCK,
                reason=f"Transcription failed, so the text rails could not run: {exc}. "
                "Blocked (fail-closed).",
                text="",
                layer=f"{LAYER}:transcription",
            ),
            controls_run=[HYGIENE_CONTROL],
            controls_skipped=skipped,
        )

    ran.extend([HYGIENE_CONTROL, TRANSCRIPTION_CONTROL])

    if text_check is None:
        skipped.insert(0, f"{TEXT_RAILS_CONTROL} (no rail stack was supplied)")
        verdict = GuardResult(
            verdict=GuardVerdict.BLOCK,
            reason="The transcript was never screened — no text rail stack was supplied, "
            "so speech would have reached the agent unguarded. Blocked (fail-closed).",
            text="",
            layer=f"{LAYER}:text_rails",
        )
    else:
        # Routed through the media package's own transcribe-then-guard contract
        # rather than re-implementing it: `guard_audio` owns the fail-closed
        # semantics and the `[transcript]` verdict prefix that tells a reader the
        # verdict came via speech rather than typed input.
        verdict = await guard_audio(
            payload,
            transcriber=lambda _payload: transcription.text,
            text_check=text_check,
        )
        ran.append(TEXT_RAILS_CONTROL)

    result = VoiceResult(
        transcription=transcription,
        guard=verdict,
        controls_run=ran,
        controls_skipped=skipped,
    )
    # The reason line is generated from the coverage lists, so it cannot overstate
    # which controls ran (the same discipline as MediaGuardResult.coverage).
    scored = verdict.model_copy(update={"reason": f"{verdict.reason} {result.coverage()}"})
    return result.model_copy(update={"guard": scored})
