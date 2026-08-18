"""The Docling seam — the one module in the platform that may import a PDF parser.

Everything above this line reads :class:`~aegis.ingestion.blocks.ParsedDocument`.
Nothing above this line has ever seen a ``DoclingDocument``, a ``ProvenanceItem`` or a
``ConversionResult``, and that is enforced by there being no import path to them: the
Docling imports in this module are function-local, behind
:func:`aegis.core.lazy.require`, so ``aegis.ingestion`` is importable — and its
furniture, probe and block logic fully testable — on a machine with no PDF stack
installed at all.

The reason is not tidiness. Phase 4 D1 chose the standard layout+TableFormer pipeline
over the VLM one on a **255× throughput** difference and explicitly reserves the right
to revisit that the moment throughput stops binding. A seam that a chunker, a citation
renderer and a UI have all reached through is a seam that cannot be re-decided.

What this module configures, and why each setting is not the default
--------------------------------------------------------------------

**D2 — heading hierarchy needs both switches.** Docling ships
``heading_hierarchy_options.enabled = False``; its layout model emits ``SECTION_HEADER``
with no level, so every heading lands at level 1. Turning that option on *alone*
produces the dangerous middle state — a plausible-looking, partly-flattened tree with no
error anywhere. ``generate_parsed_pages = True`` is what supplies the font and layout
evidence the hierarchy is inferred from. Both, or the result is quietly wrong, which is
why :attr:`~aegis.ingestion.blocks.ParsedDocument.heading_histogram` is recorded on
every parse rather than trusted once at configuration time. **The configuration is not
the test; the histogram is.**

**D3 — OCR is decided per document.** ``do_ocr`` is a pipeline-wide switch and OCR is
the single most expensive thing in the parse (88% of runtime on a born-digital file), so
the decision comes from :func:`aegis.ingestion.probe.probe_text_layer` before the parser
starts. Because the switch is baked into the pipeline, there is one cached converter per
decision; the OCR variant is only built when a document actually needs it.

**D3b — TableFormer stays on ACCURATE**, which is Docling 2.120's default and is
asserted here rather than assumed, because it is the setting a future default change
would silently take away. ACCURATE costs roughly +0.8 s per table over FAST, and that
lands on the ingest clock — the cheap one. A mis-parsed table is a wrong answer with a
confident citation, and no amount of reranking recovers it.

**D4 — the converter is warmed at startup, not on first request.** Cold start is
seconds of model loading that would otherwise be paid by the first upload of the day,
which on demo day is a jury handing us a document. :func:`warm_converter` exists for the
host to call from its worker bootstrap; the cache it fills is process-wide and guarded
by a lock, because the warm-up runs in a thread while an activity may already be asking
for the same converter.

OpenMP: the parser and the ML spine cannot share a process unbrokered
---------------------------------------------------------------------

Docling pulls **torch**, which ships its own ``libomp``. This platform also runs
**xgboost**, which ships another. Loading both into one process on macOS/arm64 breaks,
and it breaks in whichever direction the imports happen to fall — **measured
2026-08-18**:

* ``import torch`` first, then an xgboost fit → **segmentation fault** inside
  ``xgboost.core.set_label`` (``KMP_DUPLICATE_LIB_OK=TRUE`` does *not* help);
* xgboost first, then torch → **deadlock** on the first ``torch`` op; a 512×512 matmul
  never returns.

Neither raises anything a caller could catch, and the API process is exactly where both
end up: the ML spine is warmed in the lifespan and the in-process worker runs the parse.
The one setting that fixes both directions is ``OMP_NUM_THREADS=1``, applied **before
torch is first imported** — which is what :func:`_require_docling` does, since this module
is the only door torch can come through. Cost, measured on the 16-page fixture: 7.6 s →
8.0 s, +5% on the ingest clock, which is the cheap one.

``setdefault``, not assignment: a deployment that has already chosen a value has chosen
it deliberately, and this is the wrong module to overrule it from.

Failure is loud
---------------

A conversion that Docling reports as failed raises :class:`ParseError` here rather than
returning an empty document. An ingest that "succeeded" with no blocks would advance the
job's ``completed_stage`` past work that did not happen, and every later stage would
faithfully process nothing.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from importlib import metadata
from pathlib import Path
from typing import Any

from aegis.core.lazy import require
from aegis.ingestion.blocks import BBox, BlockKind, ParsedBlock, ParsedDocument, ParsedPage
from aegis.ingestion.furniture import strip_running_furniture
from aegis.ingestion.probe import (
    INGESTION_EXTRA,
    MIN_TEXT_PAGE_RATIO,
    OcrDecision,
    decide_ocr,
    probe_text_layer,
)

logger = logging.getLogger(__name__)

__all__ = [
    "OCR_LANGUAGE",
    "ParseError",
    "parse_pdf",
    "parser_version",
    "reset_converters",
    "warm_converter",
]

#: Docling label -> our kind. Anything unlisted becomes ``OTHER`` and keeps its text;
#: dropping an unknown label would lose content on the next Docling release.
_KIND_BY_LABEL = {
    "title": BlockKind.HEADING,
    "section_header": BlockKind.HEADING,
    "text": BlockKind.TEXT,
    "paragraph": BlockKind.TEXT,
    "list_item": BlockKind.LIST_ITEM,
    "table": BlockKind.TABLE,
    "caption": BlockKind.CAPTION,
    "footnote": BlockKind.FOOTNOTE,
    "formula": BlockKind.FORMULA,
    "code": BlockKind.CODE,
    "page_header": BlockKind.PAGE_HEADER,
    "page_footer": BlockKind.PAGE_FOOTER,
    "reference": BlockKind.TEXT,
    "checkbox_selected": BlockKind.OTHER,
    "checkbox_unselected": BlockKind.OTHER,
    "form": BlockKind.OTHER,
    "key_value_region": BlockKind.OTHER,
    "document_index": BlockKind.OTHER,
}

#: Labels with no retrievable text of their own; their captions arrive as separate items.
_SKIPPED_LABELS = frozenset({"picture", "chart", "picture_group"})

#: The OCR engine's recognition language. Named rather than left to Docling's ``auto``
#: engine selection (D3): ``auto`` resolves to whichever engine happens to be installed,
#: so the same document could be read by different models on the laptop and on the demo
#: box, with different quality and no log line saying so. RapidOCR's own default is
#: ``chinese``; this is the line to change if the corpus on the day is not English.
OCR_LANGUAGE = "english"

_CONVERTERS: dict[bool, Any] = {}
_CONVERTER_LOCK = threading.Lock()


def _require_docling(module: str) -> Any:  # noqa: ANN401 - a Docling module, kept opaque
    """Import a Docling module, pinning OpenMP to one thread first.

    Every Docling import in this module goes through here, because the pin only works if
    it happens before torch loads and a second import path would eventually be added
    that skipped it. See the module docstring for the two crashes this prevents.

    Args:
        module: The Docling module to import.

    Returns:
        The imported module.

    Raises:
        ImportError: If the ingestion extra is not installed.
    """
    if os.environ.get("OMP_NUM_THREADS") is None:
        os.environ["OMP_NUM_THREADS"] = "1"
        logger.info(
            "OMP_NUM_THREADS pinned to 1 before loading Docling: torch and xgboost ship "
            "separate OpenMP runtimes, and one process holding both either segfaults or "
            "deadlocks depending on import order. Costs ~5% of parse wall clock."
        )
    return require(INGESTION_EXTRA, module)


class ParseError(RuntimeError):
    """Raised when Docling could not convert the document.

    Not degraded into an empty :class:`~aegis.ingestion.blocks.ParsedDocument`: a
    document that produced no blocks but reported success is indistinguishable
    downstream from a document that genuinely says nothing.
    """


def parser_version() -> str:
    """Return the parser name and version this process would parse with.

    Recorded on every :class:`~aegis.ingestion.blocks.ParsedDocument` because a re-parse
    under a different Docling release is a different result — reading order, heading
    levels and table structure all move — and chunks from two versions are not
    interchangeable.

    Returns:
        e.g. ``"docling 2.120.3"``, or ``"docling (version unknown)"`` if the
        distribution metadata is missing (an editable checkout, typically).
    """
    try:
        return f"docling {metadata.version('docling')}"
    except metadata.PackageNotFoundError:
        return "docling (version unknown)"


def _pdf_format_option(*, do_ocr: bool) -> tuple[Any, Any]:
    """Build the PDF pipeline options this platform parses with.

    Args:
        do_ocr: Whether this pipeline runs OCR — the D3 per-document decision, baked in
            because Docling's switch is per pipeline, not per call.

    Returns:
        The ``InputFormat`` enum and the ``PdfFormatOption`` to register against it.

    Raises:
        ParseError: If TableFormer is not on ``ACCURATE`` after configuration (D3b).
    """
    base_models = _require_docling("docling.datamodel.base_models")
    pipeline_options = _require_docling("docling.datamodel.pipeline_options")
    document_converter = _require_docling("docling.document_converter")

    options = pipeline_options.PdfPipelineOptions()
    options.do_ocr = do_ocr
    options.ocr_options = pipeline_options.RapidOcrOptions(lang=[OCR_LANGUAGE])
    options.do_table_structure = True
    options.table_structure_options.mode = pipeline_options.TableFormerMode.ACCURATE
    # D2: both, or the heading tree is silently partial.
    options.heading_hierarchy_options.enabled = True
    options.generate_parsed_pages = True
    if options.table_structure_options.mode is not pipeline_options.TableFormerMode.ACCURATE:
        raise ParseError("TableFormer is not on ACCURATE; tables would be parsed by the fast model")
    return base_models.InputFormat.PDF, document_converter.PdfFormatOption(
        pipeline_options=options
    )


def _converter(*, do_ocr: bool) -> Any:  # noqa: ANN401 - a Docling type, deliberately opaque
    """Return the process-wide converter for this OCR decision, building it once.

    Args:
        do_ocr: Which pipeline is wanted.

    Returns:
        The cached ``DocumentConverter``. It never leaves this module.
    """
    with _CONVERTER_LOCK:
        cached = _CONVERTERS.get(do_ocr)
        if cached is not None:
            return cached
        document_converter = _require_docling("docling.document_converter")
        input_format, format_option = _pdf_format_option(do_ocr=do_ocr)
        converter = document_converter.DocumentConverter(
            format_options={input_format: format_option}
        )
        _CONVERTERS[do_ocr] = converter
        return converter


def warm_converter(*, ocr: bool = False) -> float:
    """Load the layout and table models now, so the first upload does not pay for them.

    Blocking and CPU-bound — the host calls it from a thread at worker startup (D4).
    Warming the ``ocr=False`` pipeline is the default because the probe sends almost
    every real document there; the OCR pipeline is built on demand by the first document
    that needs it, rather than holding a second set of models resident for a case that
    may never arrive.

    Args:
        ocr: Which pipeline to warm.

    Returns:
        Seconds spent. Recorded rather than discarded: the cold-start cost is a number
        the phase notes carry, and a warm-up that suddenly takes 4× longer is the first
        sign the model cache is being re-downloaded on every boot.
    """
    base_models = _require_docling("docling.datamodel.base_models")
    started = time.perf_counter()
    _converter(do_ocr=ocr).initialize_pipeline(base_models.InputFormat.PDF)
    elapsed = time.perf_counter() - started
    logger.info("Docling converter warmed (ocr=%s) in %.1fs — %s", ocr, elapsed, parser_version())
    return elapsed


def reset_converters() -> None:
    """Drop the cached converters, releasing the models they hold.

    For tests, and for a host that wants the ~2 GB back when ingestion is idle.
    """
    with _CONVERTER_LOCK:
        _CONVERTERS.clear()


def _bbox_of(item: Any, page_heights: dict[int, float]) -> tuple[int | None, BBox | None]:  # noqa: ANN401
    """Return the page and top-left-origin box for a Docling item.

    Args:
        item: A Docling document item.
        page_heights: Page number to page height, for the origin flip.

    Returns:
        The 1-based page number and the box, either of which is ``None`` when Docling
        reported no provenance — recorded honestly rather than guessed.
    """
    prov = getattr(item, "prov", None)
    if not prov:
        return None, None
    page_no = int(prov[0].page_no)
    height = page_heights.get(page_no)
    if height is None:
        return page_no, None
    box: BBox | None = None
    for entry in prov:
        if int(entry.page_no) != page_no:
            continue
        native = entry.bbox.to_top_left_origin(page_height=height)
        current = BBox(
            left=float(native.l),
            top=float(native.t),
            right=float(native.r),
            bottom=float(native.b),
        )
        box = current if box is None else box.merge(current)
    return page_no, box


def _label_of(item: Any) -> str:  # noqa: ANN401 - a Docling enum, read as its string value
    """Return a Docling item's label as a plain string.

    Args:
        item: A Docling document item.

    Returns:
        The label value, e.g. ``"section_header"``; ``""`` when the item has none.
    """
    label = getattr(item, "label", None)
    if label is None:
        return ""
    return str(getattr(label, "value", label))


def _text_of(item: Any, document: Any, label: str) -> tuple[str, tuple[int, int] | None]:  # noqa: ANN401
    """Return the retrievable text of an item, and a table's shape when it is one.

    Args:
        item: A Docling document item.
        document: The owning ``DoclingDocument`` — a table renders against it.
        label: The item's Docling label.

    Returns:
        The text and, for a table, ``(rows, columns)``.
    """
    if label == "table":
        data = getattr(item, "data", None)
        shape = (
            (int(data.num_rows), int(data.num_cols))
            if data is not None and hasattr(data, "num_rows")
            else None
        )
        return item.export_to_markdown(document).strip(), shape
    return str(getattr(item, "text", "") or "").strip(), None


def _blocks_from(document: Any, page_heights: dict[int, float]) -> tuple[ParsedBlock, ...]:  # noqa: ANN401
    """Walk the Docling tree in reading order and build our blocks.

    The heading stack is maintained here rather than recomputed later: the tree is only
    available on this side of the seam, and a heading path reconstructed downstream from
    flat blocks would be a guess.

    Args:
        document: The converted ``DoclingDocument``.
        page_heights: Page number to page height, for the bbox origin flip.

    Returns:
        Every block that carries text, in reading order.
    """
    blocks: list[ParsedBlock] = []
    stack: list[tuple[int, str]] = []
    for item, _tree_level in document.iterate_items():
        label = _label_of(item)
        if label in _SKIPPED_LABELS:
            continue
        kind = _KIND_BY_LABEL.get(label, BlockKind.OTHER)
        text, table_shape = _text_of(item, document, label)
        if not text:
            continue
        page_no, bbox = _bbox_of(item, page_heights)
        if page_no is None:
            # Group and body nodes have no page. They contribute no citable text, and
            # inventing a page number for one would put a citation on the wrong page.
            continue
        level: int | None = None
        if kind is BlockKind.HEADING:
            level = int(getattr(item, "level", 0) or 0) or 1
            while stack and stack[-1][0] >= level:
                stack.pop()
        blocks.append(
            ParsedBlock(
                kind=kind,
                text=text,
                page_no=page_no,
                bbox=bbox,
                level=level,
                heading_path=tuple(heading for _, heading in stack),
                table_shape=table_shape,
            )
        )
        if level is not None:
            stack.append((level, text))
    return tuple(blocks)


def parse_pdf(
    source: Path | str,
    *,
    ocr: bool | None = None,
    min_text_page_ratio: float = MIN_TEXT_PAGE_RATIO,
    strip_furniture: bool = True,
) -> ParsedDocument:
    """Parse a PDF into blocks that carry their page and bounding box.

    Args:
        source: Path to the PDF.
        ocr: Force OCR on or off. ``None`` — the normal case — decides per document from
            the text-layer probe (D3).
        min_text_page_ratio: Fraction of pages that must carry a text layer for OCR to
            stay off.
        strip_furniture: Remove running headers, footers and page numbers (task 4.2).

    Returns:
        The parsed document, with the OCR decision and anything stripped recorded on it.

    Raises:
        ParseError: If Docling reports the conversion as failed.
        TextLayerProbeError: If the file cannot be opened to be probed.
    """
    path = Path(source)
    if ocr is None:
        decision = decide_ocr(probe_text_layer(path), min_ratio=min_text_page_ratio)
    else:
        decision = OcrDecision(
            enabled=ocr,
            reason=f"OCR {'on' if ocr else 'off'} — set explicitly by the caller",
        )

    converter = _converter(do_ocr=decision.enabled)
    started = time.perf_counter()
    result = converter.convert(path, raises_on_error=False)
    parse_seconds = time.perf_counter() - started

    status = str(getattr(result.status, "value", result.status))
    if status not in {"success", "partial_success"}:
        errors = "; ".join(str(error) for error in getattr(result, "errors", ())) or "no detail"
        raise ParseError(f"Docling failed to convert {path.name} ({status}): {errors}")
    if status == "partial_success":
        logger.warning(
            "Docling partially converted %s; some pages produced no content: %s",
            path.name,
            "; ".join(str(error) for error in getattr(result, "errors", ())) or "no detail",
        )

    document = result.document
    page_heights = {int(no): float(page.size.height) for no, page in document.pages.items()}
    probe_chars = decision.probe.page_chars if decision.probe is not None else ()
    pages = tuple(
        ParsedPage(
            page_no=int(no),
            width=float(page.size.width),
            height=float(page.size.height),
            char_count=probe_chars[int(no) - 1] if int(no) - 1 < len(probe_chars) else 0,
            has_text_layer=(
                probe_chars[int(no) - 1] >= decision.probe.min_chars_per_page
                if decision.probe is not None and int(no) - 1 < len(probe_chars)
                else False
            ),
        )
        for no, page in sorted(document.pages.items())
    )

    blocks = _blocks_from(document, page_heights)
    removed: tuple[Any, ...] = ()
    if strip_furniture:
        blocks, removed = strip_running_furniture(blocks, pages)
    return ParsedDocument(
        source_name=path.name,
        pages=pages,
        blocks=blocks,
        ocr=decision,
        removed_furniture=removed,
        parse_seconds=parse_seconds,
        parser=parser_version(),
    )
