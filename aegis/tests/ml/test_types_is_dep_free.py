"""Guard test: importing aegis.ml.types alone pulls no heavy ML dependencies.

This proves ``MLExplainResponse``/``ShapFeature`` (used e.g. by a light API schema
layer) can be imported without dragging in xgboost/sklearn/mapie/shap/pandas/torch.
Runs in a subprocess so sys.modules from other tests can't hide a real import.
"""

import os
import subprocess
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2] / "src")


def test_ml_types_imports_no_heavy_deps() -> None:
    """Assert importing aegis.ml.types adds none of the banned heavy deps to sys.modules.

    Banned deps: xgboost, sklearn, mapie, shap, pandas, numpy, joblib, torch. These
    live only behind ``aegis.ml.model`` / ``aegis.ml.dataset``, gated by the ``ml``
    extra, never behind ``aegis.ml.types``.

    Raises:
        AssertionError: If any banned module appears in sys.modules after the import.
    """
    code = (
        "import sys; import aegis.ml.types; "
        "banned = {'xgboost','sklearn','mapie','shap','pandas','numpy','joblib','torch'}; "
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
