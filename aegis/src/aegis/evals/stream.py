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
    metric_configs: list[dict] | None = None,
) -> None:
    """Emit one `STEP(evaluate, EVALUATOR)` bracket carrying an `EVAL_RESULT` custom event.

    Args:
        emitter: The AG-UI emitter for streaming events.
        overall: The run's overall score in ``[0, 1]``.
        passed: Whether the run cleared its gate.
        metrics: The per-metric ``name → value`` breakdown.
        metric_configs: Optional richer per-metric config list (name/threshold/
            higherIsBetter/value/passed) for the dashboard; added as ``metricConfigs``
            only when supplied so the minimal payload shape is preserved by default.
    """
    payload: dict = {
        "overall": float(overall),
        "passed": bool(passed),
        "metrics": dict(metrics),
    }
    if metric_configs is not None:
        payload["metricConfigs"] = metric_configs
    async with emitter.step(_STEP_NAME, SpanKind.EVALUATOR):
        await emitter.custom(stream_names.EVAL_RESULT, payload)


async def stream_regression_report(
    emitter: AegisEmitter, report: RegressionReport
) -> RegressionReport:
    """Stream a :class:`~aegis.evals.regression.RegressionReport` over the emitter.

    Reads the report's single authoritative projection (:meth:`RegressionReport.as_dict`):
    the aggregate per-metric ``name → value`` map, the overall score, and the full metric
    config list — so the streamed numbers are **identical** to what the accessor returns
    and what the persisted rows carry (no recompute, no rounding). Emits the standard
    `STEP(evaluate, EVALUATOR)` + `EVAL_RESULT` pair.

    Args:
        emitter: The AG-UI emitter for streaming events.
        report: The already-computed regression report to surface.

    Returns:
        The same ``report`` (so callers can stream-and-forward in one expression).
    """
    configs = report.metric_configs()
    metrics = {c.name: c.value for c in configs}
    await emit_eval_result(
        emitter,
        overall=report.overall(),
        passed=report.passed,
        metrics=metrics,
        metric_configs=[c.as_dict() for c in configs],
    )
    return report


__all__ = ["emit_eval_result", "stream_regression_report"]
