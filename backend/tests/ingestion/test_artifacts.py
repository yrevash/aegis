"""The parse artifact round-trips, field for field, and refuses a version it cannot read.

The artifact is how the structure the ``parse`` stage recovered reaches the ``chunk``
stage: two activities, two transactions, possibly two processes. If a field is lost in
transit nothing raises — the chunker simply builds chunks with a missing page number, an
absent heading path or no OCR record, and the result looks like a successful ingest with
quietly wrong provenance.

So the round-trip is asserted **field by field on every block**, and the codec names each
field explicitly rather than reflecting over the dataclass: a field added to
:mod:`aegis.ingestion.blocks` upstream then fails here, at the seam, instead of vanishing
in production.
"""

from __future__ import annotations

import pytest
from aegis.ingestion.blocks import (
    BBox,
    BlockKind,
    FurnitureRun,
    ParsedBlock,
    ParsedDocument,
    ParsedPage,
)
from aegis.ingestion.probe import OcrDecision, TextLayerProbe

from app.ingestion.artifacts import (
    ARTIFACT_VERSION,
    ArtifactVersionError,
    dumps_parsed,
    loads_parsed,
)


def _document() -> ParsedDocument:
    """A parsed document exercising every optional field the codec has to carry."""
    return ParsedDocument(
        source_name="filing.pdf",
        pages=(
            ParsedPage(page_no=1, width=612.0, height=792.0, char_count=1800, has_text_layer=True),
            ParsedPage(page_no=2, width=612.0, height=792.0, char_count=0, has_text_layer=False),
        ),
        blocks=(
            ParsedBlock(
                kind=BlockKind.HEADING,
                text="Attention Is All You Need",
                page_no=1,
                bbox=BBox(left=72.0, top=90.0, right=540.0, bottom=112.5),
                level=1,
            ),
            ParsedBlock(
                kind=BlockKind.TABLE,
                text="| a | b |\n| - | - |\n| 1 | 2 |",
                page_no=2,
                bbox=None,  # a block Docling reported with no provenance at all
                heading_path=("3 Model Architecture", "3.2 Attention"),
                table_shape=(3, 2),
            ),
        ),
        ocr=OcrDecision(
            enabled=False,
            reason="text layer on 1 of 2 pages",
            probe=TextLayerProbe(page_chars=(1800, 0), min_chars_per_page=100),
        ),
        removed_furniture=(
            FurnitureRun(
                pattern="page N", sample="page 7", pages=(1, 2), band="footer"
            ),
        ),
        parse_seconds=8.25,
        parser="docling 2.120.3",
    )


def test_a_parsed_document_survives_the_round_trip_field_for_field() -> None:
    original = _document()

    restored = loads_parsed(dumps_parsed(original))

    assert restored == original, "the artifact lost or changed something on the way back"
    # Spelled out as well as compared, because dataclass equality would still hold if a
    # field were dropped from *both* sides by a codec that reflected over the class.
    assert restored.parser == "docling 2.120.3"
    assert restored.page_count == 2
    assert restored.heading_histogram == {1: 1}
    assert restored.table_count == 1
    assert restored.blocks[0].bbox == BBox(left=72.0, top=90.0, right=540.0, bottom=112.5)
    assert restored.blocks[1].bbox is None
    assert restored.blocks[1].heading_path == ("3 Model Architecture", "3.2 Attention")
    assert restored.blocks[1].table_shape == (3, 2)
    assert restored.ocr.probe is not None
    assert restored.ocr.probe.page_chars == (1800, 0)
    assert restored.removed_furniture[0].pages == (1, 2)


def test_an_artifact_from_another_version_is_refused_rather_than_guessed_at() -> None:
    """Chunking a structure this build only half understands is the failure to avoid.

    It would not raise: it would produce plausible chunks carrying wrong page numbers,
    which is exactly the class of defect the provenance work exists to remove.
    """
    payload = dumps_parsed(_document()).replace(
        f'"version": {ARTIFACT_VERSION}', '"version": 999'
    )

    with pytest.raises(ArtifactVersionError, match="999"):
        loads_parsed(payload)
