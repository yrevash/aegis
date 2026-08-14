"""Image PII for the vision path — the same rail, plus the boxes for the console.

:func:`aegis.guardrails.media.image_pii.redact_image` is the rail, and this
module does **not** reimplement it: it calls it. What it adds is the one thing
the rail deliberately does not return — *where* the personal data was, so the
console can draw the detected regions over the image the operator uploaded.
A user who is told "we found an EMAIL_ADDRESS in your screenshot" and cannot see
where has been given a claim, not evidence.

**The honest cost of not owning the rail.** Presidio's analyzer reports the
boxes; ``redact_image`` runs its own analyze pass internally and returns only
entity *kinds*. Getting the boxes without forking the rail therefore means one
extra analyze pass on images that actually carry PII (clean images are analysed
once). OCR is deterministic on identical bytes, so the boxes the console draws
are exactly the boxes the rail painted. The alternative — reimplementing the
redaction here with our own rectangles — would give two redaction code paths to
keep honest, which is worse than one extra pass on the rare hit.
"""

from __future__ import annotations

import io
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aegis.core.lazy import require
from aegis.guardrails.media.image_pii import default_analyzer, redact_image
from aegis.media import ImagePayload
from aegis.vision.types import PIIRegion


class ImagePIIScan(BaseModel):
    """What the image-PII rail found, and the payload to send onward.

    Attributes:
        entities: Presidio entity kinds found (kinds only — never the values).
        regions: Where they were found, in source-image pixel space.
        payload: The image to hand the analysis model. The redacted rewrite when
            anything was found, otherwise the original object unchanged.
        redacted: Whether pixels were actually painted over.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    entities: list[str] = Field(default_factory=list)
    regions: list[PIIRegion] = Field(default_factory=list)
    payload: ImagePayload
    redacted: bool = False


def _as_region(finding: Any) -> PIIRegion | None:  # noqa: ANN401 - duck-typed Presidio result
    """Convert one Presidio ``ImageRecognizerResult`` to a :class:`PIIRegion`.

    Returns ``None`` for a finding that does not carry a complete box. A
    detection whose geometry cannot be read is still reported through
    ``entities`` — it is only the *overlay* that has nothing to draw, and
    inventing a rectangle would be worse than drawing none.
    """
    entity = getattr(finding, "entity_type", None)
    if not isinstance(entity, str):
        return None
    box = [getattr(finding, side, None) for side in ("left", "top", "width", "height")]
    if any(not isinstance(value, int | float) for value in box):
        return None
    left, top, width, height = box
    score = getattr(finding, "score", None)
    return PIIRegion(
        entity_type=entity,
        left=int(left),  # type: ignore[arg-type]
        top=int(top),  # type: ignore[arg-type]
        width=int(width),  # type: ignore[arg-type]
        height=int(height),  # type: ignore[arg-type]
        score=float(score) if isinstance(score, int | float) else None,
    )


def scan_and_redact(
    payload: ImagePayload,
    *,
    analyzer: Any = None,  # noqa: ANN401 - duck-typed Presidio engine (test seam)
    redactor: Any = None,  # noqa: ANN401 - duck-typed Presidio engine (test seam)
) -> ImagePIIScan:
    """Locate PII burned into ``payload`` and return the redacted image plus boxes.

    Args:
        payload: The inline image that already cleared the injection screen.
        analyzer: A Presidio ``ImageAnalyzerEngine`` (or stub exposing ``analyze``).
            Omit to build the real engine, which raises :class:`ImportError`
            naming the install command when ``aegis[media]`` is missing.
        redactor: A Presidio ``ImageRedactorEngine`` (or stub exposing ``redact``).

    Returns:
        An :class:`ImagePIIScan`.

    Raises:
        ValueError: If the payload holds no inline bytes.
        ImportError: If ``aegis[media]`` is required and missing — the rail was
            asked for and cannot run, which fails loud rather than quietly doing
            nothing.
    """
    if payload.data is None:
        raise ValueError("image PII needs inline bytes; got a bare URI payload")

    engine = analyzer if analyzer is not None else default_analyzer()
    image = require("aegis[media]", "PIL.Image").open(io.BytesIO(payload.data))
    findings = engine.analyze(image) or []

    regions = [region for region in (_as_region(f) for f in findings) if region is not None]
    entities = sorted({getattr(f, "entity_type", None) or "" for f in findings} - {""})
    if not entities:
        return ImagePIIScan(entities=[], regions=[], payload=payload, redacted=False)

    # Delegate the actual painting to the existing rail — one redaction
    # implementation in the codebase, not two (see the module docstring).
    result = redact_image(payload, analyzer=engine, redactor=redactor)
    return ImagePIIScan(
        entities=result.entities or entities,
        regions=regions,
        payload=result.payload,
        redacted=result.redacted,
    )
