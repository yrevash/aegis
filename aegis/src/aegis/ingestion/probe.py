"""The text-layer probe — how one document decides whether OCR runs at all (D3).

Docling's ``do_ocr`` is a pipeline-wide switch, and left on it is the most expensive
thing in the parse by a wide margin: profiling a **fully born-digital** PDF showed OCR
consuming 33.5 s of 38.1 s — 88% of total runtime — because figure regions contain no
text cells and get sent to the OCR engine anyway. Turning it blanket-off is the obvious
next move and it is wrong for the opposite reason: a scanned document silently parses to
almost nothing, produces a handful of empty chunks, and answers every question about
itself with "not found".

So the decision is made per document, from evidence, before the parser starts: open the
PDF, count the characters the **raw text layer** already yields, and let that decide.
The probe costs ~0.4 s on a 126-page document because extracting a text layer is not
parsing — no layout model, no table model, no image rasterisation.

Two thresholds, and why they are where they are
-----------------------------------------------

:data:`MIN_CHARS_PER_PAGE` decides whether a *page* has text. It is not zero: a scanned
page is rarely empty, because a page number, a stamp or an OCR watermark from whoever
scanned it will yield a few characters. Sixty-four is about one short sentence — below
that there is nothing to retrieve.

:data:`MIN_TEXT_PAGE_RATIO` decides whether the *document* has text. At 0.8, a document
that is 90% digital with one scanned page gets no OCR and we lose that page. That is the
trade-off Phase 4 D3 states explicitly, and the reason :class:`OcrDecision` carries its
own evidence: the ingest log names the decision and the ratio behind it, so a document
that was read the wrong way is visible rather than silent.

**What would be better** is a per-*page* decision — OCR only the pages below the
threshold. Docling's pipeline switch is per-document, so that is a real change rather
than a constant, and it sits on the phase's more-time list rather than in it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aegis.core.lazy import require

__all__ = [
    "INGESTION_EXTRA",
    "MIN_CHARS_PER_PAGE",
    "MIN_TEXT_PAGE_RATIO",
    "OcrDecision",
    "TextLayerProbe",
    "TextLayerProbeError",
    "decide_ocr",
    "probe_page_text",
    "probe_text_layer",
]

#: The pip extra that carries the PDF stack.
INGESTION_EXTRA = "aegis[ingestion]"

#: Characters a page's text layer must yield to count as having one.
MIN_CHARS_PER_PAGE = 64

#: Fraction of pages that must have a text layer for the document to skip OCR.
MIN_TEXT_PAGE_RATIO = 0.8


class TextLayerProbeError(RuntimeError):
    """Raised when the PDF cannot be opened to be probed.

    Deliberately not caught into a default: a file we cannot open is not a file we know
    the OCR answer for, and guessing here would turn an encrypted or truncated upload
    into a document that parses to nothing and reports success.
    """


@dataclass(frozen=True, slots=True)
class TextLayerProbe:
    """What the raw text layer of each page yielded.

    Attributes:
        page_chars: Characters extracted per page, in page order.
        min_chars_per_page: The threshold a page had to clear.
    """

    page_chars: tuple[int, ...]
    min_chars_per_page: int = MIN_CHARS_PER_PAGE

    @property
    def page_count(self) -> int:
        """How many pages were probed."""
        return len(self.page_chars)

    @property
    def pages_with_text(self) -> int:
        """How many pages carry a usable text layer."""
        return sum(1 for chars in self.page_chars if chars >= self.min_chars_per_page)

    @property
    def text_page_ratio(self) -> float:
        """Fraction of pages carrying a text layer; ``0.0`` for an empty document."""
        if not self.page_chars:
            return 0.0
        return self.pages_with_text / len(self.page_chars)

    def pages_without_text(self) -> tuple[int, ...]:
        """Return the 1-based page numbers that carry no usable text layer.

        Returns:
            The page numbers, ascending. These are the pages a ``do_ocr=False``
            decision gives up on, which is why they are named rather than counted.
        """
        return tuple(
            index
            for index, chars in enumerate(self.page_chars, start=1)
            if chars < self.min_chars_per_page
        )


@dataclass(frozen=True, slots=True)
class OcrDecision:
    """Whether OCR ran, and the evidence that decided it.

    Attributes:
        enabled: Whether the parser was configured to OCR this document.
        reason: One line, written for the ingest log rather than for a developer.
        probe: The measurement behind the decision. ``None`` when the caller overrode
            the decision explicitly.
    """

    enabled: bool
    reason: str
    probe: TextLayerProbe | None = None


def probe_text_layer(source: Path | str) -> TextLayerProbe:
    """Count the characters each page's embedded text layer yields.

    Uses PDFium directly rather than the parser: this must be cheap enough to run before
    every parse, and it must be *independent* of Docling — the same independence the
    D-parse reading-order cross-check relies on. Extracting a text layer touches no
    model.

    Args:
        source: Path to the PDF.

    Returns:
        The per-page character counts.

    Raises:
        TextLayerProbeError: If the file cannot be opened or read as a PDF.
    """
    pdfium = require(INGESTION_EXTRA, "pypdfium2")
    path = Path(source)
    counts: list[int] = []
    try:
        document = pdfium.PdfDocument(path)
    except Exception as exc:  # noqa: BLE001 - pdfium raises several unrelated types
        raise TextLayerProbeError(
            f"cannot open {path.name} to probe its text layer: {exc}"
        ) from exc
    try:
        for index in range(len(document)):
            page = document[index]
            textpage = page.get_textpage()
            try:
                counts.append(textpage.count_chars())
            finally:
                textpage.close()
                page.close()
    except Exception as exc:  # noqa: BLE001 - a damaged page object raises from pdfium
        raise TextLayerProbeError(f"cannot read the text layer of {path.name}: {exc}") from exc
    finally:
        document.close()
    return TextLayerProbe(page_chars=tuple(counts))


def probe_page_text(source: Path | str) -> tuple[str, ...]:
    """Extract each page's embedded text layer, in the order the PDF stores it.

    This is the **independent** reading of the document that
    :func:`aegis.ingestion.quality.assess_parse` cross-checks Docling's block order
    against (D-parse). Independent is the operative word, and it is a property of *how*
    each order is arrived at rather than a claim about which tool is better: PDFium
    returns text in content-stream order — the order the producing application emitted
    it — while Docling's reading order comes from a rule-based geometric predictor over
    the bounding boxes its layout model found (``docling_ibm_models.reading_order``).
    Two different mechanisms over the same bytes, so when they disagree about *order*,
    at least one of them is wrong about the document.

    Neither is authoritative. A content stream can itself be emitted out of visual order
    — that is what makes this a flag rather than a veto; see
    :mod:`aegis.ingestion.quality`.

    Separate from :func:`probe_text_layer` rather than folded into it, because the text
    is wanted for exactly one purpose and is not wanted on the parse artifact: a
    126-page document's text layer is megabytes that the ``chunk`` stage has no use for.
    The second pass costs about the same as the first (~0.4 s on 126 pages, against a
    361 s parse), which is the honest price of not persisting it.

    Args:
        source: Path to the PDF.

    Returns:
        One string per page, in page order.

    Raises:
        TextLayerProbeError: If the file cannot be opened or read as a PDF.
    """
    pdfium = require(INGESTION_EXTRA, "pypdfium2")
    path = Path(source)
    texts: list[str] = []
    try:
        document = pdfium.PdfDocument(path)
    except Exception as exc:  # noqa: BLE001 - pdfium raises several unrelated types
        raise TextLayerProbeError(
            f"cannot open {path.name} to read its text layer: {exc}"
        ) from exc
    try:
        for index in range(len(document)):
            page = document[index]
            textpage = page.get_textpage()
            try:
                texts.append(textpage.get_text_range())
            finally:
                textpage.close()
                page.close()
    except Exception as exc:  # noqa: BLE001 - a damaged page object raises from pdfium
        raise TextLayerProbeError(f"cannot read the text layer of {path.name}: {exc}") from exc
    finally:
        document.close()
    return tuple(texts)


def decide_ocr(probe: TextLayerProbe, *, min_ratio: float = MIN_TEXT_PAGE_RATIO) -> OcrDecision:
    """Turn a probe into the per-document OCR decision, with its reason.

    Args:
        probe: The measurement from :func:`probe_text_layer`.
        min_ratio: Fraction of pages that must carry text for OCR to stay off.

    Returns:
        The decision, carrying the sentence the ingest log shows the tenant.
    """
    if probe.page_count == 0:
        return OcrDecision(
            enabled=True,
            reason="no pages could be probed, so OCR is on rather than reading nothing",
            probe=probe,
        )
    ratio = probe.text_page_ratio
    if ratio >= min_ratio:
        missing = probe.pages_without_text()
        detail = (
            f"; {len(missing)} page(s) without one are not OCR'd: "
            f"{', '.join(str(page) for page in missing[:10])}"
            if missing
            else ""
        )
        return OcrDecision(
            enabled=False,
            reason=(
                f"OCR off — {probe.pages_with_text}/{probe.page_count} pages "
                f"({ratio:.0%}) are born-digital{detail}"
            ),
            probe=probe,
        )
    return OcrDecision(
        enabled=True,
        reason=(
            f"OCR on — only {probe.pages_with_text}/{probe.page_count} pages "
            f"({ratio:.0%}) carry a text layer"
        ),
        probe=probe,
    )
