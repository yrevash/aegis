"""Importing aegis.governance pulls the auth/ORM deps but none of the megadeps.

``aegis.governance`` DOES pull ``sqlalchemy`` + ``jwt`` + ``argon2`` (its ``aegis[data]``
/ ``aegis[governance]`` extras — expected and fine). What it must NOT pull is any heavy
gateway/agent/platform megadep: ``fastapi`` and ``litellm`` above all (the strangler shim
imports the gateway ``BudgetExceededError`` type, which stays litellm-free), nor
``langgraph`` / ``torch`` / ``xgboost`` / ``nemoguardrails`` / ``lightrag`` / ``neo4j``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2] / "src")


def test_importing_governance_pulls_no_heavy_deps() -> None:
    """Verify importing aegis.governance pulls the auth/ORM deps but no megadeps."""
    code = (
        "import sys; "
        "import aegis.governance; "
        "import aegis.governance.security; "
        "import aegis.governance.enforcement; "
        "import aegis.governance.audit; "
        "import aegis.governance.rls; "
        "assert 'sqlalchemy' in sys.modules, 'expected sqlalchemy (aegis[data])'; "
        "assert 'jwt' in sys.modules, 'expected pyjwt (aegis[governance])'; "
        "assert 'argon2' in sys.modules, 'expected argon2 (aegis[governance])'; "
        "banned = {'litellm', 'fastapi', 'torch', 'langgraph', 'xgboost', "
        "'nemoguardrails', 'lightrag', 'neo4j'}; "
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
