"""Observability: the LLM-Ops loop streams REAL numbers for every stage (offline).

Covers :mod:`aegis.ops.stream`:

* the three new ops CustomEvent names are registered/known;
* ``emit_eval_result`` brackets a STEP(EVALUATOR) and emits the graded run's overall +
  per-metric payload;
* ``emit_diagnose`` / ``emit_gate_decision`` / ``emit_release_outcome`` each bracket a STEP
  and emit their real payloads (eval delta = draft − baseline; passedGate; outcome/version);
* data consistency: the streamed eval numbers equal the persisted ``EvalResult`` rows and
  the streamed active version equals the persisted ``PromptVersion`` after a real release;
* the payloads validate through the REAL ``AegisEmitter`` (name is in ``stream_names.ALL``).
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from aegis.core import stream_names
from aegis.core.events import SpanKind
from aegis.core.stream import AegisEmitter
from aegis.ops import registry
from aegis.ops.diagnose import DiagnoseResult
from aegis.ops.models import EvalResult, PromptStatus
from aegis.ops.release import ChangeRisk, ReleaseResult, release
from aegis.ops.stream import (
    emit_diagnose,
    emit_eval_result,
    emit_gate_decision,
    emit_release_outcome,
    stream_release,
)
from aegis.ops.trace_eval import RunEval, evaluate_run

from .conftest import DEFAULT_PERSONA_ID

PK = DEFAULT_PERSONA_ID
BASE = "\n".join(f"instruction line {i}" for i in range(1, 9))
LOW_DRAFT = BASE.replace("instruction line 2", "instruction line two")


class _FakeStepScope:
    def __init__(self, log: list, name: str, kind: SpanKind) -> None:
        self._log, self._name, self._kind = log, name, kind

    async def __aenter__(self) -> _FakeStepScope:
        self._log.append(("step_start", self._name, self._kind))
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._log.append(("step_end", self._name, self._kind))


class _FakeEmitter:
    """Captures the emitter calls the stream helpers make (no AG-UI transport)."""

    def __init__(self) -> None:
        self.log: list = []

    def step(self, name: str, kind: SpanKind) -> _FakeStepScope:
        return _FakeStepScope(self.log, name, kind)

    async def custom(self, name: str, value: dict) -> None:
        self.log.append(("custom", name, value))


def _customs(emitter: _FakeEmitter) -> dict[str, dict]:
    return {row[1]: row[2] for row in emitter.log if row[0] == "custom"}


def _eval_fn(scores: dict[str, float], default: float = 0.0):
    async def eval_fn(system_prompt: str) -> float:
        return scores.get(system_prompt, default)

    return eval_fn


async def _approval(calls: list):
    async def approval_enqueue(*, prompt_key, draft_version_id, risk, reason) -> str:  # noqa: ANN001
        calls.append({"draft_version_id": draft_version_id, "risk": risk})
        return "appr-1"

    return approval_enqueue


async def _make_active_and_draft(s, draft_prompt: str):
    active = await registry.create_draft(s, prompt_key=PK, system_prompt=BASE)
    await registry.promote(s, active.id)
    draft = await registry.create_draft(
        s, prompt_key=PK, system_prompt=draft_prompt, parent_version=active.version
    )
    await s.flush()
    return active, draft


# ── registered names ─────────────────────────────────────────────────────────


def test_ops_stream_names_are_registered():
    for name in (
        stream_names.OPS_DIAGNOSE,
        stream_names.OPS_GATE_DECISION,
        stream_names.OPS_RELEASE,
    ):
        assert stream_names.is_known(name)
    assert stream_names.OPS_DIAGNOSE == "ops_diagnose"
    assert stream_names.OPS_GATE_DECISION == "ops_gate_decision"
    assert stream_names.OPS_RELEASE == "ops_release"


# ── emit_eval_result ─────────────────────────────────────────────────────────


async def test_emit_eval_result_brackets_evaluator_step_with_real_numbers():
    emitter = _FakeEmitter()
    run_eval = RunEval(overall=0.75, passed=True, metrics={"answer": 0.9, "step:tool": 0.6})
    returned = await emit_eval_result(emitter, run_eval)
    assert returned is run_eval  # stream-and-forward
    assert [r[0] for r in emitter.log] == ["step_start", "custom", "step_end"]
    assert emitter.log[0][1:] == ("ops_eval", SpanKind.EVALUATOR)
    _, name, value = emitter.log[1]
    assert name == stream_names.EVAL_RESULT
    assert value == {
        "overall": 0.75,
        "passed": True,
        "metrics": {"answer": 0.9, "step:tool": 0.6},
    }


# ── emit_diagnose ────────────────────────────────────────────────────────────


async def test_emit_diagnose_payload_carries_draft_and_breakdown():
    emitter = _FakeEmitter()
    result = DiagnoseResult(
        draft_version_id=42,
        failure_summary="3 failing evals: answer=2, step:tool=1",
        failures_considered=3,
        metric_breakdown={"answer": 2, "step:tool": 1},
    )
    await emit_diagnose(emitter, result, risk_tier="low")
    assert emitter.log[0][1:] == ("ops_diagnose", SpanKind.CHAIN)
    payload = _customs(emitter)[stream_names.OPS_DIAGNOSE]
    assert payload["draftVersionId"] == 42
    assert payload["failuresConsidered"] == 3
    assert payload["metricBreakdown"] == {"answer": 2, "step:tool": 1}
    assert payload["riskTier"] == "low"
    assert payload["rationale"].startswith("3 failing evals")


# ── emit_gate_decision / emit_release_outcome ────────────────────────────────


def _result(outcome: str, *, eval_score: float, baseline: float, level: str, approval=None):
    return ReleaseResult(
        outcome=outcome,
        risk=ChangeRisk(level=level, reasons=[f"{level} reason"]),
        eval_score=eval_score,
        baseline_score=baseline,
        reason=f"{outcome}: draft {eval_score} baseline {baseline}",
        approval_id=approval,
    )


async def test_emit_gate_decision_delta_and_verdict():
    emitter = _FakeEmitter()
    res = _result("promoted", eval_score=0.9, baseline=0.5, level="low")
    await emit_gate_decision(emitter, res, margin=0.1)
    assert emitter.log[0][1:] == ("ops_gate", SpanKind.CHAIN)
    p = _customs(emitter)[stream_names.OPS_GATE_DECISION]
    assert p["evalScore"] == 0.9 and p["baselineScore"] == 0.5
    assert p["evalDelta"] == pytest.approx(0.4)
    assert p["margin"] == 0.1
    assert p["passedGate"] is True
    assert p["riskTier"] == "low" and p["riskReasons"] == ["low reason"]


async def test_emit_gate_decision_rejected_is_not_passed():
    emitter = _FakeEmitter()
    res = _result("rejected", eval_score=0.3, baseline=0.8, level="low")
    await emit_gate_decision(emitter, res)
    p = _customs(emitter)[stream_names.OPS_GATE_DECISION]
    assert p["passedGate"] is False
    assert p["evalDelta"] == pytest.approx(-0.5)


async def test_emit_release_outcome_payload():
    emitter = _FakeEmitter()
    res = _result("staged_for_approval", eval_score=0.95, baseline=0.5, level="high",
                  approval="appr-1")
    await emit_release_outcome(emitter, res, draft_version_id=7, active_version=None)
    assert emitter.log[0][1:] == ("ops_release", SpanKind.CHAIN)
    p = _customs(emitter)[stream_names.OPS_RELEASE]
    assert p["outcome"] == "staged_for_approval"
    assert p["draftVersionId"] == 7 and p["activeVersion"] is None
    assert p["approvalId"] == "appr-1" and p["riskTier"] == "high"
    assert p["evalDelta"] == pytest.approx(0.45)


async def test_stream_release_emits_gate_then_outcome_in_order():
    emitter = _FakeEmitter()
    res = _result("promoted", eval_score=0.9, baseline=0.5, level="low")
    await stream_release(emitter, res, margin=0.0, draft_version_id=3, active_version=2)
    names = [r[1] for r in emitter.log if r[0] == "custom"]
    assert names == [stream_names.OPS_GATE_DECISION, stream_names.OPS_RELEASE]
    assert _customs(emitter)[stream_names.OPS_RELEASE]["activeVersion"] == 2


# ── payloads validate through the REAL emitter (name registration is enforced) ─


async def test_payloads_pass_the_real_emitter_name_check():
    frames: list[str] = []

    async def sink(frame: str) -> None:
        frames.append(frame)

    emitter = AegisEmitter(thread_id="t", run_id="r", sink=sink)
    await emit_eval_result(emitter, RunEval(overall=0.8, passed=True, metrics={"answer": 0.8}))
    await emit_diagnose(
        emitter,
        DiagnoseResult(draft_version_id=1, failure_summary="s", failures_considered=1,
                       metric_breakdown={"answer": 1}),
        risk_tier="low",
    )
    await stream_release(
        emitter, _result("promoted", eval_score=0.9, baseline=0.5, level="low"),
        draft_version_id=1, active_version=1,
    )
    # Four CUSTOM frames emitted (eval, diagnose, gate, release) — the real emitter would
    # have raised ValueError on any unregistered name.
    assert sum("CUSTOM" in f for f in frames) == 4


# ── data consistency: streamed numbers == persisted rows ─────────────────────


async def test_streamed_eval_equals_persisted_eval_rows(db):
    """emit_eval_result surfaces exactly the scores written to EvalResult rows."""
    async with db() as s:
        run_eval = await evaluate_run(
            s,
            run_id="run-1",
            query="what is the capital of france",
            answer="the capital of france is paris",
            contexts=["paris is the capital of france"],
            steps=[{"node": "retrieve", "kind": "RETRIEVER",
                    "detail": {"contexts": ["paris is the capital of france"]}}],
            complete=None,  # deterministic lexical proxies — real, offline numbers
            prompt_key=PK,
        )
        await s.commit()

        emitter = _FakeEmitter()
        await emit_eval_result(emitter, run_eval)
        streamed = _customs(emitter)[stream_names.EVAL_RESULT]

        rows = (
            await s.execute(select(EvalResult).where(EvalResult.run_id == "run-1"))
        ).scalars().all()
        persisted = {r.metric: r.score for r in rows}

    # Every streamed metric equals its persisted row, and the streamed overall is their mean.
    assert streamed["metrics"] == pytest.approx(persisted)
    assert streamed["overall"] == pytest.approx(sum(persisted.values()) / len(persisted))
    assert streamed["passed"] == run_eval.passed


async def test_streamed_release_active_version_equals_persisted_row(db):
    """After a real promote, the streamed activeVersion equals the live PromptVersion row."""
    async with db() as s:
        _, draft = await _make_active_and_draft(s, LOW_DRAFT)
        result = await release(
            s,
            draft_version_id=draft.id,
            eval_fn=_eval_fn({LOW_DRAFT: 0.9, BASE: 0.5}),
            approval_enqueue=await _approval([]),
        )
        await s.commit()
        assert result.outcome == "promoted"
        active = await registry.get_active(s, PK)

        emitter = _FakeEmitter()
        await stream_release(
            emitter, result, draft_version_id=draft.id, active_version=active.version
        )
        p = _customs(emitter)[stream_names.OPS_RELEASE]

    assert active.status is PromptStatus.ACTIVE
    assert p["activeVersion"] == active.version
    assert p["evalScore"] == pytest.approx(result.eval_score)
    assert p["baselineScore"] == pytest.approx(result.baseline_score)
    assert p["evalDelta"] == pytest.approx(result.eval_score - result.baseline_score)


def test_gate_decision_payload_json_serializable():
    """The gate payload is plain JSON (no dataclasses leak into the wire)."""
    res = _result("promoted", eval_score=0.9, baseline=0.5, level="medium")
    payload = {
        "evalScore": float(res.eval_score),
        "riskTier": res.risk.level,
        "riskReasons": list(res.risk.reasons),
    }
    assert json.loads(json.dumps(payload)) == payload
