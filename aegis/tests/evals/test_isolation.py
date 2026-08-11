"""Importing aegis.evals pulls the retrieval/gateway types but none of the megadeps.

``aegis.evals`` is a *pure* eval library: it depends on ``aegis.retrieval`` +
``aegis.gateway`` **types** and stdlib, with no ORM and no heavy eval deps. What it must
NOT pull is any heavy gateway/agent/platform megadep — ``fastapi`` and ``litellm`` above
all (the gateway *types* are litellm-free), nor ``sqlalchemy`` / ``langgraph`` / ``torch``
/ ``ragas`` / ``deepeval``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2] / "src")


def test_importing_evals_pulls_no_heavy_deps() -> None:
    """Verify importing aegis.evals pulls no fastapi/litellm/ORM/heavy-eval megadeps."""
    code = (
        "import sys; "
        "import aegis.evals; "
        "import aegis.evals.harness; "
        "import aegis.evals.regression; "
        "import aegis.evals.judge; "
        "import aegis.evals.stream; "
        "banned = {'litellm', 'fastapi', 'sqlalchemy', 'torch', 'langgraph', "
        "'xgboost', 'nemoguardrails', 'ragas', 'deepeval'}; "
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
