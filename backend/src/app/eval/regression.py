"""Strangler shim: ``app.eval.regression`` delegates to :mod:`aegis.evals.regression`.

The DeepEval-pattern regression gate now lives in the standalone ``aegis.evals`` package,
where the agentic tool-selection case is **inject-only** (a ``route_fn`` + ``roster``). This
shim wires in the backend's real supervisor router (:func:`app.agent.router.route_query`
over :func:`app.agent.router.load_roster`) by default, so :func:`run_regression_gate` /
:func:`run_tool_selection_eval` behave exactly as before (the agentic case runs) while a
caller can still override the router. Every other symbol is re-exported unchanged.

``python -m app.eval.regression`` still prints the human-readable report + POSIX exit code.
"""

from __future__ import annotations

from collections.abc import Sequence

from aegis.evals.regression import (
    DEFAULT_METRICS,
    ROUTER_EVAL_CASES,
    GateCaseResult,
    Metric,
    MetricResult,
    RegressionReport,
    RouterEvalCase,
)
from aegis.evals.regression import run_regression_gate as _aegis_run_regression_gate
from aegis.evals.regression import run_tool_selection_eval as _aegis_run_tool_selection_eval

__all__ = [
    "DEFAULT_METRICS",
    "ROUTER_EVAL_CASES",
    "GateCaseResult",
    "Metric",
    "MetricResult",
    "RegressionReport",
    "RouterEvalCase",
    "run_regression_gate",
    "run_tool_selection_eval",
]


def _default_router() -> tuple:
    """Return ``(route_query, roster)`` from the backend supervisor router."""
    from app.agent.router import load_roster, route_query  # noqa: PLC0415

    return route_query, load_roster()


async def run_tool_selection_eval(
    *, complete=None, route_fn=None, roster=None  # noqa: ANN001
) -> tuple[float, list[tuple[str, str, str, bool]]]:
    """Run the agentic router eval, defaulting to the backend supervisor router."""
    if route_fn is None:
        route_fn, default_roster = _default_router()
        if roster is None:
            roster = default_roster
    return await _aegis_run_tool_selection_eval(
        complete=complete, route_fn=route_fn, roster=roster
    )


async def run_regression_gate(
    *,
    complete=None,  # noqa: ANN001
    metrics: Sequence[Metric] = DEFAULT_METRICS,
    route_fn=None,  # noqa: ANN001
    roster=None,  # noqa: ANN001
) -> RegressionReport:
    """Run the regression gate, defaulting in the backend router so the agentic case runs."""
    if route_fn is None:
        route_fn, default_roster = _default_router()
        if roster is None:
            roster = default_roster
    return await _aegis_run_regression_gate(
        complete=complete, metrics=metrics, route_fn=route_fn, roster=roster
    )


def _main() -> int:
    """Run the regression gate (with the backend router), print a report, return an exit code."""
    import asyncio  # noqa: PLC0415

    report = asyncio.run(run_regression_gate())
    print(f"DeepEval-pattern regression gate over {len(report.cases)} cases:")  # noqa: T201
    for case in report.cases:
        flag = "PASS" if case.passed else "FAIL"
        print(f"  [{flag}] {case.name}")  # noqa: T201
        for m in case.metrics:
            mark = "ok" if m.passed else "XX"
            print(  # noqa: T201
                f"        {mark} {m.name} = {m.value:.3f} (threshold {m.threshold:.3f})"
            )
    if report.passed:
        print("PASS")  # noqa: T201
        return 0
    failed = ", ".join(c.name for c in report.failures())
    print(f"FAIL: {failed}")  # noqa: T201
    return 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(_main())
