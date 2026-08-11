"""AG-UI streaming for the eval pass — emits its verdict à la carte over the emitter.

Wraps an evaluation in a `STEP_STARTED`/`STEP_FINISHED` bracket keyed
``SpanKind.EVALUATOR`` (which the trace panel already knows how to render), emitting the
`EVAL_RESULT` custom event in between so a frontend can show a run's overall score, its
pass/fail verdict, and the per-metric breakdown as soon as the eval finishes. Never calls
a model — the payload is whatever the (already-computed) report carries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aegis.core import stream_names
from aegis.core.events import SpanKind

from .regression import RegressionReport

if TYPE_CHECKING:
    from aegis.core.stream import AegisEmitter

_STEP_NAME = "evaluate"


async def emit_eval_result(
    emitter: AegisEmitter,
    *,
    overall: float,
    passed: bool,
    metrics: dict[str, float],
) -> None:
    """Emit one `STEP(evaluate, EVALUATOR)` bracket carrying an `EVAL_RESULT` custom event.

    Args:
        emitter: The AG-UI emitter for streaming events.
        overall: The run's overall score in ``[0, 1]``.
        passed: Whether the run cleared its gate.
        metrics: The per-metric ``name → value`` breakdown.
    """
    async with emitter.step(_STEP_NAME, SpanKind.EVALUATOR):
        await emitter.custom(
            stream_names.EVAL_RESULT,
            {"overall": float(overall), "passed": bool(passed), "metrics": dict(metrics)},
        )


async def stream_regression_report(
    emitter: AegisEmitter, report: RegressionReport
) -> RegressionReport:
    """Stream a :class:`~aegis.evals.regression.RegressionReport` over the emitter.

    Flattens the per-case metric results into a ``name → value`` map, derives an overall
    score as the mean of every measured metric value, and emits the standard
    `STEP(evaluate, EVALUATOR)` + `EVAL_RESULT` pair.

    Args:
        emitter: The AG-UI emitter for streaming events.
        report: The already-computed regression report to surface.

    Returns:
        The same ``report`` (so callers can stream-and-forward in one expression).
    """
    values = [m.value for case in report.cases for m in case.metrics]
    metrics = {m.name: m.value for case in report.cases for m in case.metrics}
    overall = sum(values) / len(values) if values else 0.0
    await emit_eval_result(
        emitter, overall=overall, passed=report.passed, metrics=metrics
    )
    return report


__all__ = ["emit_eval_result", "stream_regression_report"]
