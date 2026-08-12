"""Importing aegis.redteam pulls only guardrails (+ core/stdlib), no megadeps."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2] / "src")


def test_importing_redteam_pulls_no_heavy_deps() -> None:
    """Verify importing aegis.redteam does not pull heavy platform deps.

    The harness is a leaf: it must attack the guardrails without dragging in the
    gateway/agent/platform stack (litellm, torch, langgraph, xgboost, fastapi,
    sqlalchemy, nemoguardrails). The subprocess resolves ``aegis`` from the source
    tree via ``PYTHONPATH`` so the guard tests the real import graph.
    """
    code = (
        "import sys; "
        "import aegis.redteam; "
        "import aegis.redteam.battery; "
        "import aegis.redteam.runner; "
        "banned = {'litellm', 'torch', 'langgraph', 'xgboost', 'fastapi', "
        "'sqlalchemy', 'nemoguardrails'}; "
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
