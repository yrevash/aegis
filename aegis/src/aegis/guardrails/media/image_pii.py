"""Image PII — detect and **actually redact** personal data burned into pixels.

The text path already runs Microsoft Presidio (``aegis[pii]``). Images need the
same engine one layer up: ``presidio-image-redactor`` OCRs the image, runs the
Presidio analyzer over the recognised text, and paints over the boxes it found.
It is the same vendor, the same recognisers, the same entity names — an
extension of a dependency Aegis already carries rather than a new one.

**Why this rail returns an image and not a verdict.** On text, "redact" is
actionable: the pipeline hands the caller the masked string. Emitting a bare
``REDACT`` verdict for an image would be theatre — the caller would still be
holding the original bytes with the passport number in them. So this rail
returns a **new** :class:`~aegis.media.ImagePayload` carrying the redacted PNG,
and the pipeline puts it on the result so the caller can forward *that* instead.
The original payload is frozen and untouched: what was screened stays exactly
what was screened.

**Optional and loud.** ``presidio-image-redactor`` pulls OCR (``pytesseract``,
and the ``tesseract`` binary) plus OpenCV, so it sits behind the ``aegis[media]``
extra and is imported lazily through :func:`aegis.core.lazy.require`. The rail is
opt-in; when an operator opts in and the extra is missing, the import raises with
the exact install command. It never degrades to "no redaction, verdict says
pass" — that is the failure mode this codebase's audit found and banned.
"""

from __future__ import annotations

import io
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aegis.core.lazy import require
from aegis.media import ImagePayload

logger = logging.getLogger(__name__)

#: Fill colour for redaction boxes (opaque black). A visible, unambiguous block —
#: blurring is reversible enough to be worth avoiding.
REDACTION_FILL: tuple[int, int, int] = (0, 0, 0)


class ImagePIIResult(BaseModel):
    """The outcome of the image-PII rail.

    Attributes:
        redacted: Whether anything was found and painted over.
        entities: Presidio entity types found (kinds only — never the values).
        payload: The redacted image when ``redacted`` is true, else the original.
        reason: Human-readable rationale for the trace.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    redacted: bool
    entities: list[str] = Field(default_factory=list)
    payload: ImagePayload
    reason: str = ""


def _pil_image() -> Any:  # noqa: ANN401 - the PIL.Image module
    """Return the lazily-imported ``PIL.Image`` module (part of ``aegis[media]``)."""
    return require("aegis[media]", "PIL.Image")


def default_analyzer() -> Any:  # noqa: ANN401 - presidio ImageAnalyzerEngine
    """Build Presidio's image analyzer, failing loud when the extra is missing."""
    module = require("aegis[media]", "presidio_image_redactor")
    return module.ImageAnalyzerEngine()


def default_redactor() -> Any:  # noqa: ANN401 - presidio ImageRedactorEngine
    """Build Presidio's image redactor, failing loud when the extra is missing."""
    module = require("aegis[media]", "presidio_image_redactor")
    return module.ImageRedactorEngine()


def _entity_names(results: object) -> list[str]:
    """Extract sorted, unique entity type names from a Presidio analyzer result.

    Only the *kinds* are kept. The recognised values are the PII — putting them in
    a verdict would leak exactly what the rail exists to protect.
    """
    names: set[str] = set()
    for item in results or []:  # type: ignore[union-attr]
        name = getattr(item, "entity_type", None)
        if isinstance(name, str):
            names.add(name)
    return sorted(names)


def redact_image(
    payload: ImagePayload,
    *,
    analyzer: Any = None,  # noqa: ANN401 - duck-typed Presidio engine (test seam)
    redactor: Any = None,  # noqa: ANN401 - duck-typed Presidio engine (test seam)
    fill: tuple[int, int, int] = REDACTION_FILL,
) -> ImagePIIResult:
    """Find PII in ``payload``'s pixels and return an image with it painted over.

    ``analyzer`` / ``redactor`` are injection seams, mirroring how every other rail
    in this package takes its model dependency as a parameter: the defaults build
    the real Presidio engines, and tests pass stubs so the rail's *logic* is tested
    without the OCR stack. Passing neither and not having ``aegis[media]``
    installed raises :class:`ImportError` naming the install command — the rail
    never silently does nothing.

    Args:
        payload: The inline image payload to screen.
        analyzer: A Presidio ``ImageAnalyzerEngine`` (or stub) exposing ``analyze``.
        redactor: A Presidio ``ImageRedactorEngine`` (or stub) exposing ``redact``.
        fill: RGB fill for the redaction boxes.

    Returns:
        An :class:`ImagePIIResult`. When nothing was found, ``payload`` is the
        original object, unchanged.

    Raises:
        ValueError: If the payload holds no inline bytes (nothing to OCR).
        ImportError: If the ``aegis[media]`` extra is required and missing.
    """
    if payload.data is None:
        raise ValueError("image PII redaction needs inline bytes; got a bare URI payload")

    image_module = _pil_image()
    analyzer = analyzer if analyzer is not None else default_analyzer()
    image = image_module.open(io.BytesIO(payload.data))

    findings = analyzer.analyze(image)
    entities = _entity_names(findings)
    if not entities:
        return ImagePIIResult(
            redacted=False,
            entities=[],
            payload=payload,
            reason="Image PII scan found no personal data in the rendered text.",
        )

    redactor = redactor if redactor is not None else default_redactor()
    redacted_image = redactor.redact(image, fill=fill)
    buffer = io.BytesIO()
    # Always re-encode as PNG: lossless (a redaction box must not be smeared by
    # JPEG artefacts) and a format the hygiene rail can dimension-check.
    redacted_image.save(buffer, format="PNG")
    new_payload = ImagePayload(
        data=buffer.getvalue(),
        mime_type="image/png",
        provenance=payload.provenance,
    )
    return ImagePIIResult(
        redacted=True,
        entities=entities,
        payload=new_payload,
        reason=f"Redacted PII burned into the image: {', '.join(entities)}.",
    )
