"""Strangler shim: ``app.ops.trace_eval`` delegates to :mod:`aegis.ops.trace_eval`.

The off-hot-path live trace-eval (grade a completed run's answer + trajectory steps and
persist ``EvalResult`` rows) now lives in the standalone ``aegis.ops`` package; re-exported
here under its historical names. ``session`` + ``complete`` are still passed by the caller
(the orchestrator fires this best-effort, post-run), so no configuration is needed.
"""

from __future__ import annotations

from aegis.ops.trace_eval import DEFAULT_THRESHOLD, RunEval, evaluate_run

__all__ = ["DEFAULT_THRESHOLD", "RunEval", "evaluate_run"]
