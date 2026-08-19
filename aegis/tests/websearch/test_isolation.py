"""Importing aegis.websearch must stay cheap and must not need the vendor SDK."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2] / "src")


def test_importing_websearch_pulls_no_heavy_deps_and_no_vendor_sdk() -> None:
    """The seam is the point: nothing above it may depend on Tavily being installed."""
    code = (
        "import sys; import aegis.websearch; "
        "banned = {'litellm','torch','langgraph','xgboost','fastapi','tavily'}; "
        "hit = banned & set(sys.modules); "
        "print('HIT', hit); assert not hit, hit"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": _SRC},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
