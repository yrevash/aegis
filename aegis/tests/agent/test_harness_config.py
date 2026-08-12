"""Tweakable-config surface + per-knob behaviour-change tests (fakes only).

Two claims are pinned here:

* :meth:`AgentConfig.as_dict` and :func:`harness_config` expose EVERY knob as data (the
  "tweak the agent" panel the harness UI renders), with types/defaults/allowed values.
* Flipping each key knob genuinely changes the run — the config is not decorative:
  a lower ``gate_min_risk`` pauses an action the default tier waves through; toggling
  ``self_repair_enabled`` adds/removes the reflect→plan loop; ``max_plan_iterations``
  hard-caps the rounds; ``answer_cache_enabled`` skips (or runs) the planner.
"""

from __future__ import annotations

import dataclasses

import pytest

from aegis.agent import AgentConfig, ApprovalRegistry, harness_config, run_agent
from aegis.core.types import ApprovalDecision, RiskLevel
from aegis.retrieval.models import GraphDelta, RetrievalResult, Source
from aegis.retrieval.types import GraphEdge, GraphNode


def _ordered_subsequence(whole: list[str], sub: list[str]) -> bool:
    it = iter(whole)
    return all(item in it for item in sub)


async def _drive(deps, query="resolve R1", persona="operations_lead", approve=True):
    """Run a query to completion, auto-approving any human gate; return the events."""
    registry = ApprovalRegistry()
    events: list[dict] = []
    async for event in run_agent(query, persona=persona, deps=deps, registry=registry):
        events.append(event)
        if event["type"] == "approval_required" and approve:
            registry.resolve(event["approval_id"], ApprovalDecision.APPROVE, approver="al")
    return events


# ── as_dict / harness_config are complete + typed ─────────────────────────────


def test_as_dict_lists_every_config_field():
    field_names = {f.name for f in dataclasses.fields(AgentConfig)}
    assert set(AgentConfig().as_dict()) == field_names


def test_as_dict_serializes_enum_to_str():
    d = AgentConfig().as_dict()
    assert d["gate_min_risk"] == RiskLevel.HIGH.value
    assert isinstance(d["gate_min_risk"], str)


def test_harness_config_covers_every_knob():
    field_names = {f.name for f in dataclasses.fields(AgentConfig)}
    keys = {k["key"] for k in harness_config()["knobs"]}
    assert keys == field_names


def test_harness_config_defaults_match_fresh_config():
    fresh = AgentConfig().as_dict()
    for knob in harness_config()["knobs"]:
        assert knob["default"] == fresh[knob["key"]]


def test_harness_config_reports_effective_values():
    cfg = AgentConfig(gate_min_risk=RiskLevel.MEDIUM, max_plan_iterations=5)
    hc = harness_config(cfg)
    assert hc["effective"]["gate_min_risk"] == "medium"
    assert hc["effective"]["max_plan_iterations"] == 5
    by_key = {k["key"]: k for k in hc["knobs"]}
    assert by_key["gate_min_risk"]["value"] == "medium"
    assert by_key["gate_min_risk"]["default"] == "high"
    assert by_key["max_plan_iterations"]["value"] == 5


def test_gate_min_risk_knob_declares_enum_allowed_values():
    by_key = {k["key"]: k for k in harness_config()["knobs"]}
    knob = by_key["gate_min_risk"]
    assert knob["type"] == "enum"
    assert knob["allowed"] == [r.value for r in RiskLevel]


def test_numeric_knobs_declare_minimums():
    by_key = {k["key"]: k for k in harness_config()["knobs"]}
    assert by_key["max_plan_iterations"]["minimum"] == 1
    assert by_key["agentic_retrieval_max_rounds"]["minimum"] == 1
    assert by_key["stream_chunk_words"]["minimum"] == 1


def test_approval_park_timeout_knob_is_nullable_float():
    by_key = {k["key"]: k for k in harness_config()["knobs"]}
    knob = by_key["approval_park_timeout"]
    assert knob["type"] == "float"
    assert knob["nullable"] is True
    assert knob["default"] is None


def test_every_knob_carries_a_doc_string():
    for knob in harness_config()["knobs"]:
        assert knob["doc"] and isinstance(knob["doc"], str)


# ── gate_min_risk genuinely changes whether the run pauses ────────────────────


@pytest.mark.asyncio
async def test_default_high_threshold_waves_medium_risk_through(make_deps):
    # Default gate_min_risk=HIGH; a MEDIUM-risk tool executes without a human gate.
    deps = make_deps(propose_tool=True, uncertain=False, high_risk=False)
    assert deps.config.gate_min_risk is RiskLevel.HIGH
    types = [e["type"] for e in await _drive(deps)]
    assert "approval_required" not in types
    assert "tool_call" in types


@pytest.mark.asyncio
async def test_lowering_gate_min_risk_pauses_the_same_action(make_deps):
    # Same MEDIUM-risk tool, but gate_min_risk lowered to MEDIUM → it now pauses.
    deps = make_deps(propose_tool=True, uncertain=False, high_risk=False)
    deps = dataclasses.replace(
        deps, config=dataclasses.replace(deps.config, gate_min_risk=RiskLevel.MEDIUM)
    )
    types = [e["type"] for e in await _drive(deps)]
    assert "approval_required" in types
    assert types.index("approval_required") < types.index("tool_call")


# ── self_repair_enabled genuinely adds/removes the reflect→plan loop ──────────


