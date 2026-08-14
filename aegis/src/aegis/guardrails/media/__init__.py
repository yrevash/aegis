"""Media guardrails — the rails that screen images and audio.

Where :mod:`aegis.media` holds *facts about bytes* (types, sniffing, hygiene),
this package holds *policy*: the image-injection screen that closes the vision
hole, the image-PII rail that returns an actually-redacted image, the
transcribe-then-guard contract for audio, and the adapter that let the ``Rail``
type widen from ``str`` to ``MediaPayload`` without breaking a single existing
custom rail.

Standalone usage::

    from aegis.guardrails.media import MediaScreen
    from aegis.media import ImagePayload

    screen = MediaScreen(vision_completer=my_vision_completer)
    verdict = await screen.check(ImagePayload(data=png, mime_type="image/png"),
                                 text_check=guards.check_input)

In practice you get it for free: :class:`aegis.guardrails.Guardrails` builds one
and ``check_input`` routes any non-text payload through it.
"""

from __future__ import annotations

from aegis.guardrails.media.adapt import call_rail, is_media_rail, media_rail
from aegis.guardrails.media.audio import Transcriber, guard_audio
from aegis.guardrails.media.image_pii import ImagePIIResult, redact_image
from aegis.guardrails.media.injection import (
    IMAGE_SCREEN_SYSTEM_PROMPT,
    ImageScreenVerdict,
    classify_image,
    screen_image,
    vision_messages,
)
from aegis.guardrails.media.screen import (
    HYGIENE_LAYER,
    INJECTION_LAYER,
    PII_LAYER,
    MediaScreen,
)
from aegis.guardrails.media.types import MediaGuardResult

__all__ = [
    "HYGIENE_LAYER",
    "IMAGE_SCREEN_SYSTEM_PROMPT",
    "INJECTION_LAYER",
    "PII_LAYER",
    "ImagePIIResult",
    "ImageScreenVerdict",
    "MediaGuardResult",
    "MediaScreen",
    "Transcriber",
    "call_rail",
    "classify_image",
    "guard_audio",
    "is_media_rail",
    "media_rail",
    "redact_image",
    "screen_image",
    "vision_messages",
]
