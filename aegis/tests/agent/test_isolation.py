"""Guard test: importing aegis.agent pulls no host/heavy runtime dependencies.

``aegis.agent`` is a pure graph-over-injected-deps. Importing it may pull ``langgraph``
(its own declared dependency) and ``opentelemetry`` (via ``aegis.observability``, a real
cheap dep it imports for spans) — but NEVER ``litellm`` (the gateway's LLM client),
``fastapi`` (a host web layer), a DB driver (``sqlalchemy``/``asyncpg``/``psycopg``),
``redis``, ``xgboost``, or the host ``app`` package. The graph reaches every capability
through injected callables, so none of that is dragged in at import time.

Runs in a subprocess so sys.modules isn't polluted by other tests.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2] / "src")


def test_import_agent_pulls_no_host_or_heavy_deps() -> None:
    """Assert importing aegis.agent adds none of the banned modules to sys.modules."""
    code = (
        "import sys; import aegis.agent; "
        "banned = {'litellm','fastapi','sqlalchemy','asyncpg','psycopg',"
        "'redis','xgboost','app','starlette'}; "
        "hit = banned & set(m.split('.')[0] for m in sys.modules); "
        "assert not hit, hit; "
        # langgraph is aegis.agent's own dependency — it MUST be present.
        "assert 'langgraph' in sys.modules, 'langgraph should be imported'"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "PYTHONPATH": _SRC},
    )
    assert proc.returncode == 0, f"Import guard failed:\n{proc.stdout}\n{proc.stderr}"
