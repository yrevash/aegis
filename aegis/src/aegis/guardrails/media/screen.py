"""The media rail chain — the media twin of :mod:`aegis.guardrails.pipeline`.

Layer order deliberately mirrors the text pipeline, for the same reasons:

===========================  ===================================================
Text pipeline                Media chain
===========================  ===================================================
schema / format validation   payload hygiene (size, MIME truth, bomb guard)
PII redaction                image-PII redaction (paints the pixels out)
injection classifier         image-injection screen (a cheap vision call)
content safety / topical     — (see "Deliberate gaps" below)
custom rails                 custom rails, now payload-typed
===========================  ===================================================

The PII-before-classifier ordering carries over verbatim: sending an unredacted
image to a screening model is itself a sensitive-information disclosure (OWASP
LLM06), exactly as it was for text. Cheap and deterministic first, model calls
last.

Audio does not get its own chain. It is transcribed and handed to the *whole*
text stack (:mod:`aegis.guardrails.media.audio`), so every rail an operator
already configured — including their custom ones — applies to speech unchanged.

**Deliberate gaps, stated rather than implied.** There is no content-safety or
topical screen over raw pixels: both would need a second vision call per image,
and the cheap screen's job is the injection hole. Unsafe *imagery* is therefore
out of scope for this release, and the verdict's ``rails_skipped`` says so
rather than letting a reader assume coverage.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aegis.core.interfaces import ChatCompleter
from aegis.core.types import GuardResult, GuardVerdict
from aegis.guardrails.media.audio import Transcriber, guard_audio
from aegis.guardrails.media.injection import ImageScreenVerdict, screen_image
from aegis.guardrails.media.types import MediaGuardResult
from aegis.media import (
    AudioPayload,
    ImagePayload,
    MediaKind,
    MediaLimits,
    MediaPayload,
    inspect_payload,
)

logger = logging.getLogger(__name__)

#: Rail labels, used in verdicts and as the ``layer`` a UI renders.
HYGIENE_LAYER = "media_hygiene"
INJECTION_LAYER = "media_injection"
PII_LAYER = "media_pii"
CUSTOM_STAGE = "custom"

#: Named here so the honest-coverage list is written once, not restated per branch.
_NO_IMAGE_SAFETY = (
    "image content-safety/topical screen (not implemented for pixels in this release)"
)

CustomRunner = Callable[[MediaPayload], Awaitable[GuardResult | None]]


class MediaScreen:
    """The configured media rail chain. Held by :class:`aegis.guardrails.Guardrails`.

    Every dependency is injected, matching how the text rails take their
    ``ChatCompleter``: this class makes policy decisions and owns no provider.
    """

    def __init__(
        self,
        *,
        vision_completer: ChatCompleter | None = None,
        limits: MediaLimits | None = None,
        image_pii: bool = False,
        image_analyzer: Any = None,  # noqa: ANN401 - duck-typed Presidio engine
        image_redactor: Any = None,  # noqa: ANN401 - duck-typed Presidio engine
        transcriber: Transcriber | None = None,
    ) -> None:
        """Configure the chain.

        Args:
            vision_completer: A vision-capable completer for the image-injection
                screen. ``None`` makes the screen fail **closed** on every image —
                there is no offline backstop for pixels.
            limits: Hygiene thresholds; :class:`~aegis.media.MediaLimits` defaults.
            image_pii: Opt in to the ``presidio-image-redactor`` rail. When set and
                the ``aegis[media]`` extra is missing, the rail raises
                :class:`ImportError` with the install command — declared-but-missing
                fails loud, it never degrades to silence.
            image_analyzer: Test/advanced seam — a Presidio ``ImageAnalyzerEngine``.
                Supplying it implies ``image_pii``.
            image_redactor: Test/advanced seam — a Presidio ``ImageRedactorEngine``.
            transcriber: Injected speech-to-text for audio. ``None`` blocks audio.
        """
        self._vision_completer = vision_completer
        self._limits = limits or MediaLimits()
        self._image_pii = image_pii or image_analyzer is not None
        self._image_analyzer = image_analyzer
        self._image_redactor = image_redactor
        self._transcriber = transcriber

    @property
    def limits(self) -> MediaLimits:
        """The hygiene thresholds in force."""
        return self._limits

    @property
    def has_vision_completer(self) -> bool:
        """Whether an image can actually be screened (False ⇒ images fail closed)."""
        return self._vision_completer is not None

    # ── the chain ────────────────────────────────────────────────────────────

    async def check(
        self,
        payload: MediaPayload,
        *,
        text_check: Callable[[str], Awaitable[GuardResult]],
        custom: CustomRunner | None = None,
        skipped_custom: list[str] | None = None,
    ) -> MediaGuardResult:
        """Screen a non-text payload and return an honest, itemised verdict.

        Args:
            payload: The image or audio payload.
            text_check: The full text rail stack, used for audio transcripts.
            custom: Runner for the caller's custom rails (already adapted).
            skipped_custom: Collector the custom runner appends skip reasons to.

        Returns:
            A :class:`MediaGuardResult` whose ``rails_run``/``rails_skipped``
            itemise the coverage and whose ``media`` carries a rewritten payload
            when a rail produced one.

        Raises:
            ValueError: If handed a text payload — text has its own pipeline and
                routing it here would silently skip the text rails.
        """
        if payload.kind is MediaKind.TEXT:
            raise ValueError(
                "text payloads belong to the text pipeline, not the media chain"
            )
        if isinstance(payload, AudioPayload):
            return await self._check_audio(payload, text_check=text_check)
        if isinstance(payload, ImagePayload):
            return await self._check_image(
                payload, custom=custom, skipped_custom=skipped_custom
            )
        raise ValueError(f"unsupported media kind: {payload.kind}")  # pragma: no cover

    def _hygiene(self, payload: MediaPayload) -> MediaGuardResult | None:
        """Run payload hygiene; return a BLOCK verdict on failure, else ``None``."""
        report = inspect_payload(payload, limits=self._limits)
        if report.ok:
            return None
        return MediaGuardResult(
            verdict=GuardVerdict.BLOCK,
            reason=f"Payload hygiene refused this {payload.kind.value}: {report.summary()}.",
            text=payload.describe(),
            layer=HYGIENE_LAYER,
            rails_run=["hygiene"],
            rails_skipped=[
                "every downstream media rail (hygiene refused the payload first)"
            ],
        )

    async def _check_audio(
        self, payload: AudioPayload, *, text_check: Callable[[str], Awaitable[GuardResult]]
    ) -> MediaGuardResult:
        """Hygiene, then transcribe-then-guard through the full text stack."""
        blocked = self._hygiene(payload)
        if blocked is not None:
            return blocked
        result = await guard_audio(
            payload, transcriber=self._transcriber, text_check=text_check
        )
        ran = ["hygiene"]
        skipped: list[str] = []
        if self._transcriber is None:
            skipped.append("transcription + the entire text rail stack (no transcriber wired)")
        else:
            ran.append("transcription → full text rail stack")
        return MediaGuardResult(
            verdict=result.verdict,
            reason=result.reason,
            text=result.text,
            layer=result.layer,
            redactions=result.redactions,
            rails_run=ran,
            rails_skipped=skipped,
        )

    async def _run_image_pii(
        self, payload: ImagePayload, ran: list[str], skipped: list[str]
    ) -> tuple[ImagePayload, list[str]]:
        """Redact PII burned into the image, mirroring text's redact-before-classify.

        Returns the payload to screen from here on and the entity kinds found.
        """
        if not self._image_pii:
            skipped.append(
                "image-PII redaction (not enabled; pass image_pii=True and install aegis[media])"
            )
            return payload, []
        from aegis.guardrails.media.image_pii import redact_image

        result = redact_image(
            payload, analyzer=self._image_analyzer, redactor=self._image_redactor
        )
        ran.append("image-PII redaction")
        return (result.payload if result.redacted else payload), result.entities

    async def _check_image(
        self,
        payload: ImagePayload,
        *,
        custom: CustomRunner | None,
        skipped_custom: list[str] | None,
    ) -> MediaGuardResult:
        """Hygiene → image-PII redaction → injection screen → custom rails."""
        blocked = self._hygiene(payload)
        if blocked is not None:
            return blocked

        ran: list[str] = ["hygiene"]
        skipped: list[str] = [_NO_IMAGE_SAFETY]
        current, entities = await self._run_image_pii(payload, ran, skipped)

        verdict = await screen_image(current, completer=self._vision_completer)
        if verdict.screened:
            ran.append("image-injection screen")
        else:
            skipped.append(
                "image-injection screen (could not run — blocking instead of passing)"
            )
        if verdict.injection:
            return self._injection_block(payload, current, verdict, ran, skipped, entities)

        if custom is not None:
            result = await custom(current)
            if skipped_custom:
                skipped.extend(skipped_custom)
            if result is not None:
                ran.append(f"custom rail '{result.layer or CUSTOM_STAGE}'")
                return MediaGuardResult(
                    verdict=result.verdict,
                    reason=result.reason,
                    text=result.text,
                    layer=result.layer,
                    redactions=result.redactions,
                    media=current if entities else None,
                    rails_run=ran,
                    rails_skipped=skipped,
                )
            ran.append("custom rails")

        if entities:
            result = MediaGuardResult(
                verdict=GuardVerdict.REDACT,
                reason=f"Redacted PII burned into the image: {', '.join(entities)}. "
                "The returned `media` payload is the redacted image — forward that, "
                "not the original.",
                text=current.describe(),
                layer=PII_LAYER,
                redactions=entities,
                media=current,
                rails_run=ran,
                rails_skipped=skipped,
            )
            return result
        result = MediaGuardResult(
            verdict=GuardVerdict.PASS,
            reason="Image cleared the media rails.",
            text=payload.describe(),
            rails_run=ran,
            rails_skipped=skipped,
        )
        result.reason = f"{result.reason} {result.coverage()}"
        return result

    @staticmethod
    def _injection_block(
        original: ImagePayload,
        screened: ImagePayload,
        verdict: ImageScreenVerdict,
        ran: list[str],
        skipped: list[str],
        entities: list[str],
    ) -> MediaGuardResult:
        """Build the BLOCK verdict for a failed (or unavailable) injection screen."""
        prefix = (
            "Image blocked by the injection screen"
            if verdict.screened
            else "Image blocked because the injection screen could not run"
        )
        result = MediaGuardResult(
            verdict=GuardVerdict.BLOCK,
            reason=f"{prefix}: {verdict.reason}",
            text=original.describe(),
            layer=INJECTION_LAYER,
            redactions=entities,
            media=screened if entities else None,
            rails_run=ran,
            rails_skipped=skipped,
        )
        result.reason = f"{result.reason} {result.coverage()}"
        return result
