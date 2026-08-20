"""Glass-box telemetry events (node timing/usage, reasoning, retrieval funnel, gate detail).

Drives :func:`aegis.agent.run_agent` with the shared fakes and asserts the additive event
variants/fields a frontend needs to show the whole process: per-node ``node_finished``
timing/usage, the planner's ``reasoning`` chunks, retrieval ``candidates``/``reranked``
progress with scored sources, the tool-risk gate detail, and masked-only guardrail
redaction detail. Events are plain dicts (the dict-stamp seam).
"""

from __future__ import annotations

import dataclasses

import pytest

from aegis.agent import ApprovalRegistry, run_agent
from aegis.core.types import ApprovalDecision, GuardResult, GuardVerdict


async def _run(deps, query="Please resolve request R1", persona="operations_lead"):
    """Run one query to completion, auto-approving any human gate."""
    registry = ApprovalRegistry()
    events: list[dict] = []
    async for event in run_agent(query, persona=persona, deps=deps, registry=registry):
        events.append(event)
        if event["type"] == "approval_required":
            registry.resolve(event["approval_id"], ApprovalDecision.APPROVE, approver="al")
    return events


@pytest.mark.asyncio
async def test_node_finished_emitted_per_node_with_timing(make_deps):
    events = await _run(make_deps(propose_tool=False), query="what is the policy?")
    finished = [e for e in events if e["type"] == "node_finished"]

    nodes = [e["node"] for e in finished]
    for expected in ("guard_input", "retrieve", "plan", "generate", "guard_output", "stream"):
        assert expected in nodes, f"missing node_finished for {expected}"
    assert all(e["duration_ms"] >= 0 for e in finished)

    started = [e["node"] for e in events if e["type"] == "node_started"]
    for node in started:
        assert node in nodes

    plan_done = next(e for e in finished if e["node"] == "plan")
    assert plan_done["model"] == "fake-generation"
    assert plan_done["prompt_tokens"] > 0
    retrieve_done = next(e for e in finished if e["node"] == "retrieve")
    assert retrieve_done["model"] is None
    assert retrieve_done["prompt_tokens"] == 0


@pytest.mark.asyncio
async def test_generate_node_finished_carries_model_usage(make_deps):
    events = await _run(make_deps(propose_tool=True, high_risk=True))
    generate_done = next(
        e for e in events if e["type"] == "node_finished" and e["node"] == "generate"
    )
    assert generate_done["model"] == "fake-generation"
    assert generate_done["completion_tokens"] > 0
    assert generate_done["cost_usd"] > 0


@pytest.mark.asyncio
async def test_plan_emits_reasoning_chunks(make_deps):
    events = await _run(make_deps(propose_tool=True))
    reasoning = [e for e in events if e["type"] == "reasoning"]
    assert len(reasoning) == 2
    assert all(e["text"] for e in reasoning)
    assert "resolved" in " ".join(e["text"] for e in reasoning)


@pytest.mark.asyncio
async def test_retrieve_emits_candidates_and_reranked(make_deps):
    events = await _run(make_deps(propose_tool=False), query="what is the policy?")
    statuses = [e["status"] for e in events if e["type"] == "retrieval"]
    assert statuses == ["started", "candidates", "reranked", "done"]

    reranked = next(
        e for e in events if e["type"] == "retrieval" and e["status"] == "reranked"
    )
    assert reranked["scored_sources"]
    src = reranked["scored_sources"][0]
    assert src["id"] == "kb-1"
    assert src["label"]
    assert src["score"] == pytest.approx(0.9)

    candidates = next(
        e for e in events if e["type"] == "retrieval" and e["status"] == "candidates"
    )
    assert candidates["num_candidates"] == 5
    assert candidates["num_candidates"] >= len(reranked["scored_sources"])


