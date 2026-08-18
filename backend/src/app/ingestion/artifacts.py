"""Serialising a :class:`~aegis.ingestion.blocks.ParsedDocument` between two stages.

``parse`` runs on the CPU queue and ``chunk`` runs on the default queue: two activities,
two transactions, and — under the scale-out posture — two processes. The structured tree
the parse recovered has to cross that gap intact, because re-deriving it costs 0.4–3.2
seconds *per page* (measured, D1) and re-parsing on the way to a cheap stage is exactly
the waste stage-level resume exists to prevent.

**Why an explicit codec rather than :mod:`pickle` or a generic dataclass walk.** Two
reasons, and the second is the real one:

* A pickle of an application class is an artifact only *this build* can read. Ours has to
  survive a worker restart onto a new deploy, and it is written by a process the tenant is
  waiting on — "the redeploy invalidated every in-flight ingest" is a bad afternoon.
* An explicit codec makes the seam visible. :mod:`aegis.ingestion.blocks` is deliberately
  poorer than Docling's own document model so that the parser can be replaced; a codec
  that reflected over whatever fields happened to exist would silently start carrying a
  new one, and the first place anybody would notice is a chunk that could not be read
  back. :func:`loads_parsed` names every field it restores, so a field added upstream
  fails a round-trip test here instead of vanishing in production.

The format is JSON, versioned by :data:`ARTIFACT_VERSION`. An artifact written by an
incompatible version is **refused, not guessed at**: the alternative is a chunk stage
building rows out of a structure it half-understands, which produces plausible chunks with
wrong page numbers — the failure class this phase's provenance work exists to remove.
"""

from __future__ import annotations

import json
from typing import Any

from aegis.ingestion.blocks import (
    BBox,
    BlockKind,
    FurnitureRun,
    ParsedBlock,
    ParsedDocument,
    ParsedPage,
)
from aegis.ingestion.probe import OcrDecision, TextLayerProbe

__all__ = ["ARTIFACT_VERSION", "ArtifactVersionError", "dumps_parsed", "loads_parsed"]

#: The artifact schema version. Bump it whenever a field is added, removed or reinterpreted
#: — a reader that cannot make sense of an artifact must say so rather than fill in a
#: default and produce chunks whose provenance is quietly wrong.
ARTIFACT_VERSION = 1


class ArtifactVersionError(ValueError):
    """A parse artifact was written by an incompatible version of this codec.

    Raised rather than tolerated. The caller (the ``chunk`` stage) turns it into a
    non-retryable failure naming the document, so the fix is a re-parse a person asks for
    rather than chunks nobody can trace.
    """


def _bbox_to_json(bbox: BBox | None) -> list[float] | None:
    """Return ``bbox`` as ``[left, top, right, bottom]``, or ``None``."""
    if bbox is None:
        return None
    return [bbox.left, bbox.top, bbox.right, bbox.bottom]


def _bbox_from_json(raw: Any) -> BBox | None:  # noqa: ANN401 - JSON is Any by nature
    """Rebuild a :class:`~aegis.ingestion.blocks.BBox` from its JSON form.

    Args:
        raw: The four-element list written by :func:`_bbox_to_json`, or ``None``.

    Returns:
        The rectangle, or ``None`` when the block carried no provenance.
    """
    if raw is None:
        return None
    left, top, right, bottom = (float(value) for value in raw)
    return BBox(left=left, top=top, right=right, bottom=bottom)


def dumps_parsed(document: ParsedDocument) -> str:
    """Serialise a parsed document to the JSON artifact the chunk stage reads.

    Args:
        document: The parse output.

    Returns:
        The artifact, as a JSON string.
    """
    probe = document.ocr.probe
    payload: dict[str, Any] = {
        "version": ARTIFACT_VERSION,
        "source_name": document.source_name,
        "parse_seconds": document.parse_seconds,
        "parser": document.parser,
        "ocr": {
            "enabled": document.ocr.enabled,
            "reason": document.ocr.reason,
            "probe": None
            if probe is None
            else {
                "page_chars": list(probe.page_chars),
                "min_chars_per_page": probe.min_chars_per_page,
            },
        },
        "pages": [
            {
                "page_no": page.page_no,
                "width": page.width,
                "height": page.height,
                "char_count": page.char_count,
                "has_text_layer": page.has_text_layer,
            }
            for page in document.pages
        ],
        "blocks": [
            {
                "kind": block.kind.value,
                "text": block.text,
                "page_no": block.page_no,
                "bbox": _bbox_to_json(block.bbox),
                "level": block.level,
                "heading_path": list(block.heading_path),
                "table_shape": None
                if block.table_shape is None
                else list(block.table_shape),
            }
            for block in document.blocks
        ],
        "removed_furniture": [
            {
                "pattern": run.pattern,
                "sample": run.sample,
                "pages": list(run.pages),
                "band": run.band,
            }
            for run in document.removed_furniture
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def loads_parsed(payload: str) -> ParsedDocument:
    """Rebuild a parsed document from its JSON artifact.

    Args:
        payload: What :func:`dumps_parsed` wrote.

    Returns:
        The document, structurally identical to the one that was serialised.

    Raises:
        ArtifactVersionError: If the artifact declares a version this codec does not
            understand.
        ValueError: If the JSON is malformed, or a block's rectangle is inside-out (which
            :class:`~aegis.ingestion.blocks.BBox` refuses, exactly as it does on the way
            out of the parser).
    """
    raw = json.loads(payload)
    version = raw.get("version")
    if version != ARTIFACT_VERSION:
        raise ArtifactVersionError(
            f"parse artifact declares version {version!r}, but this build reads version "
            f"{ARTIFACT_VERSION}. Re-parse the document rather than chunking a structure "
            "this code only half understands."
        )
    ocr_raw = raw["ocr"]
    probe_raw = ocr_raw.get("probe")
    ocr = OcrDecision(
        enabled=bool(ocr_raw["enabled"]),
        reason=str(ocr_raw["reason"]),
        probe=None
        if probe_raw is None
        else TextLayerProbe(
            page_chars=tuple(int(value) for value in probe_raw["page_chars"]),
            min_chars_per_page=int(probe_raw["min_chars_per_page"]),
        ),
    )
    pages = tuple(
        ParsedPage(
            page_no=int(page["page_no"]),
            width=float(page["width"]),
            height=float(page["height"]),
            char_count=int(page["char_count"]),
            has_text_layer=bool(page["has_text_layer"]),
        )
        for page in raw["pages"]
    )
    blocks = tuple(
        ParsedBlock(
            kind=BlockKind(block["kind"]),
            text=block["text"],
            page_no=int(block["page_no"]),
            bbox=_bbox_from_json(block["bbox"]),
            level=None if block["level"] is None else int(block["level"]),
            heading_path=tuple(block["heading_path"]),
            table_shape=None
            if block["table_shape"] is None
            else (int(block["table_shape"][0]), int(block["table_shape"][1])),
        )
        for block in raw["blocks"]
    )
    furniture = tuple(
        FurnitureRun(
            pattern=run["pattern"],
            sample=run["sample"],
            pages=tuple(int(page) for page in run["pages"]),
            band=run["band"],
        )
        for run in raw["removed_furniture"]
    )
    return ParsedDocument(
        source_name=raw["source_name"],
        pages=pages,
        blocks=blocks,
        ocr=ocr,
        removed_furniture=furniture,
        parse_seconds=float(raw["parse_seconds"]),
        parser=raw["parser"],
    )
