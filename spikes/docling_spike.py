"""Task 4.0 — measure Docling on this machine before Phase 4 depends on it.

Everything Phase 4 assumes about the parser is a number: cold start, seconds per page,
peak RSS, and whether the heading tree comes back multi-level (D2) rather than the
plausible-looking flat one that raises nothing. This script produces those numbers on
whatever box it is run on, which is the point — the ones in the phase doc were measured
on macOS, and the demo box is Windows.

Run (from the repo root, with the backend venv)::

    PYTHONPATH=aegis/src backend/.venv/bin/python spikes/docling_spike.py
    PYTHONPATH=aegis/src backend/.venv/bin/python spikes/docling_spike.py --prefetch /tmp/dl
    PYTHONPATH=aegis/src backend/.venv/bin/python spikes/docling_spike.py bert-two-column.pdf

``--prefetch DIR`` downloads the models our pipeline actually uses into an empty
directory and reports the size and the wall clock, so the demo machine can be primed
while there is still network. The fixtures come from ``tests/fixtures/pdfs`` — run
``./fetch.sh`` there first.
"""

from __future__ import annotations

import resource
import subprocess
import sys
import time
from pathlib import Path

from aegis.ingestion import parse_pdf, parser_version, warm_converter

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "pdfs"

# What the standard pipeline (D1) loads: the layout model, TableFormer on ACCURATE
# (D3b), and the RapidOCR checkpoints the per-document OCR decision (D3) may need.
MODELS = ("layout", "tableformer", "rapidocr")


def peak_rss_mb() -> float:
    """Return this process's peak resident set size in MB.

    Returns:
        The peak RSS. ``ru_maxrss`` is bytes on macOS and kilobytes on Linux, so the
        value is normalised by platform rather than reported in whichever unit the OS
        happened to use.
    """
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def directory_mb(path: Path) -> float:
    """Return the total size of a directory tree in MB.

    Args:
        path: The directory to measure.

    Returns:
        Megabytes on disk.
    """
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / (1024 * 1024)


def prefetch(target: Path) -> None:
    """Download the pipeline's models into ``target`` and report size and duration.

    Args:
        target: An empty directory to download into.
    """
    target.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    subprocess.run(
        [sys.executable, "-m", "docling.cli.tools", "models", "download", "-o", str(target), *MODELS],
        check=True,
    )
    elapsed = time.perf_counter() - started
    print(f"\nmodel download: {directory_mb(target):.0f} MB in {elapsed:.1f}s into {target}")


def main() -> int:
    """Warm the converter, parse the fixtures, and print every measured number.

    Returns:
        0 on success, 1 if the fixtures are missing.
    """
    args = sys.argv[1:]
    if args and args[0] == "--prefetch":
        prefetch(Path(args[1]))
        return 0

    wanted = args or [
        "bert-two-column.pdf",
        "transformer-single-column.pdf",
        "census-income-tables.pdf",
        "irs-1040-instructions-tables.pdf",
    ]
    missing = [name for name in wanted if not (FIXTURES / name).exists()]
    if missing:
        print(f"missing fixtures: {', '.join(missing)} — run {FIXTURES}/fetch.sh")
        return 1

    print(f"parser:        {parser_version()}")
    print(f"python:        {sys.version.split()[0]} on {sys.platform}")
    cold = warm_converter()
    print(f"cold start:    {cold:.1f}s (models loaded, nothing parsed)")
    print(f"peak RSS:      {peak_rss_mb():.0f} MB after warm-up\n")

    for name in wanted:
        document = parse_pdf(FIXTURES / name)
        per_page = document.parse_seconds / max(document.page_count, 1)
        print(f"── {name}")
        print(f"   pages          {document.page_count}")
        print(f"   parse          {document.parse_seconds:.1f}s  ({per_page:.2f} s/page)")
        print(f"   peak RSS       {peak_rss_mb():.0f} MB")
        print(f"   ocr            {document.ocr.reason}")
        print(f"   headings       {document.heading_histogram}")
        print(f"   tables         {document.table_count}")
        print(f"   blocks         {len(document.blocks)}")
        with_box = sum(1 for block in document.blocks if block.bbox is not None)
        print(f"   with page+bbox {with_box}/{len(document.blocks)}")
        for run in document.removed_furniture:
            print(f"   stripped       {run.band} x{len(run.pages)} {run.sample[:60]!r}")
        if not document.removed_furniture:
            print("   stripped       nothing repeated in the margins")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
