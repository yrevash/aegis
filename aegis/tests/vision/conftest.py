"""Shared fakes for the aegis.vision suite — no network, no model, no OCR stack.

Everything the pipeline talks to is injected, so the whole module is testable
against stubs. That is the point of the seams, and it is also the honest limit of
this suite: it proves the *ordering and the verdicts*, not that the hosted
Llama-3.2-90B-Vision deployment returns good analyses (the gateway credential in
this repo is a placeholder, so no live fleet call is possible here).
"""

from __future__ import annotations

import base64
import io
import json
import zlib
from dataclasses import dataclass

import pytest

from aegis.core.types import GuardResult, GuardVerdict
from aegis.media import ImagePayload, MediaSource, Provenance
from aegis.vision import AnalystReply, VisionUsage

#: A real, valid 1×1 PNG — small enough to inline, real enough that magic-byte
#: sniffing and the header dimension read both succeed.
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGA"
    "hKmMIQAAAABJRU5ErkJggg=="
)


def png_payload(*, source: MediaSource = MediaSource.USER_UPLOAD) -> ImagePayload:
    """A clean, inline PNG payload that clears hygiene."""
    return ImagePayload(
        data=PNG_1X1,
        mime_type="image/png",
        provenance=Provenance(source=source, origin="test.png"),
    )


def bomb_payload() -> ImagePayload:
    """A tiny PNG whose header declares 40 000 × 40 000 — a decompression bomb.

    Built by hand rather than by a codec: the whole point is that the *header*
    lies about the pixel count, and no honest encoder would produce this.
    """
    ihdr = (
        b"\x00\x00\x9c\x40"  # width  40000
        b"\x00\x00\x9c\x40"  # height 40000
        b"\x08\x02\x00\x00\x00"
    )
    chunk = b"IHDR" + ihdr
    data = (
        b"\x89PNG\r\n\x1a\n"
        + len(ihdr).to_bytes(4, "big")
        + chunk
        + zlib.crc32(chunk).to_bytes(4, "big")
    )
    return ImagePayload(data=data, mime_type="image/png")


class FakeScreen:
    """A vision completer standing in for the injection screen's model."""

    def __init__(self, *, injection: bool, contains_text: bool = True, reason: str = "") -> None:
        self.injection = injection
        self.contains_text = contains_text
        self.reason = reason or ("rendered instructions for an AI" if injection else "benign")
        self.calls: list[list[dict]] = []

    async def __call__(self, messages: list[dict], *, response_format: dict | None = None) -> str:
        self.calls.append(messages)
        return json.dumps(
            {
                "contains_text": self.contains_text,
                "injection": self.injection,
                "reason": self.reason,
            }
        )


class ExplodingScreen:
    """A screen completer that raises — the classifier must fail closed on it."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, messages: list[dict], *, response_format: dict | None = None) -> str:
        self.calls += 1
        raise RuntimeError("screen deployment unreachable")


class RecordingAnalyst:
    """The main vision call. Records every invocation so ordering is provable."""

    def __init__(self, text: str = "A single white pixel.") -> None:
        self.text = text
        self.calls: list[list[dict]] = []

    async def __call__(self, messages: list[dict]) -> AnalystReply:
        self.calls.append(messages)
        return AnalystReply(
            text=self.text,
            usage=VisionUsage(
                model="genailab-maas-Llama-3.2-90B-Vision-Instruct",
                prompt_tokens=812,
                completion_tokens=64,
                images=1,
                cost_usd=0.00243,
                cost_source="provider",
            ),
        )


@dataclass
class FakeFinding:
    """A stand-in for Presidio's ``ImageRecognizerResult``."""

    entity_type: str
    left: int
    top: int
    width: int
    height: int
    score: float = 0.85


class FakeAnalyzer:
    """A Presidio ``ImageAnalyzerEngine`` stub returning fixed findings."""

    def __init__(self, findings: list[FakeFinding]) -> None:
        self.findings = findings
        self.calls = 0

    def analyze(self, image: object) -> list[FakeFinding]:
        self.calls += 1
        return list(self.findings)


class FakeRedactor:
    """A Presidio ``ImageRedactorEngine`` stub returning a distinct image."""

    def __init__(self) -> None:
        self.calls = 0

    def redact(self, image: object, fill: tuple[int, int, int] = (0, 0, 0)) -> object:
        self.calls += 1
        from PIL import Image

        return Image.new("RGB", (2, 2), fill)


def passing_rails(text: str) -> GuardResult:
    """A PASS verdict from the text output rails."""
    return GuardResult(verdict=GuardVerdict.PASS, reason="Output passed every rail.", text=text)


@pytest.fixture
def clean_png() -> ImagePayload:
    """A clean inline PNG payload."""
    return png_payload()


@pytest.fixture
def analyst() -> RecordingAnalyst:
    """A recording vision analyst."""
    return RecordingAnalyst()


def png_bytes_of(image: object) -> bytes:
    """Encode a PIL image to PNG bytes (used to assert a rewritten payload)."""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")  # type: ignore[attr-defined]
    return buffer.getvalue()
