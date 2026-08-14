"""The typed results ``aegis.voice`` returns — transcript, segments, verdict.

Pydantic + stdlib only, exactly like :mod:`aegis.media.types`, so an API schema
layer (or a test) can import these without dragging in ``litellm`` or the rest of
the gateway. The heavy work lives behind :mod:`aegis.voice.transcribe`.

**Two things in here are deliberately honest rather than convenient:**

* :attr:`VoiceSegment.confidence` is ``None`` on every segment the fleet's hosted
  Whisper returns today. Whisper's ``verbose_json`` reports ``avg_logprob`` and
  ``no_speech_prob``, but the gateway's segment parser
  (:func:`aegis.gateway.llm._parse_segments`) keeps only ``id``/``start``/``end``/
  ``text``, so no confidence signal reaches this module. The field exists so a
  provider that *does* report one can be carried straight through — it is never
  filled in with a derived or invented number, and the console renders "not
  reported" rather than a plausible-looking percentage.
* :attr:`VoiceResult.agent_input` is the **only** way transcribed speech is meant
  to leave this module towards an agent, and it is ``None`` unless the full text
  rail stack cleared the transcript. The raw transcript stays reachable as
  evidence for the operator's own console, never as agent input.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from aegis.core.types import GuardResult, GuardVerdict


class VoiceSegment(BaseModel):
    """One time-aligned segment of a transcript.

    Attributes:
        index: Position of the segment in the whole transcript (0-based). Chunked
            audio is renumbered end to end, so this is a transcript index rather
            than the provider's per-request one.
        start: Segment start in seconds **from the start of the whole recording**
            (chunk offsets are added back), or ``None`` when the provider gave no
            timing.
        end: Segment end in seconds from the start of the whole recording.
        text: The transcribed text of the segment.
        confidence: Provider-reported confidence in ``[0, 1]``, or ``None`` when
            the provider reports none. See the module docstring — today this is
            always ``None`` on the hosted fleet, and that is displayed as
            "not reported", never as a number.
        chunk: Which chunk of the (possibly split) recording produced it.
    """

    model_config = ConfigDict(frozen=True)

    index: int
    start: float | None = None
    end: float | None = None
    text: str = ""
    confidence: float | None = None
    chunk: int = 0


class VoiceTranscription(BaseModel):
    """A finished transcription — the evidence, before any rail has judged it.

    Holding this separately from :class:`VoiceResult` is the point: a transcript
    exists as *evidence* the moment the model returns it, but it is not agent
    input until the text rails have cleared it.

    Attributes:
        text: The full transcript (chunk transcripts joined in order).
        language: Language the provider detected, or ``None`` when it reported none.
        duration_seconds: Total audio duration in seconds — the billing unit — or
            ``None`` when neither the provider nor the container reported one.
        segments: Time-aligned segments, empty when the provider returned none.
        model: The deployment id that answered (e.g. ``genailab-maas-whisper``).
        chunk_count: How many requests the recording was split into (1 = one shot).
        chunking: One honest line about *why* it was or was not split.
        cost_usd: What the transcription cost, as ledgered by the gateway.
        audio_seconds_billed: Audio seconds the gateway actually billed for.
    """

    model_config = ConfigDict(frozen=True)

    text: str = ""
    language: str | None = None
    duration_seconds: float | None = None
    segments: list[VoiceSegment] = Field(default_factory=list)
    model: str = ""
    chunk_count: int = 1
    chunking: str = ""
    cost_usd: float = 0.0
    audio_seconds_billed: float = 0.0

    @property
    def has_confidence(self) -> bool:
        """Whether *any* segment carries a provider-reported confidence."""
        return any(s.confidence is not None for s in self.segments)


class VoiceResult(BaseModel):
    """A transcription plus the text rail stack's verdict on the transcript.

    Attributes:
        transcription: The transcript and its evidence, or ``None`` when the audio
            never got as far as being transcribed (payload hygiene refused it, or
            the transcription call failed).
        guard: The verdict of the **full text rail stack**, run over the
            transcript. On a BLOCK this is the only thing that matters.
        controls_run: The controls that actually executed, in order.
        controls_skipped: The controls that did **not** execute, each with the
            reason. A control that cannot run fails closed *and* says so — the
            reason line is generated from these lists, so it can never claim
            coverage that did not happen.
    """

    model_config = ConfigDict(frozen=True)

    transcription: VoiceTranscription | None = None
    guard: GuardResult
    controls_run: list[str] = Field(default_factory=list)
    controls_skipped: list[str] = Field(default_factory=list)

    @property
    def cleared(self) -> bool:
        """Whether the rails let the transcript through (anything but BLOCK)."""
        return self.guard.verdict is not GuardVerdict.BLOCK

    @property
    def agent_input(self) -> str | None:
        """The text an agent may be given, or ``None`` when the rails refused.

        This is deliberately the rails' ``text``, not the raw transcript: when the
        PII rail redacts, the agent must receive the *redacted* string, and when
        any rail blocks, the agent must receive nothing at all. Reading
        :attr:`VoiceTranscription.text` instead would be the bypass this whole
        module exists to prevent.
        """
        if not self.cleared:
            return None
        return self.guard.text

    def coverage(self) -> str:
        """A sentence naming exactly which controls ran and which did not."""
        ran = ", ".join(self.controls_run) if self.controls_run else "none"
        parts = [f"Controls run: {ran}."]
        if self.controls_skipped:
            parts.append("Not run: " + "; ".join(self.controls_skipped) + ".")
        return " ".join(parts)
