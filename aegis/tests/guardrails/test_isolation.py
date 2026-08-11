"""Test that importing aegis.guardrails pulls no heavy dependencies."""

from __future__ import annotations

import subprocess
import sys


def test_importing_guardrails_pulls_no_heavy_deps() -> None:
    """Verify importing aegis.guardrails does not pull heavy platform deps.

    This guard test ensures guardrails remains lightweight and can be used
    without importing litellm, torch, langgraph, xgboost, or fastapi.
    """
    code = (
        "import sys; import aegis.guardrails; "
        "banned = {'litellm','torch','langgraph','xgboost','fastapi'}; "
        "hit = banned & set(sys.modules); "
        "print('HIT', hit); assert not hit, hit"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
