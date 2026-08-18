"""The Docling seam: real parses of real PDFs, and the D2 result the config cannot prove."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import aegis
from aegis.ingestion import BlockKind, ParsedBlock, ParsedDocument, parse_pdf, parser_version
from aegis.ingestion.convert import _CONVERTERS, _pdf_format_option, warm_converter

from .conftest import fixture_pdf, slow_fixtures

pytest.importorskip("docling", reason="the 'ingestion' extra is not installed")


@pytest.fixture(scope="module")
def bert(parsed_bert) -> ParsedDocument:
    """One real 16-page two-column parse, reused (a parse costs seconds, not ms)."""
    return parsed_bert


def test_the_pipeline_carries_both_d2_switches_and_accurate_tables():
    # The configuration half of D2/D3b. The result half is the histogram tests below —
    # setting only heading_hierarchy.enabled produces a plausible tree and no error.
    _input_format, option = _pdf_format_option(do_ocr=False)
    options = option.pipeline_options

    assert options.heading_hierarchy_options.enabled is True
    assert options.generate_parsed_pages is True
    assert options.table_structure_options.mode.value == "accurate"
    assert options.do_ocr is False
    assert options.ocr_options.kind == "rapidocr"


def test_importing_the_package_does_not_import_docling():
    # The seam is only a seam if nothing pulls the parser in by accident. Checked in a
    # fresh interpreter because this one has already imported Docling above.
    source = Path(aegis.__file__).resolve().parents[1]
    probe = subprocess.run(
        [sys.executable, "-c", "import aegis.ingestion, sys; print('docling' in sys.modules)"],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(source)},
        check=True,
    )

    assert probe.stdout.strip() == "False"


def test_a_real_parse_returns_our_types_and_nothing_of_doclings(bert):
    assert isinstance(bert, ParsedDocument)
    assert all(isinstance(block, ParsedBlock) for block in bert.blocks)
    assert all(type(block).__module__.startswith("aegis.") for block in bert.blocks)
    assert bert.parser == parser_version()
    assert bert.parse_seconds > 0


def test_every_block_carries_the_page_and_box_it_came_from(bert):
    assert bert.blocks
    for block in bert.blocks:
        assert 1 <= block.page_no <= bert.page_count
        assert block.bbox is not None, f"no provenance on {block.text[:40]!r}"


def test_boxes_are_top_left_origin_and_inside_their_page(bert):
    # The origin flip is the easy thing to get wrong, and a flipped box does not raise —
    # it draws the citation highlight in the wrong place.
    pages = {page.page_no: page for page in bert.pages}
    for block in bert.blocks:
        page = pages[block.page_no]
        box = block.bbox
        assert 0 <= box.left < box.right <= page.width + 1
        assert 0 <= box.top < box.bottom <= page.height + 1


def test_the_heading_tree_is_multi_level_not_flat(bert):
    # D2's whole point: {1: N} means the hierarchy never turned on, and a two-level
    # histogram on a document this structured is the silent partial failure.
    histogram = bert.heading_histogram

    assert len(histogram) >= 3, histogram
    assert min(histogram) == 1
    assert sum(histogram.values()) > 10


def test_the_heading_path_is_the_chain_of_enclosing_headings(bert):
    headings = {block.text for block in bert.blocks if block.kind is BlockKind.HEADING}
    with_path = [block for block in bert.blocks if block.heading_path]

    assert with_path
    for block in with_path:
        assert set(block.heading_path) <= headings


def test_the_ocr_decision_is_recorded_with_its_evidence(bert):
    assert bert.ocr.enabled is False
    assert bert.ocr.probe is not None
    assert bert.ocr.probe.page_count == bert.page_count
    assert "born-digital" in bert.ocr.reason


def test_tables_arrive_as_markdown_with_their_shape(bert):
    tables = [block for block in bert.blocks if block.kind is BlockKind.TABLE]

    assert tables
    for table in tables:
        rows, columns = table.table_shape
        assert rows > 0 and columns > 0
        assert "|" in table.text


def test_the_converter_is_built_once_and_warming_fills_the_cache(bert):
    from aegis.ingestion.convert import _converter

    first = _converter(do_ocr=False)
    assert warm_converter() >= 0
    assert _converter(do_ocr=False) is first
    assert _CONVERTERS[False] is first


def test_the_parser_and_the_ml_spine_survive_the_same_process():
    # Docling brings torch; the ML spine brings xgboost; each ships its own OpenMP
    # runtime. Measured 2026-08-18: torch first then an xgboost fit segfaults, xgboost
    # first then a torch op deadlocks. The seam pins OMP_NUM_THREADS=1 before torch
    # loads, which fixes both. In a subprocess because the failure it guards is a
    # segmentation fault, which would take the whole suite with it rather than fail one
    # test — as it did before the pin.
    pytest.importorskip("xgboost", reason="the 'ml' extra is not installed")
    source = Path(aegis.__file__).resolve().parents[1]
    code = (
        "from aegis.ingestion.convert import _require_docling;"
        "_require_docling('docling.document_converter');"
        "import numpy as np; from xgboost import XGBClassifier;"
        "X = np.random.rand(120, 3); y = (X[:, 0] > 0.5).astype(int);"
        "XGBClassifier(n_estimators=5).fit(X, y); print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(source)},
    )

    assert result.returncode == 0, f"exit {result.returncode}\n{result.stderr[-2000:]}"
    assert result.stdout.strip().endswith("ok")


@slow_fixtures
def test_census_is_the_deep_tree_d2_was_written_about():
    # The fixture the phase names for this assertion: 67 pages whose true structure is
    # genuinely several levels deep, so a flat or two-level histogram here is a failure.
    document = parse_pdf(fixture_pdf("census-income-tables.pdf"))

    histogram = document.heading_histogram
    assert len(histogram) >= 4, histogram
    assert document.table_count >= 10
    assert all(block.bbox is not None for block in document.blocks)


@slow_fixtures
def test_the_table_dense_government_document_parses_end_to_end():
    document = parse_pdf(fixture_pdf("irs-1040-instructions-tables.pdf"))

    assert document.page_count == 126
    assert len(document.heading_histogram) >= 4, document.heading_histogram
    assert document.table_count >= 10
    assert document.ocr.enabled is False
