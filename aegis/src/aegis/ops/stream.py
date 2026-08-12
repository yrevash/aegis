"""AG-UI streaming for the LLM-Ops self-improvement loop — à la carte over the emitter.

Makes the closed loop **observable**: each stage of Trace → Eval → Diagnose → Gate →
Release emits one AG-UI event (a `STEP_STARTED`/`STEP_FINISHED` bracket around a `CUSTOM`
event) so the LLMOps UI can render the loop as it runs. The numbers are **real** — they are
whatever the actual :mod:`aegis.ops.trace_eval` / :mod:`aegis.ops.diagnose` /
:mod:`aegis.ops.release` produced — never fabricated; this module only surfaces already-
computed results and never calls a model. The streamed values are exactly what was written
to the ``EvalResult`` / ``PromptVersion`` rows.

Four à la carte helpers, one per loop step:

* :func:`emit_eval_result` — the graded-run score + per-metric breakdown (`EVAL_RESULT`,
  the same event the offline eval pass emits, keyed ``SpanKind.EVALUATOR``).
* :func:`emit_diagnose` — the Reflexion draft (id + rationale + failure breakdown + the
  optional change-risk tier) (`OPS_DIAGNOSE`).
* :func:`emit_gate_decision` — the eval gate + change-risk verdict (draft/baseline score,
  eval delta vs margin, risk tier, whether the gate passed) (`OPS_GATE_DECISION`).
* :func:`emit_release_outcome` — the release action (promoted | staged_for_approval |
  rejected, version id, eval delta, approval id) (`OPS_RELEASE`).

:func:`stream_release` is a convenience that emits the gate decision then the release
outcome from one :class:`~aegis.ops.release.ReleaseResult`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aegis.core import stream_names
from aegis.core.events import SpanKind

if TYPE_CHECKING:
    from aegis.core.stream import AegisEmitter
    from aegis.ops.diagnose import DiagnoseResult
    from aegis.ops.release import ReleaseResult
    from aegis.ops.trace_eval import RunEval

_EVAL_STEP = "ops_eval"
_DIAGNOSE_STEP = "ops_diagnose"
_GATE_STEP = "ops_gate"
_RELEASE_STEP = "ops_release"


async def emit_eval_result(emitter: AegisEmitter, run_eval: RunEval) -> RunEval:
    """Emit a `STEP(ops_eval, EVALUATOR)` bracket carrying the graded run's `EVAL_RESULT`.

    The payload is the exact :class:`~aegis.ops.trace_eval.RunEval` the grader produced —
    ``overall`` is the mean of the persisted per-metric scores and ``metrics`` mirrors the
    ``EvalResult`` rows written for the run.

    Args:
        emitter: The AG-UI emitter for streaming events.
        run_eval: The graded-run outcome to surface.

    Returns:
        The same ``run_eval`` (so callers can stream-and-forward in one expression).
    """
    async with emitter.step(_EVAL_STEP, SpanKind.EVALUATOR):
        await emitter.custom(
            stream_names.EVAL_RESULT,
            {
                "overall": float(run_eval.overall),
                "passed": bool(run_eval.passed),
                "metrics": {k: float(v) for k, v in run_eval.metrics.items()},
            },
        )
    return run_eval


async def emit_diagnose(
    emitter: AegisEmitter,
    result: DiagnoseResult,
    *,
    risk_tier: str | None = None,
) -> DiagnoseResult:
    """Emit a `STEP(ops_diagnose, CHAIN)` bracket carrying the drafted `OPS_DIAGNOSE` event.

    Surfaces whether a draft was produced (``draftVersionId``), the one-line rationale /
    failure summary, how many failing evals were clustered, the per-metric failure
    breakdown, and — when the orchestrator has classified the draft — its change-risk tier.

    Args:
        emitter: The AG-UI emitter for streaming events.
        result: The :class:`~aegis.ops.diagnose.DiagnoseResult` to surface.
        risk_tier: Optional pre-computed change-risk tier of the draft (``"low"`` /
            ``"medium"`` / ``"high"``), or ``None`` when not yet classified.

    Returns:
        The same ``result`` (stream-and-forward).
    """
    async with emitter.step(_DIAGNOSE_STEP, SpanKind.CHAIN):
        await emitter.custom(
            stream_names.OPS_DIAGNOSE,
            {
                "draftVersionId": result.draft_version_id,
                "rationale": result.failure_summary,
                "failuresConsidered": int(result.failures_considered),
                "metricBreakdown": {
                    k: int(v) for k, v in result.metric_breakdown.items()
                },
                "riskTier": risk_tier,
            },
        )
    return result


def _eval_delta(result: ReleaseResult) -> float:
    """The draft's eval score minus the baseline's (the gate's decision signal)."""
    return float(result.eval_score) - float(result.baseline_score)


async def emit_gate_decision(
    emitter: AegisEmitter, result: ReleaseResult, *, margin: float = 0.0
) -> ReleaseResult:
    """Emit a `STEP(ops_gate, CHAIN)` bracket carrying the `OPS_GATE_DECISION` verdict.

    The payload carries the real draft/baseline scores from ``eval_fn``, their delta, the
    ``margin`` the gate required, whether the eval gate passed (``rejected`` ⇒ it did not),
    and the deterministic change-risk tier + reasons.

    Args:
        emitter: The AG-UI emitter for streaming events.
        result: The :class:`~aegis.ops.release.ReleaseResult` the gate produced.
        margin: The eval pass margin the gate applied (for the UI to show delta vs bar).

    Returns:
        The same ``result`` (stream-and-forward).
    """
    async with emitter.step(_GATE_STEP, SpanKind.CHAIN):
        await emitter.custom(
            stream_names.OPS_GATE_DECISION,
            {
                "evalScore": float(result.eval_score),
                "baselineScore": float(result.baseline_score),
                "evalDelta": _eval_delta(result),
                "margin": float(margin),
                "passedGate": result.outcome != "rejected",
                "riskTier": result.risk.level,
                "riskReasons": list(result.risk.reasons),
            },
        )
    return result


async def emit_release_outcome(
    emitter: AegisEmitter,
    result: ReleaseResult,
    *,
    draft_version_id: int | None = None,
    active_version: int | None = None,
) -> ReleaseResult:
    """Emit a `STEP(ops_release, CHAIN)` bracket carrying the `OPS_RELEASE` outcome event.

    The payload carries the terminal outcome (``promoted`` | ``staged_for_approval`` |
    ``rejected``), the draft id, the now-active version (when promoted), the eval delta, the
    risk tier, and the approval id (when staged). ``activeVersion`` is the persisted
    ``PromptVersion.version`` the caller read back, so the streamed number equals the row.

    Args:
        emitter: The AG-UI emitter for streaming events.
        result: The :class:`~aegis.ops.release.ReleaseResult` to surface.
        draft_version_id: The draft the release acted on.
        active_version: The version now live after the release (``None`` unless promoted).

    Returns:
        The same ``result`` (stream-and-forward).
    """
    async with emitter.step(_RELEASE_STEP, SpanKind.CHAIN):
        await emitter.custom(
            stream_names.OPS_RELEASE,
            {
                "outcome": result.outcome,
                "draftVersionId": draft_version_id,
                "activeVersion": active_version,
                "evalScore": float(result.eval_score),
                "baselineScore": float(result.baseline_score),
                "evalDelta": _eval_delta(result),
                "riskTier": result.risk.level,
                "approvalId": result.approval_id,
                "reason": result.reason,
            },
        )
    return result


async def stream_release(
    emitter: AegisEmitter,
    result: ReleaseResult,
    *,
    margin: float = 0.0,
    draft_version_id: int | None = None,
    active_version: int | None = None,
) -> ReleaseResult:
    """Emit the gate decision then the release outcome for one release, in order.

    A convenience for the ``/ops/release`` surface: :func:`emit_gate_decision` followed by
    :func:`emit_release_outcome`, both from the same :class:`~aegis.ops.release.ReleaseResult`.

    Args:
        emitter: The AG-UI emitter for streaming events.
        result: The release result to surface.
        margin: The eval pass margin the gate applied.
        draft_version_id: The draft the release acted on.
        active_version: The version now live after the release (``None`` unless promoted).

    Returns:
        The same ``result`` (stream-and-forward).
    """
    await emit_gate_decision(emitter, result, margin=margin)
    await emit_release_outcome(
        emitter,
        result,
        draft_version_id=draft_version_id,
        active_version=active_version,
    )
    return result


__all__ = [
    "emit_diagnose",
    "emit_eval_result",
    "emit_gate_decision",
    "emit_release_outcome",
    "stream_release",
]
