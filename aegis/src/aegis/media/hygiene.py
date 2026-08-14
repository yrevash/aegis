"""Payload hygiene — the cheap, deterministic checks that run before anything else.

**Why these three, in this order.** They are the bypasses that cost an attacker
nothing and cost the defender everything:

1. **Size cap.** An unbounded payload is a denial-of-service on the guardrails
   themselves — and on the vision model behind them, which is billed per pixel.
2. **MIME mismatch.** The declared type is attacker-controlled. If the declared
   type says ``text/plain`` and the magic bytes say PNG, the caller's routing
   decided *text rails* for something that is actually an image; that single
   lie is the whole bypass.
3. **Decompression bomb.** The classic: a few kilobytes of PNG that declare
   40 000 × 40 000 pixels and turn into gigabytes of RAM the instant any
   downstream library decodes them. The guard reads the *header* and refuses —
   it never decodes, so the bomb cannot go off while being inspected.

Everything here is pure and offline: no model call, no network, no image codec.
A hygiene failure is a hard block, and every failure carries a stable ``code``
so a verdict can say exactly which check refused and why.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from aegis.media.sniff import DIMENSIONABLE_IMAGE_MIMES, image_dimensions, sniff_mime
from aegis.media.types import MediaKind, MediaPayload


class HygieneCode:
    """Stable failure codes. Strings, so they survive serialisation into a verdict."""

    EMPTY = "empty_payload"
    SIZE_CAP = "size_cap_exceeded"
    NOT_INSPECTABLE = "uri_not_inspectable"
    MIME_UNKNOWN = "mime_unrecognized"
    MIME_MISMATCH = "mime_mismatch"
    MIME_NOT_ALLOWED = "mime_not_allowed"
    DIMENSIONS_UNREADABLE = "image_dimensions_unreadable"
    BOMB_PIXELS = "decompression_bomb_pixels"
    BOMB_RATIO = "decompression_bomb_ratio"


class MediaLimits(BaseModel):
    """The hygiene thresholds. Defaults are deliberately conservative.

    Attributes:
        max_bytes: Hard cap on any binary payload.
        max_text_bytes: Separate, much smaller cap for text (the text schema rail
            has its own character limit; this is the byte-level backstop).
        max_pixels: Cap on ``width * height`` declared by an image header. 40 MP
            sits below Pillow's own 89 MP ``DecompressionBombWarning`` threshold,
            so Aegis refuses before any downstream decoder even warns.
        max_pixels_per_byte: Compression-ratio cap. A legitimate photo lands
            around 1–10 pixels per byte; a bomb is thousands. This catches the
            small-but-enormous file that slips under ``max_pixels``.
        allowed_image_mimes: Image types that may be accepted at all.
        allowed_audio_mimes: Audio types that may be accepted at all.
    """

    model_config = ConfigDict(frozen=True)

    max_bytes: int = 8 * 1024 * 1024
    max_text_bytes: int = 256 * 1024
    max_pixels: int = 40_000_000
    max_pixels_per_byte: int = 500
    allowed_image_mimes: frozenset[str] = frozenset(DIMENSIONABLE_IMAGE_MIMES)
    allowed_audio_mimes: frozenset[str] = frozenset(
        {"audio/wav", "audio/mpeg", "audio/ogg", "audio/flac", "audio/mp4"}
    )


class HygieneFailure(BaseModel):
    """One refused check."""

    model_config = ConfigDict(frozen=True)

    code: str
    detail: str


class HygieneReport(BaseModel):
    """The outcome of :func:`inspect_payload`.

    Attributes:
        ok: Whether every check passed.
        failures: Every check that refused (all of them, not just the first —
            an operator debugging a rejected upload wants the full picture).
        sniffed_mime: The MIME type derived from magic bytes, or ``None``.
        dimensions: ``(width, height)`` read from an image header, when readable.
    """

    ok: bool
    failures: list[HygieneFailure] = Field(default_factory=list)
    sniffed_mime: str | None = None
    dimensions: tuple[int, int] | None = None

    def summary(self) -> str:
        """A one-line, PII-free description of why hygiene refused."""
        if self.ok:
            return "payload hygiene passed"
        return "; ".join(f"{f.code}: {f.detail}" for f in self.failures)


def _size_failures(payload: MediaPayload, limits: MediaLimits) -> list[HygieneFailure]:
    """Check the byte-size caps (kind-specific for text)."""
    size = payload.byte_size
    if size is None:
        return []
    cap = limits.max_text_bytes if payload.kind is MediaKind.TEXT else limits.max_bytes
    if size > cap:
        return [
            HygieneFailure(
                code=HygieneCode.SIZE_CAP,
                detail=f"{size} bytes exceeds the {cap}-byte cap for {payload.kind.value}",
            )
        ]
    return []


def _expected_prefix(kind: MediaKind) -> str:
    """The MIME family a payload of ``kind`` must sniff as."""
    return {MediaKind.TEXT: "text/", MediaKind.IMAGE: "image/", MediaKind.AUDIO: "audio/"}[kind]


def _mime_failures(
    payload: MediaPayload, sniffed: str | None, limits: MediaLimits
) -> list[HygieneFailure]:
    """Compare the declared type against the sniffed one and the allowlists."""
    failures: list[HygieneFailure] = []
    if sniffed is None:
        return [
            HygieneFailure(
                code=HygieneCode.MIME_UNKNOWN,
                detail=f"magic bytes match no known container (declared {payload.mime_type!r})",
            )
        ]
    if payload.kind is MediaKind.TEXT:
        # Magic bytes cannot tell text/plain from text/markdown or application/json,
        # so asserting an exact match here would be a lie. The family is the honest
        # claim: bytes that do not decode as clean text are not text, whatever the
        # declaration says (that check already happened — `sniffed` would be None).
        if not sniffed.startswith("text/"):
            failures.append(
                HygieneFailure(
                    code=HygieneCode.MIME_MISMATCH,
                    detail=f"text payload carries {sniffed!r} bytes",
                )
            )
        return failures
    if sniffed != payload.mime_type:
        failures.append(
            HygieneFailure(
                code=HygieneCode.MIME_MISMATCH,
                detail=f"declared {payload.mime_type!r} but the bytes are {sniffed!r}",
            )
        )
    if not sniffed.startswith(_expected_prefix(payload.kind)):
        failures.append(
            HygieneFailure(
                code=HygieneCode.MIME_MISMATCH,
                detail=f"{payload.kind.value} payload carries {sniffed!r} bytes",
            )
        )
        return failures
    allowed = {
        MediaKind.IMAGE: limits.allowed_image_mimes,
        MediaKind.AUDIO: limits.allowed_audio_mimes,
    }.get(payload.kind)
    if allowed is not None and sniffed not in allowed:
        failures.append(
            HygieneFailure(
                code=HygieneCode.MIME_NOT_ALLOWED,
                detail=f"{sniffed!r} is not in the accepted {payload.kind.value} set",
            )
        )
    return failures


def _bomb_failures(
    payload: MediaPayload, sniffed: str | None, limits: MediaLimits, dims: tuple[int, int] | None
) -> list[HygieneFailure]:
    """Apply the decompression-bomb guards to an image whose header was read."""
    if payload.kind is not MediaKind.IMAGE or sniffed not in limits.allowed_image_mimes:
        return []
    if dims is None:
        return [
            HygieneFailure(
                code=HygieneCode.DIMENSIONS_UNREADABLE,
                detail=f"cannot read {sniffed} dimensions from the header; "
                "the decompression-bomb guard cannot run, so the image is refused",
            )
        ]
    width, height = dims
    pixels = width * height
    failures: list[HygieneFailure] = []
    if pixels > limits.max_pixels:
        failures.append(
            HygieneFailure(
                code=HygieneCode.BOMB_PIXELS,
                detail=f"header declares {width}x{height} = {pixels} pixels, "
                f"over the {limits.max_pixels}-pixel cap",
            )
        )
    size = payload.byte_size or 0
    if size > 0 and pixels / size > limits.max_pixels_per_byte:
        failures.append(
            HygieneFailure(
                code=HygieneCode.BOMB_RATIO,
                detail=f"{pixels} pixels from {size} bytes is "
                f"{pixels / size:.0f} pixels/byte, over the "
                f"{limits.max_pixels_per_byte} cap (decompression bomb)",
            )
        )
    return failures


def inspect_payload(
    payload: MediaPayload, *, limits: MediaLimits | None = None
) -> HygieneReport:
    """Run every hygiene check over ``payload`` and report all failures.

    Pure, offline, and cheap — this is the first thing any media rail does, so a
    hostile payload is refused before it costs a model call.

    Args:
        payload: The payload to inspect.
        limits: Thresholds to apply; :class:`MediaLimits` defaults when omitted.

    Returns:
        A :class:`HygieneReport`. ``ok=False`` must be treated as a hard block.
    """
    limits = limits or MediaLimits()

    if not payload.inline:
        # Bytes this process does not hold cannot be sniffed, sized, or
        # bomb-checked. Text may legitimately be referenced by URI upstream, but
        # an unscreenable *image* is exactly the hole this module closes: fail closed.
        if payload.kind is MediaKind.TEXT:
            return HygieneReport(ok=True, failures=[], sniffed_mime=None)
        return HygieneReport(
            ok=False,
            failures=[
                HygieneFailure(
                    code=HygieneCode.NOT_INSPECTABLE,
                    detail=f"{payload.kind.value} payload is a bare URI ({payload.uri!r}); "
                    "its bytes were never fetched, so no rail can screen it",
                )
            ],
        )

    data = payload.data or b""
    if not data:
        return HygieneReport(
            ok=False,
            failures=[HygieneFailure(code=HygieneCode.EMPTY, detail="payload holds zero bytes")],
        )

    sniffed = sniff_mime(data)
    dims = image_dimensions(data, sniffed) if payload.kind is MediaKind.IMAGE else None
    failures = [
        *_size_failures(payload, limits),
        *_mime_failures(payload, sniffed, limits),
        *_bomb_failures(payload, sniffed, limits, dims),
    ]
    return HygieneReport(
        ok=not failures, failures=failures, sniffed_mime=sniffed, dimensions=dims
    )
