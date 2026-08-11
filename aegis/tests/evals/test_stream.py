"""AG-UI streaming for the eval pass emits a STEP(EVALUATOR) + EVAL_RESULT custom event."""

from __future__ import annotations

import pytest

from aegis.core import stream_names
from aegis.core.events import SpanKind
from aegis.evals.regression import (
    GateCaseResult,
    MetricResult,
    RegressionReport,
)
from aegis.evals.stream import emit_eval_result, stream_regression_report

pytestmark = pytest.mark.asyncio


class _FakeStepScope:
    def __init__(self, log: list, name: str, kind: SpanKind) -> None:
        self._log = log
        self._name = name
        self._kind = kind

    async def __aenter__(self) -> _FakeStepScope:
        self._log.append(("step_start", self._name, self._kind))
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._log.append(("step_end", self._name, self._kind))


class _FakeEmitter:
    """Captures the emitter calls the stream helper makes (no real AG-UI transport)."""

    def __init__(self) -> None:
        self.log: list = []

    def step(self, name: str, kind: SpanKind) -> _FakeStepScope:
        return _FakeStepScope(self.log, name, kind)

    async def custom(self, name: str, value: dict) -> None:
        self.log.append(("custom", name, value))


async def test_eval_result_registered_stream_name():
    """The EVAL_RESULT custom-event name is a registered, known stream name."""
    assert stream_names.EVAL_RESULT == "eval_result"
    assert stream_names.is_known(stream_names.EVAL_RESULT)


async def test_emit_eval_result_brackets_evaluator_step_and_custom():
    """`emit_eval_result` opens a STEP(evaluate, EVALUATOR) and emits EVAL_RESULT inside."""
    emitter = _FakeEmitter()
    await emit_eval_result(
        emitter, overall=0.9, passed=True, metrics={"groundedness": 0.9}
    )
    kinds = [row[0] for row in emitter.log]
    assert kinds == ["step_start", "custom", "step_end"]
    # The step is an EVALUATOR span named "evaluate".
    assert emitter.log[0][1] == "evaluate"
    assert emitter.log[0][2] is SpanKind.EVALUATOR
    # The custom event carries the eval verdict payload.
    _, name, value = emitter.log[1]
    assert name == stream_names.EVAL_RESULT
    assert value == {"overall": 0.9, "passed": True, "metrics": {"groundedness": 0.9}}


async def test_stream_regression_report_derives_payload():
    """`stream_regression_report` flattens a report into overall/passed/metrics + forwards it."""
    report = RegressionReport(
        cases=[
            GateCaseResult(
                name="retrieval: q",
                metrics=[
                    MetricResult("context_recall", 1.0, 0.95, True),
                    MetricResult("groundedness", 0.8, 0.85, False),
                ],
                passed=False,
            ),
        ],
        passed=False,
    )
    emitter = _FakeEmitter()
    returned = await stream_regression_report(emitter, report)
    assert returned is report  # stream-and-forward
    _, name, value = emitter.log[1]
    assert name == stream_names.EVAL_RESULT
    assert value["passed"] is False
    assert value["metrics"] == {"context_recall": 1.0, "groundedness": 0.8}
    assert value["overall"] == pytest.approx((1.0 + 0.8) / 2)