def _failing_run_tool(fail_first_n: int):
    """Return a ``run_tool`` fake whose first ``fail_first_n`` calls report failure."""
    calls = {"n": 0}

    class _Outcome:
        def __init__(self, ok: bool, summary: str) -> None:
            self.ok, self.summary = ok, summary

    async def run_tool(persona, name, args, *, actor, model, trace_id, approver):  # noqa: ANN001
        calls["n"] += 1
        ok = calls["n"] > fail_first_n
        return _Outcome(ok=ok, summary=f"attempt {calls['n']}")

    return run_tool, calls


@pytest.mark.asyncio
async def test_self_repair_enabled_replans_on_failure(make_deps):
    deps = make_deps(propose_tool=True, uncertain=False, high_risk=False)
    run_tool, calls = _failing_run_tool(fail_first_n=1)
    deps = dataclasses.replace(deps, run_tool=run_tool)
    assert deps.config.self_repair_enabled is True

    types = [e["type"] for e in await _drive(deps, approve=False)]
    assert types.count("tool_call") == 2  # failed → re-planned → second action
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_self_repair_disabled_skips_replan(make_deps):
    deps = make_deps(propose_tool=True, uncertain=False, high_risk=False)
    run_tool, calls = _failing_run_tool(fail_first_n=1)
    deps = dataclasses.replace(
        deps,
        run_tool=run_tool,
        config=dataclasses.replace(deps.config, self_repair_enabled=False),
    )

    events = await _drive(deps, approve=False)
    types = [e["type"] for e in events]
    assert types.count("tool_call") == 1  # no re-plan despite the failure
    assert calls["n"] == 1

    reflection = next(e for e in events if e["type"] == "reflection")
    assert reflection["will_retry"] is False
    assert "self-repair disabled" in reflection["reason"]


@pytest.mark.asyncio
async def test_max_plan_iterations_one_forces_single_pass(make_deps):
    deps = make_deps(propose_tool=True, uncertain=False, high_risk=False)
    run_tool, calls = _failing_run_tool(fail_first_n=99)
    deps = dataclasses.replace(
        deps,
        run_tool=run_tool,
        config=dataclasses.replace(deps.config, max_plan_iterations=1),
    )

    types = [e["type"] for e in await _drive(deps, approve=False)]
    assert types.count("tool_call") == 1
    assert calls["n"] == 1


# ── answer_cache_enabled genuinely skips (or runs) the planner ────────────────


class _FakeHit:
    def __init__(self, answer: str, similarity: float = 0.99) -> None:
        self.answer, self.similarity = answer, similarity


class _FakeAnswerCache:
    """A canned answer cache: always hits with ``hit_answer`` (or misses when None)."""

    def __init__(self, hit_answer: str | None) -> None:
        self.hit_answer = hit_answer
        self.reads: list[str] = []
        self.writes: list[str] = []

    async def get(self, embedding, *, scope):  # noqa: ANN001
        self.reads.append(scope)
        return _FakeHit(self.hit_answer) if self.hit_answer is not None else None

    async def set(self, *, query, embedding, answer, scope, sources):  # noqa: ANN001
        self.writes.append(answer)
        return None


def _cache_deps(make_deps, *, enabled: bool, hit_answer: str | None):
    """Build deps wired to a canned answer cache + a retrieve that yields a query_vec.

    Query-rewrite and agentic retrieval are pinned OFF so the fake ``RetrievalResult``
    (carrying ``query_vec``) reaches the plan node unchanged and the cache lookup fires.
    """

    async def retrieve(query: str, *, persona: str | None = None) -> RetrievalResult:
        return RetrievalResult(
            answer_context="ctx",
            sources=[Source(id="kb-1", text="Refund policy", score=0.9)],
            num_candidates=3,
            graph_delta=GraphDelta(
                nodes=[GraphNode(id="R1", label="R1", kind="request")],
                edges=[GraphEdge(source="R1", target="C1", relation="raised_by")],
            ),
            cache_hit=False,
            query_vec=[0.1, 0.2, 0.3],
        )

    deps = make_deps(propose_tool=False)
    return dataclasses.replace(
        deps,
        retrieve=retrieve,
        answer_cache=_FakeAnswerCache(hit_answer),
        config=dataclasses.replace(
            deps.config,
            answer_cache_enabled=enabled,
            query_rewrite_enabled=False,
            agentic_retrieval_enabled=False,
        ),
    )


@pytest.mark.asyncio
async def test_answer_cache_hit_skips_the_planner(make_deps):
    deps = _cache_deps(make_deps, enabled=True, hit_answer="Cached: refunds in 30 days.")
    events = await _drive(deps, query="what is the refund policy?")
    types = [e["type"] for e in events]

    assert deps.answer_cache.reads  # the cache WAS consulted
    assert "reasoning" not in types  # planner short-circuited → no plan text
    answer = "".join(e["text"] for e in events if e["type"] == "token")
    assert "Cached" in answer


@pytest.mark.asyncio
async def test_answer_cache_disabled_runs_the_planner(make_deps):
    deps = _cache_deps(make_deps, enabled=False, hit_answer="Cached: never returned.")
    events = await _drive(deps, query="what is the refund policy?")
    types = [e["type"] for e in events]

    assert deps.answer_cache.reads == []  # disabled → never consulted
    assert "reasoning" in types  # planner actually ran
    answer = "".join(e["text"] for e in events if e["type"] == "token")
    assert "Cached" not in answer
