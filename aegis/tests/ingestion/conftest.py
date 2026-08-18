"""Shared fixture-file plumbing for the ingestion tests.

The four PDFs are deliberately not committed (see ``tests/fixtures/pdfs/README.md``), so
every test that needs one skips with a message naming the missing file rather than
failing on a machine where ``fetch.sh`` has not been run.

The two small papers are parsed **once per session** and shared. A parse is 12–23 s of
CPU, three modules want the same two documents, and :class:`ParsedDocument` is frozen —
so re-deriving it per module would buy nothing but wall clock.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from aegis.ingestion import ParsedDocument

PDF_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "pdfs"


def fixture_pdf(name: str) -> Path:
    """Return the path to a fixture PDF, skipping the test if it is not downloaded.

    Args:
        name: File name inside ``tests/fixtures/pdfs``.

    Returns:
        The path to the PDF.
    """
    path = PDF_DIR / name
    if not path.exists():
        pytest.skip(f"{name} is not downloaded — run {PDF_DIR}/fetch.sh")
    return path


#: The 67- and 126-page fixtures parse in minutes, not seconds (measured: 214 s and 361 s
#: on an M3). They are real assertions, not optional ones — they are simply too slow to
#: sit in a suite that runs on every change, so they are opt-in and recorded in the phase
#: notes instead. Run them with ``AEGIS_DOCLING_SLOW_FIXTURES=1``.
slow_fixtures = pytest.mark.skipif(
    not os.environ.get("AEGIS_DOCLING_SLOW_FIXTURES"),
    reason="set AEGIS_DOCLING_SLOW_FIXTURES=1 to parse the 67- and 126-page fixtures (~10 min)",
)


@pytest.fixture(scope="session")
def parsed_bert() -> ParsedDocument:
    """The 16-page two-column paper, parsed once for the whole session."""
    pytest.importorskip("docling", reason="the 'ingestion' extra is not installed")
    from aegis.ingestion import parse_pdf  # noqa: PLC0415 - only when docling is present

    return parse_pdf(fixture_pdf("bert-two-column.pdf"))


@pytest.fixture(scope="session")
def parsed_transformer() -> ParsedDocument:
    """The 15-page single-column control, parsed once for the whole session."""
    pytest.importorskip("docling", reason="the 'ingestion' extra is not installed")
    from aegis.ingestion import parse_pdf  # noqa: PLC0415 - only when docling is present

    return parse_pdf(fixture_pdf("transformer-single-column.pdf"))
