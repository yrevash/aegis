"""Test that importing aegis.gateway pulls no litellm (or other heavy deps).

``litellm`` is lazy-imported inside `aegis.gateway.llm._litellm`, called only
from `complete`/`embed`. So `import aegis.gateway` — including `configure` and
the result types — must never require it to be installed, matching the same
guard used for `aegis.retrieval`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2] / "src")


def test_importing_gateway_pulls_no_litellm() -> None:
    """Verify importing `aegis.gateway` does not pull litellm (or other heavy deps).

    The subprocess resolves ``aegis`` from the source tree via ``PYTHONPATH`` so
    the guard tests the real import graph deterministically, independent of
    editable-install state (and of whether the `[gateway]` extra is installed).
    """
    code = (
        "import sys; "
        "import aegis.gateway; "
        "import aegis.gateway.llm; "
        "import aegis.gateway.routing; "
        "import aegis.gateway.stream; "
        "import aegis.gateway.types; "
        "banned = {'litellm', 'torch', 'langgraph', 'xgboost', 'fastapi', "
        "'redis', 'nemoguardrails', 'lightrag', 'neo4j', 'asyncpg', 'pgvector'}; "
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


def test_gateway_is_usable_after_import_with_no_litellm_installed() -> None:
    """A fresh `import aegis.gateway` leaves `complete`/`embed`/`configure` callable.

    (litellm only needs to exist once a call is actually made — see
    ``tests/gateway/test_llm.py`` for the fake-litellm-injected call tests.)
    """
    code = (
        "import aegis.gateway as gw; "
        "assert callable(gw.complete); "
        "assert callable(gw.embed); "
        "assert callable(gw.configure); "
        "print('OK')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": _SRC},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK" in proc.stdout
