"""Guard test: importing aegis.core pulls no heavy dependencies.

This test proves the core module can be imported cheaply and stays isolation-
ready. It runs in a subprocess to ensure sys.modules isn't polluted by other tests.
"""

import os
import subprocess
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2] / "src")


def test_core_imports_no_heavy_deps() -> None:
    """Assert importing aegis.core adds none of the banned heavy dependencies to sys.modules.

    Banned deps: litellm, torch, langgraph, xgboost, fastapi, redis, nemoguardrails,
    sqlalchemy. ``sqlalchemy`` lives in ``aegis.data`` (the ``aegis[data]`` extra), never in
    ``aegis.core`` — core stays pydantic-only. These must be imported *only* through
    aegis.require() / the relevant extra when explicitly installed.

    The subprocess resolves ``aegis`` from the source tree via ``PYTHONPATH`` so the guard
    tests the real import graph deterministically, independent of editable-install state.

    Raises:
        AssertionError: If any banned module appears in sys.modules after importing aegis.core.
    """
    code = (
        "import sys; import aegis.core; import aegis.core.stream; "
        "banned = {'litellm','torch','langgraph','xgboost','fastapi','redis',"
        "'nemoguardrails','sqlalchemy'}; "
        "hit = banned & set(sys.modules); assert not hit, hit"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "PYTHONPATH": _SRC},
    )
    assert proc.returncode == 0, f"Import guard failed:\n{proc.stdout}\n{proc.stderr}"
