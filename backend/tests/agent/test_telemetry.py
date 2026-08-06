"""Tests for the glass-box telemetry events (node timing, reasoning, gate detail).

These drive :func:`app.agent.run_agent` with the shared fakes and assert the new,
additive :data:`~app.api.schemas.StreamEvent` variants/fields the frontend needs
to show the whole process: per-node ``node_finished`` timing/usage, the planner's
``reasoning`` chunks, retrieval ``candidates``/``reranked`` progress with scored
sources, the gate detail on ``ml_explanation``, and masked-only guardrail
redaction detail.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.agent import ApprovalRegistry, run_agent
from app.api.schemas import GuardVerdict
from app.guardrails.models import GuardResult


async def _run(deps, query="Please resolve request R1", persona="operations_lead"):
    """Run one query to completion, auto-approving any human gate."""
    registry = ApprovalRegistry()
    events: list = []
    async for event in run_agent(query, persona=persona, deps=deps, registry=registry):
        events.append(event)
        if event.type == "approval_required":
            from app.api.schemas import ApprovalDecision

            registry.resolve(event.approval_id, ApprovalDecision.APPROVE, approver="al")
    return events


@pytest.mark.asyncio
async def test_node_finished_emitted_per_node_with_timing(make_deps):
    events = await _run(make_deps(propose_tool=False), query="what is the policy?")
    finished = [e for e in events if e.type == "node_finished"]

    nodes = [e.node for e in finished]
    # Every node on the pure-Q&A path reports a node_finished with a duration.
    for expected in ("guard_input", "retrieve", "plan", "generate", "guard_output", "stream"):
        assert expected in nodes, f"missing node_finished for {expected}"
    assert all(e.duration_ms >= 0 for e in finished)

    # Each node_started is paired by a node_finished for the same node.
    started = [e.node for e in events if e.type == "node_started"]
    for node in started:
        assert node in nodes

    # The plan node made an LLM call → its node_finished carries model + usage.
    plan_done = next(e for e in finished if e.node == "plan")
    assert plan_done.model == "fake-generation"
    assert plan_done.prompt_tokens > 0
    # A node that made no model call omits the model (stays None).
    retrieve_done = next(e for e in finished if e.node == "retrieve")
    assert retrieve_done.model is None
    assert retrieve_done.prompt_tokens == 0


@pytest.mark.asyncio
async def test_generate_node_finished_carries_model_usage(make_deps):
    # Action path → act runs → generate makes an LLM call to compose the answer.
    events = await _run(make_deps(propose_tool=True, high_risk=True))
    generate_done = next(
        e for e in events if e.type == "node_finished" and e.node == "generate"
    )
    assert generate_done.model == "fake-generation"
    assert generate_done.completion_tokens > 0
    assert generate_done.cost_usd > 0


@pytest.mark.asyncio
async def test_plan_emits_reasoning_chunks(make_deps):
    events = await _run(make_deps(propose_tool=True, uncertain=True))
    reasoning = [e for e in events if e.type == "reasoning"]
    # The planner's two-sentence plan is chunked into two reasoning events.
    assert len(reasoning) == 2
    assert all(e.text for e in reasoning)
    assert "resolved" in " ".join(e.text for e in reasoning)


@pytest.mark.asyncio
async def test_retrieve_emits_candidates_and_reranked(make_deps):
    events = await _run(make_deps(propose_tool=False), query="what is the policy?")
    statuses = [e.status for e in events if e.type == "retrieval"]
    assert statuses == ["started", "candidates", "reranked", "done"]

    reranked = next(
        e for e in events if e.type == "retrieval" and e.status == "reranked"
    )
    assert reranked.scored_sources
    src = reranked.scored_sources[0]
    assert src.id == "kb-1"
    assert src.label  # a short snippet for display
    assert src.score == pytest.approx(0.9)

    candidates = next(
        e for e in events if e.type == "retrieval" and e.status == "candidates"
    )
    # num_candidates is the honest WIDE-RECALL pool (N), not the survivor count.
    # The fake recalls 5 and reranks down to 1, so the funnel must show 5 → 1.
    assert candidates.num_candidates == 5
    assert candidates.num_candidates >= len(reranked.scored_sources)


@pytest.mark.asyncio
async def test_ml_event_is_informational_evidence_no_gating(make_deps):
    # ML is a solution SIGNAL, not a gate: the ml_explanation event carries the
    # prediction/interval/SHAP as supporting evidence and NO gating semantics —
    # even when the action itself is high-risk and the human gate fires on risk.
    events = await _run(make_deps(propose_tool=True, high_risk=True))
    ml = next(e for e in events if e.type == "ml_explanation")
    assert ml.gated is None  # no gating signal on the ML event
    assert ml.gate_reason is None
    # The evidence payload is present for the answer to cite.
    assert ml.prediction is not None
    assert ml.conformal_interval is not None
    assert ml.shap_attribution  # signed drivers


@pytest.mark.asyncio
async def test_ml_event_present_but_never_gates_a_low_risk_action(make_deps):
    # A within-ceiling (MEDIUM) action executes autonomously; the ML event is still
    # emitted as evidence but never routes the run to the human gate.
    events = await _run(make_deps(propose_tool=True, uncertain=True, high_risk=False))
    types = [e.type for e in events]
    assert "approval_required" not in types  # ML uncertainty does NOT gate
    ml = next(e for e in events if e.type == "ml_explanation")
    assert ml.gated is None
    assert ml.prediction is not None


@pytest.mark.asyncio
async def test_guardrail_carries_masked_redaction_detail(make_deps):
    deps = make_deps(propose_tool=False)

    async def redacting_input(text: str) -> GuardResult:
        # Simulate the PII rail firing: MASKED text only, plus the kinds.
        return GuardResult(
            verdict=GuardVerdict.REDACT,
            reason="Redacted PII on the inbound path: EMAIL.",
            text="my email is [REDACTED_EMAIL], summarise the policy",
            layer="pii",
            redactions=["EMAIL"],
        )

    deps = dataclasses.replace(deps, check_input=redacting_input)
    events = await _run(deps, query="my email is jane@corp.com, summarise the policy")

    guard = next(
        e for e in events if e.type == "guardrail" and e.stage.value == "input"
    )
    assert guard.verdict is GuardVerdict.REDACT
    assert guard.layer == "pii"
    assert [r.kind for r in guard.redactions] == ["EMAIL"]
    # CRITICAL: only masked text on the wire — never the raw PII.
    assert guard.before_masked is not None and guard.after is not None
    assert "jane@corp.com" not in guard.before_masked
    assert "jane@corp.com" not in guard.after
    assert "[REDACTED_EMAIL]" in guard.after


@pytest.mark.asyncio
async def test_guardrail_pass_has_no_redaction_detail(make_deps):
    events = await _run(make_deps(propose_tool=False), query="what is the policy?")
    guard = next(
        e for e in events if e.type == "guardrail" and e.stage.value == "input"
    )
    assert guard.verdict is GuardVerdict.PASS
    assert guard.redactions == []
    assert guard.before_masked is None
    assert guard.after is None
