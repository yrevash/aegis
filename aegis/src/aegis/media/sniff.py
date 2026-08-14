"""Magic-byte MIME sniffing and image header parsing — never trust a declared type.

**Why.** ``Content-Type`` and the ``mime_type`` on a payload are supplied by
whoever sent the bytes. An attacker who wants an image past a text-only rail
declares it ``text/plain``; one who wants a payload past an image rail declares
it ``image/png``. The only honest answer comes from the bytes themselves, so
every hygiene decision in :mod:`aegis.media.hygiene` is made against
:func:`sniff_mime`, and the declared type is only ever used to detect the
*mismatch*.

**Why header parsing rather than decoding.** :func:`image_dimensions` reads the
declared pixel dimensions out of the container header without decompressing a
single pixel. That is precisely what a decompression-bomb guard must do: a 40 KB
PNG that expands to 40 000 × 40 000 pixels is 6.4 GB of RAM the moment anything
calls ``Image.open().load()``. Checking the header first means the guard costs
nothing and the bomb never detonates.

Coverage is a deliberate allowlist — PNG, JPEG, GIF, BMP, WEBP for images; WAV,
MP3, OGG, FLAC, MP4/M4A for audio. Anything else sniffs as ``None`` and the
hygiene rail fails closed on it. Stdlib only.
"""

from __future__ import annotations

import struct

#: Image container signatures: (offset, magic, mime). Ordered longest-first where
#: prefixes overlap so the more specific signature wins.
_IMAGE_MAGIC: tuple[tuple[int, bytes, str], ...] = (
    (0, b"\x89PNG\r\n\x1a\n", "image/png"),
    (0, b"\xff\xd8\xff", "image/jpeg"),
    (0, b"GIF87a", "image/gif"),
    (0, b"GIF89a", "image/gif"),
    (0, b"BM", "image/bmp"),
    (0, b"II*\x00", "image/tiff"),
    (0, b"MM\x00*", "image/tiff"),
)

#: Audio container signatures.
_AUDIO_MAGIC: tuple[tuple[int, bytes, str], ...] = (
    (0, b"ID3", "audio/mpeg"),
    (0, b"OggS", "audio/ogg"),
    (0, b"fLaC", "audio/flac"),
)

#: MIME types whose *pixel* dimensions this module can read from the header. An
#: image outside this set cannot be bomb-checked and therefore fails closed.
DIMENSIONABLE_IMAGE_MIMES: frozenset[str] = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/bmp", "image/webp"}
)


def _sniff_riff(data: bytes) -> str | None:
    """Resolve a RIFF container (WEBP and WAV share the outer ``RIFF`` magic)."""
    if len(data) < 12 or data[:4] != b"RIFF":
        return None
    form = data[8:12]
    if form == b"WEBP":
        return "image/webp"
    if form == b"WAVE":
        return "audio/wav"
    return None


def _sniff_iso_bmff(data: bytes) -> str | None:
    """Resolve an ISO base-media container (MP4/M4A) from its ``ftyp`` brand."""
    if len(data) < 12 or data[4:8] != b"ftyp":
        return None
    brand = data[8:12]
    if brand in {b"M4A ", b"M4B "}:
        return "audio/mp4"
    return "video/mp4"


def _looks_like_mp3_frame(data: bytes) -> bool:
    """Whether the buffer opens with an MPEG audio frame sync word."""
    return len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0


def _looks_like_utf8_text(data: bytes) -> bool:
    """Whether the buffer decodes as UTF-8 and holds no control characters.

    Deliberately strict: NUL and other C0 controls (bar tab/newline/carriage
    return) mean this is a binary blob wearing a text label, and the caller
    should get ``None`` rather than a confident wrong answer.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return all(ch in "\t\n\r" or ch >= " " for ch in text)


def sniff_mime(data: bytes) -> str | None:
    """Return the MIME type implied by ``data``'s magic bytes, or ``None``.

    Args:
        data: The payload bytes (only the first few are read).

    Returns:
        The sniffed MIME type, or ``None`` when the bytes match no known
        container. ``None`` is a *refusal to guess*, not a pass — callers must
        treat it as "unidentifiable" and fail closed for binary media.
    """
    if not data:
        return None
    for offset, magic, mime in (*_IMAGE_MAGIC, *_AUDIO_MAGIC):
        if data[offset : offset + len(magic)] == magic:
            return mime
    riff = _sniff_riff(data)
    if riff is not None:
        return riff
    iso = _sniff_iso_bmff(data)
    if iso is not None:
        return iso
    if _looks_like_mp3_frame(data):
        return "audio/mpeg"
    if _looks_like_utf8_text(data):
        return "text/plain"
    return None


def _png_dimensions(data: bytes) -> tuple[int, int] | None:
    """Read ``(width, height)`` from a PNG IHDR chunk."""
    if len(data) < 24 or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _gif_dimensions(data: bytes) -> tuple[int, int] | None:
    """Read ``(width, height)`` from a GIF logical screen descriptor."""
    if len(data) < 10:
        return None
    width, height = struct.unpack("<HH", data[6:10])
    return width, height


def _bmp_dimensions(data: bytes) -> tuple[int, int] | None:
    """Read ``(width, height)`` from a BMP DIB header (height may be negative)."""
    if len(data) < 26:
        return None
    width, height = struct.unpack("<ii", data[18:26])
    return abs(width), abs(height)


#: JPEG start-of-frame markers. ``C4``/``C8``/``CC`` are Huffman/arithmetic tables,
#: not frames, and must be skipped or the width/height read lands on garbage.
_JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """Walk JPEG segments to the first start-of-frame and read its dimensions."""
    index = 2  # skip SOI
    length = len(data)
    while index + 9 < length:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in _JPEG_SOF:
            height, width = struct.unpack(">HH", data[index + 5 : index + 9])
            return width, height
        if marker in {0xD8, 0x01} or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        segment = struct.unpack(">H", data[index + 2 : index + 4])[0]
        if segment < 2:
            return None
        index += 2 + segment
    return None


def _webp_dimensions(data: bytes) -> tuple[int, int] | None:
    """Read ``(width, height)`` from a WEBP VP8 / VP8L / VP8X chunk."""
    if len(data) < 30:
        return None
    chunk = data[12:16]
    if chunk == b"VP8 ":
        if data[23:26] != b"\x9d\x01\x2a":
            return None
        width, height = struct.unpack("<HH", data[26:30])
        return width & 0x3FFF, height & 0x3FFF
    if chunk == b"VP8L":
        bits = struct.unpack("<I", data[21:25])[0]
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8X":
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return width, height
    return None


def image_dimensions(data: bytes, mime: str | None = None) -> tuple[int, int] | None:
    """Return ``(width, height)`` read from the image header — no pixels decoded.

    Args:
        data: The image bytes.
        mime: The *sniffed* MIME type, if already known. When omitted it is
            sniffed here. Never pass the declared type: this function's answer
            feeds the decompression-bomb guard.

    Returns:
        The declared dimensions, or ``None`` when the format is outside
        :data:`DIMENSIONABLE_IMAGE_MIMES` or the header is truncated. ``None``
        means "cannot be bomb-checked", which the hygiene rail treats as a block.
    """
    resolved = mime or sniff_mime(data)
    if resolved == "image/png":
        return _png_dimensions(data)
    if resolved == "image/gif":
        return _gif_dimensions(data)
    if resolved == "image/bmp":
        return _bmp_dimensions(data)
    if resolved == "image/jpeg":
        return _jpeg_dimensions(data)
    if resolved == "image/webp":
        return _webp_dimensions(data)
    return None
