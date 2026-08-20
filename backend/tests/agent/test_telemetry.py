"""Tests for the glass-box telemetry events (node timing, reasoning, gate detail).

These drive :func:`app.agent.run_agent` with the shared fakes and assert the new,
additive :data:`~app.api.schemas.StreamEvent` variants/fields the frontend needs
to show the whole process: per-node ``node_finished`` timing/usage, the planner's
``reasoning`` chunks, retrieval ``candidates``/``reranked`` progress with scored
sources, the tool-risk gate detail, and masked-only guardrail redaction detail.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.agent import ApprovalRegistry, run_agent
from app.api.schemas import GuardVerdict, RiskLevel
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
    events = await _run(make_deps(propose_tool=True))
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
    # The fake recalls 5 and reranks down to 2, so the funnel must show 5 → 2.
    assert candidates.num_candidates == 5
    assert candidates.num_candidates >= len(reranked.scored_sources)


@pytest.mark.asyncio
async def test_the_locked_wire_schema_carries_a_source_s_document(make_deps):
    """The seam that would have swallowed the fix in silence.

    ``app.agent.events.stamp`` validates every built dict against the locked
    ``StreamEvent`` union, and ``ScoredSource`` does **not** forbid extra keys — so a
    graph that started emitting ``file_path`` while the model still declared only
    ``{id, label, score}`` would have had the field dropped by Pydantic, with no error
    anywhere and an unchanged, passing test in ``aegis``. Asserting it on the *validated*
    model rather than on the dict is what makes this a test of the wire.
    """
    events = await _run(make_deps(propose_tool=False), query="what is the policy?")

    done = next(e for e in events if e.type == "retrieval" and e.status == "done")
    by_id = {s.id: s for s in done.scored_sources}

    assert by_id["kb-1"].file_path == "escalation-policy.pdf"
    assert by_id["kb-2"].file_path is None, (
        "a source with no recorded provenance must serialise as an absence, not as a "
        "filename chosen on its behalf"
    )
    # And it survives serialisation, which is what actually reaches the browser.
    assert done.model_dump(mode="json")["scored_sources"][0]["file_path"] == (
        "escalation-policy.pdf"
    )


@pytest.mark.asyncio
async def test_high_risk_action_gates_on_tool_risk_alone(make_deps):
    """The gate is the tool's declared risk tier and nothing else.

    This replaces a pair of tests that asserted the ``ml_explanation`` event carried
    no gating semantics. The ML step no longer runs in the graph, so the same claim
    is restated in the only falsifiable form left: a HIGH-risk tool gates, a
    within-ceiling one does not, and no ML event reaches the wire either way.
    """
    events = await _run(make_deps(propose_tool=True, high_risk=True))
    types = [e.type for e in events]
    assert "approval_required" in types
    approval = next(e for e in events if e.type == "approval_required")
    assert approval.risk is RiskLevel.HIGH
    assert "ml_explanation" not in types


@pytest.mark.asyncio
async def test_within_ceiling_action_executes_without_a_gate(make_deps):
    # A within-ceiling (MEDIUM) action executes autonomously — no gate, no ML event.
    events = await _run(make_deps(propose_tool=True, high_risk=False))
    types = [e.type for e in events]
    assert "approval_required" not in types
    assert "tool_result" in types
    assert "ml_explanation" not in types


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
