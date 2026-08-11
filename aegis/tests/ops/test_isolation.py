"""Importing aegis.ops pulls the ORM/eval deps but none of the megadeps.

``aegis.ops`` DOES pull ``sqlalchemy`` (its ``aegis[data]`` extra — expected and fine) and
``aegis.evals`` (which pulls the ``aegis.retrieval`` + ``aegis.gateway`` *types*). What it
must NOT pull is any heavy gateway/agent/platform megadep: ``fastapi`` and ``litellm``
above all, nor ``langgraph`` / ``torch`` / ``xgboost`` / ``nemoguardrails``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2] / "src")


def test_importing_ops_pulls_no_heavy_deps() -> None:
    """Verify importing aegis.ops pulls sqlalchemy but no fastapi/litellm megadeps."""
    code = (
        "import sys; "
        "import aegis.ops; "
        "import aegis.ops.models; "
        "import aegis.ops.registry; "
        "import aegis.ops.trace_eval; "
        "import aegis.ops.diagnose; "
        "import aegis.ops.release; "
        "import aegis.ops.gate; "
        "assert 'sqlalchemy' in sys.modules, 'expected sqlalchemy (aegis[data])'; "
        "banned = {'litellm', 'fastapi', 'torch', 'langgraph', 'xgboost', "
        "'nemoguardrails'}; "
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
