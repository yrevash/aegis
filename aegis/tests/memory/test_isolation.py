"""Test that importing aegis.memory pulls no heavy retrieval/gateway deps.

``aegis.memory`` DOES pull ``sqlalchemy`` (it is a data-layer module under the
``aegis[data]`` extra — that is expected and fine). What it must NOT pull is any of the
heavy retrieval/gateway/platform megadeps: lightrag, neo4j, redis, litellm (nor
torch/langgraph/xgboost/fastapi/nemoguardrails). The retrieval helpers it uses
(fusion/vectors/spotlight/types) are all lazy about their heavy backends.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2] / "src")


def test_importing_memory_pulls_no_heavy_deps() -> None:
    """Verify importing aegis.memory (incl. stream) pulls no heavy/optional-extra deps."""
    code = (
        "import sys; "
        "import aegis.memory; "
        "import aegis.memory.stream; "
        "import aegis.memory.consolidate; "
        "import aegis.memory.recall; "
        "assert 'sqlalchemy' in sys.modules, 'expected sqlalchemy (aegis[data])'; "
        "banned = {'litellm', 'torch', 'langgraph', 'xgboost', 'fastapi', "
        "'redis', 'nemoguardrails', 'lightrag', 'neo4j'}; "
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
