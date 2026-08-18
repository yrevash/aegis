"""Aegis ingestion — PDFs in, structured blocks with page and bbox out.

The package boundary is the point. :mod:`aegis.ingestion.convert` is the only module in
the platform permitted to import Docling, and it hands back
:class:`~aegis.ingestion.blocks.ParsedDocument` — our own frozen dataclasses, which owe
nothing to the parser that filled them. Everything downstream (the chunker, the enricher,
the citation renderer, the console) reads those, so Phase 4 D1's reserved right to
re-decide the parser stays a change to one module rather than to the pipeline.

The rest of the package is deliberately parser-free and therefore testable with no PDF
stack installed at all. :mod:`aegis.ingestion.probe` reads the raw text layer through
PDFium to decide OCR per document (D3); :mod:`aegis.ingestion.furniture` removes the
running headers and page numbers that would otherwise repeat in every chunk and trip the
retrieval dedup; and :mod:`aegis.ingestion.quality` scores a finished parse against that
same independent text layer (D-parse), because a parser that reads a document in the
wrong order does not raise, it just answers wrongly for ever.

**No orchestration lives here.** Warming, scheduling and the stage handler are the host's
(``app.ingestion``), exactly as ``aegis.jobs`` declares stages while ``app.jobs`` runs
them.
"""

from __future__ import annotations

from aegis.ingestion.blocks import (
    BBox,
    BlockKind,
    FurnitureRun,
    ParsedBlock,
    ParsedDocument,
    ParsedPage,
)
from aegis.ingestion.convert import (
    ParseError,
    parse_pdf,
    parser_version,
    reset_converters,
    warm_converter,
)
from aegis.ingestion.furniture import normalise_furniture_text, strip_running_furniture
from aegis.ingestion.probe import (
    INGESTION_EXTRA,
    MIN_CHARS_PER_PAGE,
    MIN_TEXT_PAGE_RATIO,
    OcrDecision,
    TextLayerProbe,
    TextLayerProbeError,
    decide_ocr,
    probe_page_text,
    probe_text_layer,
)
from aegis.ingestion.quality import LOW_CONFIDENCE, ParseQuality, assess_parse

__all__ = [
    "INGESTION_EXTRA",
    "LOW_CONFIDENCE",
    "MIN_CHARS_PER_PAGE",
    "MIN_TEXT_PAGE_RATIO",
    "BBox",
    "BlockKind",
    "FurnitureRun",
    "OcrDecision",
    "ParseError",
    "ParsedBlock",
    "ParseQuality",
    "ParsedDocument",
    "ParsedPage",
    "TextLayerProbe",
    "TextLayerProbeError",
    "assess_parse",
    "decide_ocr",
    "normalise_furniture_text",
    "parse_pdf",
    "parser_version",
    "probe_page_text",
    "probe_text_layer",
    "reset_converters",
    "strip_running_furniture",
    "warm_converter",
]
