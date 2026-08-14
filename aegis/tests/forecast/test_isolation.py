"""Guard tests: aegis.forecast is importable and isolated (the Module Contract).

Checked in subprocesses so another test's ``sys.modules`` cannot hide a real import:

1. ``aegis.forecast.types`` (the API-facing shapes) pulls in **no** part of the
   forecasting stack — a light schema layer must be able to depend on it.
2. Importing the ``aegis.forecast`` package pulls in no ``app.*`` module. It is a
   library, not a layer of this backend.
3. The heavy stack is reached only through :func:`aegis.core.lazy.require`, so a
   deployment without the extra gets a message naming the install command rather
   than a bare ``ModuleNotFoundError`` from somewhere deep inside a handler.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2] / "src")

_BANNED = "{'statsforecast','pandas','numpy','numba','xgboost','sklearn','torch','fastapi'}"


def _run(code: str) -> subprocess.CompletedProcess[str]:
    """Run ``code`` in a subprocess with ``aegis`` resolved from the source tree."""
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONPATH": _SRC},
    )


def test_forecast_types_pull_no_forecasting_stack() -> None:
    """Importing aegis.forecast.types must not drag statsforecast/pandas/numpy in."""
    code = (
        "import sys; import aegis.forecast.types; "
        f"banned = {_BANNED}; hit = banned & set(sys.modules); "
        "print('HIT', hit); assert not hit, hit"
    )
    proc = _run(code)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_forecast_package_import_stays_light() -> None:
    """The package __init__ must defer the engine, not import it eagerly."""
    code = (
        "import sys; import aegis.forecast; "
        f"banned = {_BANNED}; hit = banned & set(sys.modules); "
        "print('HIT', hit); assert not hit, hit"
    )
    proc = _run(code)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_series_preparation_works_with_only_the_base_install() -> None:
    """Bucketing, frequency inference and the refusal arithmetic need pydantic alone."""
    code = (
        "from datetime import datetime, timedelta\n"
        "from aegis.forecast import bucket_events, infer_freq, minimum_history\n"
        "base = datetime(2026, 1, 1)\n"
        "pts = bucket_events([(base, 2.0), (base + timedelta(days=2), 4.0)], 'D')\n"
        "assert [p.value for p in pts] == [2.0, 0.0, 4.0], pts\n"
        "assert infer_freq(pts) == 'D'\n"
        "assert minimum_history(14, 7) == 71\n"
    )
    proc = _run(code)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_forecast_imports_nothing_from_the_host_app() -> None:
    """No ``app.*`` module may appear after importing aegis.forecast."""
    code = (
        "import sys; import aegis.forecast; import aegis.forecast.engine; "
        "hit = [m for m in sys.modules if m == 'app' or m.startswith('app.')]; "
        "print('HIT', hit); assert not hit, hit"
    )
    proc = _run(code)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_a_missing_extra_fails_loud_with_the_install_command() -> None:
    """With statsforecast unimportable, the error must name ``pip install aegis[forecast]``."""
    code = (
        "import builtins, sys\n"
        "_real = builtins.__import__\n"
        "def _blocked(name, *a, **kw):\n"
        "    if name == 'statsforecast' or name.startswith('statsforecast.'):\n"
        "        raise ImportError('blocked for the test')\n"
        "    return _real(name, *a, **kw)\n"
        "builtins.__import__ = _blocked\n"
        "sys.modules.pop('statsforecast', None)\n"
        "from datetime import datetime, timedelta\n"
        "from aegis.forecast import forecast_series\n"
        "pts = [(datetime(2026,1,1) + timedelta(days=i), float(i % 5) + i) for i in range(140)]\n"
        "try:\n"
        "    forecast_series(pts, series_id='s', label='s', data_source='t', horizon=14)\n"
        "except ImportError as exc:\n"
        "    assert 'pip install aegis[forecast]' in str(exc), str(exc)\n"
        "else:\n"
        "    raise AssertionError('a missing extra must fail loud, not degrade')\n"
    )
    proc = _run(code)
    assert proc.returncode == 0, proc.stdout + proc.stderr
