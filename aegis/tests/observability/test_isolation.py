"""Test that importing aegis.observability pulls no fastapi/litellm/phoenix.

``arize-phoenix`` is imported lazily (inside
``aegis.observability.otel.init_observability``, only when ``phoenix_enabled``
is true), so a bare ``import aegis.observability`` — including
``init_observability``, the span helpers and ``OtelObservabilitySink`` — must
never require it (or any other heavy dep) to be installed. Phoenix is
deliberately *not* installed in this environment, so this guard also proves the
module degrades to a console exporter without it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2] / "src")


def test_importing_observability_pulls_no_fastapi_litellm_or_phoenix() -> None:
    """Verify a bare import of every observability submodule stays leaf-clean.

    The subprocess resolves ``aegis`` from the source tree via ``PYTHONPATH`` so
    the guard tests the real import graph deterministically, independent of
    editable-install state.
    """
    code = (
        "import sys; "
        "import aegis.observability; "
        "import aegis.observability.otel; "
        "import aegis.observability.spans; "
        "import aegis.observability.genai; "
        "import aegis.observability.semconv; "
        "import aegis.observability.sink; "
        "import aegis.observability.latency; "
        "banned = {'litellm', 'torch', 'langgraph', 'xgboost', 'fastapi', "
        "'redis', 'nemoguardrails', 'lightrag', 'neo4j', 'asyncpg', 'pgvector', "
        "'phoenix'}; "
        "hit = banned & set(sys.modules); "
        "print('HIT', hit); assert not hit, hit"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "PYTHONPATH": _SRC},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_observability_is_usable_after_import_with_no_phoenix_installed() -> None:
    """A fresh import leaves the public surface callable with no Phoenix present."""
    code = (
        "import aegis.observability as obs; "
        "assert callable(obs.init_observability); "
        "assert callable(obs.get_tracer); "
        "assert callable(obs.span); "
        "assert callable(obs.genai_span); "
        "assert callable(obs.OtelObservabilitySink); "
        "print('OK')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "PYTHONPATH": _SRC},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK" in proc.stdout
