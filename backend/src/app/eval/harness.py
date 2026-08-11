"""Strangler shim: ``app.eval.harness`` delegates to :mod:`aegis.evals.harness`.

The offline eval runner + CI quality-gate thresholds now live in the standalone
``aegis.evals`` package; re-exported here under their historical names. The judge, when a
``complete`` is injected into :func:`evaluate`, runs through ``aegis.evals`` (inject-only).

``python -m app.eval.harness`` still prints the human-readable report + POSIX exit code.
"""

from __future__ import annotations

from aegis.evals.harness import (
    DEFAULT_THRESHOLDS,
    CompleteFn,
    EvalReport,
    EvalThresholds,
    build_eval_retriever,
    evaluate,
)
from aegis.evals.harness import _main as _main

__all__ = [
    "DEFAULT_THRESHOLDS",
    "CompleteFn",
    "EvalReport",
    "EvalThresholds",
    "build_eval_retriever",
    "evaluate",
]


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(_main())
