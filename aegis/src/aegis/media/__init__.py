"""Aegis media — typed payloads and payload hygiene for non-text input.

The security seam voice and vision sit on. Standalone usage::

    from aegis.media import ImagePayload, MediaSource, Provenance, inspect_payload

    payload = ImagePayload(
        data=raw_bytes,
        mime_type="image/png",
        provenance=Provenance(source=MediaSource.USER_UPLOAD, origin="upload.png"),
    )
    report = inspect_payload(payload)
    if not report.ok:
        raise ValueError(report.summary())

Pydantic + stdlib only — importing this package pulls no codec, no model, and no
network client, so :mod:`aegis.core` and :mod:`aegis.guardrails` stay as
dependency-light as they were before media existed.

The rails that *act* on these payloads live in :mod:`aegis.guardrails.media`;
this package deliberately holds no policy, only facts about the bytes.
"""

from __future__ import annotations

from aegis.media.hygiene import (
    HygieneCode,
    HygieneFailure,
    HygieneReport,
    MediaLimits,
    inspect_payload,
)
from aegis.media.sniff import DIMENSIONABLE_IMAGE_MIMES, image_dimensions, sniff_mime
from aegis.media.types import (
    MEDIA_PAYLOAD_ADAPTER,
    AudioPayload,
    ImagePayload,
    MediaKind,
    MediaPayload,
    MediaSource,
    Provenance,
    TextPayload,
    as_payload,
    payload_from_context,
)

__all__ = [
    "DIMENSIONABLE_IMAGE_MIMES",
    "MEDIA_PAYLOAD_ADAPTER",
    "AudioPayload",
    "HygieneCode",
    "HygieneFailure",
    "HygieneReport",
    "ImagePayload",
    "MediaKind",
    "MediaLimits",
    "MediaPayload",
    "MediaSource",
    "Provenance",
    "TextPayload",
    "as_payload",
    "image_dimensions",
    "inspect_payload",
    "payload_from_context",
    "sniff_mime",
]
