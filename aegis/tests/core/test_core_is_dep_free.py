"""Guard test: importing aegis.core pulls no heavy dependencies.

This test proves the core module can be imported cheaply and stays isolation-
ready. It runs in a subprocess to ensure sys.modules isn't polluted by other tests.
"""

import subprocess
import sys


def test_core_imports_no_heavy_deps() -> None:
    """Assert importing aegis.core adds none of the banned heavy dependencies to sys.modules.

    Banned deps: litellm, torch, langgraph, xgboost, fastapi, redis, nemoguardrails.
    These must be imported *only* through aegis.require() when explicitly installed as extras.

    Raises:
        AssertionError: If any banned module appears in sys.modules after importing aegis.core.
    """
    code = (
        "import sys; import aegis.core; "
        "banned = {'litellm','torch','langgraph','xgboost','fastapi','redis','nemoguardrails'}; "
        "hit = banned & set(sys.modules); assert not hit, hit"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=10
    )
    assert proc.returncode == 0, f"Import guard failed:\n{proc.stdout}\n{proc.stderr}"