@pytest.mark.asyncio
async def test_a_scored_source_carries_the_document_it_came_from(make_deps):
    """A citation the console can name, which is the whole point of showing one.

    ``scored_sources`` carried ``{id, label, score}`` and nothing else, so a run grounded
    in a real PDF reached the browser as opaque hashes — verified live: a tenant-1 query
    returned 20 candidates whose ``file_path`` was ``transformer-single-column.pdf``
    every one, and not one of those paths crossed the wire.

    Asserted on **both** retrieval events that carry sources. They are built from one
    list, but they are emitted by two separate ``writer`` calls, and a change that
    enriched only the ``done`` frame would leave the panel that reads ``reranked``
    exactly as blind as before.
    """
    events = await _run(make_deps(propose_tool=False), query="what is the policy?")

    for status in ("reranked", "done"):
        frame = next(
            e for e in events if e["type"] == "retrieval" and e["status"] == status
        )
        by_id = {s["id"]: s for s in frame["scored_sources"]}
        assert by_id["kb-1"]["file_path"] == "escalation-policy.pdf", (
            f"the {status!r} frame dropped the source's document"
        )


@pytest.mark.asyncio
async def test_a_source_with_no_recorded_path_reports_an_absence(make_deps):
    """``None``, never a filename chosen on the passage's behalf.

    A chunk whose provenance was never recorded is a real shape — the dense arm's
    ``_untag_file_path`` returns ``None`` for a path carrying no owner tag — and a
    console that showed a made-up document for it would be worse than one that showed
    nothing. This is the assertion that fails if the field is ever defaulted.
    """
    events = await _run(make_deps(propose_tool=False), query="what is the policy?")

    done = next(e for e in events if e["type"] == "retrieval" and e["status"] == "done")
    by_id = {s["id"]: s for s in done["scored_sources"]}

    assert "file_path" in by_id["kb-2"], "the key must be present to be honestly empty"
    assert by_id["kb-2"]["file_path"] is None


@pytest.mark.asyncio
async def test_high_risk_action_gates_on_tool_risk_alone(make_deps):
    """The gate is the tool's declared risk tier and nothing else.

    Formerly this pair of tests asserted that the ``ml_explanation`` event carried no
    gating semantics. The ML step is gone from the graph, so the claim is now stated
    the only way it can still be falsified: the gate fires for a HIGH-risk tool, does
    not fire for a within-ceiling one, and no ML event exists on either stream.
    """
    events = await _run(make_deps(propose_tool=True, high_risk=True))
    types = [e["type"] for e in events]
    assert "approval_required" in types
    approval = next(e for e in events if e["type"] == "approval_required")
    assert approval["risk"] == "high"
    assert "ml_explanation" not in types


@pytest.mark.asyncio
async def test_within_ceiling_action_executes_without_a_gate(make_deps):
    events = await _run(make_deps(propose_tool=True, high_risk=False))
    types = [e["type"] for e in events]
    assert "approval_required" not in types
    assert "tool_result" in types
    assert "ml_explanation" not in types


@pytest.mark.asyncio
async def test_guardrail_carries_masked_redaction_detail(make_deps):
    deps = make_deps(propose_tool=False)

    async def redacting_input(text: str) -> GuardResult:
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
        e for e in events if e["type"] == "guardrail" and e["stage"] == "input"
    )
    assert guard["verdict"] == "redact"
    assert guard["layer"] == "pii"
    assert [r["kind"] for r in guard["redactions"]] == ["EMAIL"]
    # CRITICAL: only masked text on the wire — never the raw PII.
    assert guard["before_masked"] is not None and guard["after"] is not None
    assert "jane@corp.com" not in guard["before_masked"]
    assert "jane@corp.com" not in guard["after"]
    assert "[REDACTED_EMAIL]" in guard["after"]


@pytest.mark.asyncio
async def test_guardrail_pass_has_no_redaction_detail(make_deps):
    events = await _run(make_deps(propose_tool=False), query="what is the policy?")
    guard = next(
        e for e in events if e["type"] == "guardrail" and e["stage"] == "input"
    )
    assert guard["verdict"] == "pass"
    assert guard["redactions"] == []
    assert guard["before_masked"] is None
    assert guard["after"] is None
