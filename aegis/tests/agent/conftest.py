"""Shared fixtures for the aegis.agent tests — fakes only, no host, no infrastructure.

Everything the graph touches (the LLM gateway, retrieval, guardrails, the ML spine,
the action tools, the supervisor roster) is faked here and injected through the
standalone ``aegis.agent.AgentDeps`` — plus the injected orchestrator seams (a plain
dict-stamp event validator, an ``InMemorySaver`` checkpointer, and null durable
approvals/trace-eval sinks). This is the proof the seam works: the whole vertical slice
runs with no network, no keys, no DB, and no host application at all.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import pytest

from aegis.core.types import GuardResult, GuardVerdict, RiskLevel
from aegis.gateway.types import LLMResult, ToolCallResult, Usage
from aegis.ml.types import MLExplainResponse, ShapFeature
from aegis.retrieval.models import GraphDelta, RetrievalResult, Source
from aegis.retrieval.types import GraphEdge, GraphNode


class _Outcome:
    """Minimal stand-in for a host adapter ``ToolActionResult``."""

    def __init__(self, ok: bool = True, summary: str = "Status open -> resolved") -> None:
        self.ok = ok
        self.summary = summary


# ── A fake supervisor roster (what the host adapter would declare) ────────────


@dataclass(frozen=True)
class _Specialist:
    role: str
    description: str
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class _Roster:
    """A qa + memory roster mirroring the read shape the router/graph consume."""

    @property
    def default_role(self) -> str:
        return "qa"

    @property
    def specialists(self) -> tuple[_Specialist, ...]:
        return (
            _Specialist("qa", "General question answering.", ()),
            _Specialist(
                "memory",
                "Answers what is known/remembered about the user.",
                ("about me", "know about me", "remember about me", "what do you know"),
            ),
        )

    def roles(self) -> list[str]:
        return [s.role for s in self.specialists]

    def named(self) -> list[_Specialist]:
        # Only specialists with keyword hints are "named" for the classifier.
        return [s for s in self.specialists if s.keywords]


def fake_roster() -> _Roster:
    """Return the qa+memory fake roster (the injected ``agent_roster`` hook)."""
    return _Roster()


def _describe_prediction(resp: MLExplainResponse) -> str:
    """Fake decision-support framing of a prediction (host-adapter role)."""
    return (
        f"ML decision-support: predicts {resp.prediction} "
        f"(confidence {resp.conformal_confidence})."
    )


def build_fake_deps(
    *,
    propose_tool: bool = True,
    uncertain: bool = True,
    block_input: bool = False,
    high_risk: bool = False,
    degenerate: bool = False,
    with_roster: bool = True,
):
    """Build an ``aegis.agent.AgentDeps`` wired entirely to canned fakes.

    Args:
        propose_tool: Whether the planner proposes an action tool call.
        uncertain: Whether the ML fake returns a wide interval.
        block_input: Whether the input rail blocks the query.
        high_risk: Whether the tool is reported as HIGH risk (forces the gate).
        degenerate: Whether the ML fake returns a degenerate prediction.
        with_roster: Whether to inject the qa+memory fake roster (else the core
            qa-only fallback is used).
    """
    from aegis.agent import AgentConfig, AgentDeps

    async def check_input(text: str) -> GuardResult:
        if block_input:
            return GuardResult(
                verdict=GuardVerdict.BLOCK, reason="blocked by policy", text=text
            )
        return GuardResult(verdict=GuardVerdict.PASS, reason="clean", text=text)

    async def check_output(
        text: str, contexts: list[str] | None = None
    ) -> GuardResult:  # noqa: ARG001 - contexts accepted for the grounding-aware signature
        return GuardResult(verdict=GuardVerdict.PASS, reason="clean", text=text)

    async def retrieve(query: str, *, persona: str | None = None) -> RetrievalResult:
        return RetrievalResult(
            answer_context="Spotlighted context about request R1.",
            sources=[Source(id="kb-1", text="Refund policy", score=0.9)],
            num_candidates=5,
            graph_delta=GraphDelta(
                nodes=[GraphNode(id="R1", label="Request R1", kind="request")],
                edges=[GraphEdge(source="R1", target="C1", relation="raised_by")],
            ),
            cache_hit=False,
        )

    async def complete(role, messages, *, tools=None, temperature=0.0, response_format=None):  # noqa: ANN001
        # Faithful doubles for the two retrieval-intelligence prompts, so the REAL
        # rewrite + sufficiency code paths execute but resolve to a stable, single-round
        # unchanged-query outcome.
        system = messages[0]["content"] if messages else ""
        if "standalone search query" in system or "rewrite a user's latest turn" in system:
            user = messages[-1]["content"] if messages else ""
            match = re.search(r"LATEST TURN: (.*?)\n\n", user, re.DOTALL)
            user_query = match.group(1) if match else user
            return LLMResult(
                content=json.dumps(
                    {"rewritten": user_query, "reason": "no rewrite needed"}
                ),
                tool_calls=[],
                usage=Usage(prompt_tokens=3, completion_tokens=2, cost_usd=0.0001),
                model="fake-cheap",
            )
        if "retrieval sufficiency judge" in system:
            return LLMResult(
                content=json.dumps(
                    {
                        "sufficient": True,
                        "reason": "context sufficient",
                        "followup_query": None,
                    }
                ),
                tool_calls=[],
                usage=Usage(prompt_tokens=3, completion_tokens=2, cost_usd=0.0001),
                model="fake-cheap",
            )
        if tools and propose_tool:
            return LLMResult(
                content=(
                    "The request is overdue and matches the refund policy. "
                    "I will update its status to resolved."
                ),
                tool_calls=[
                    ToolCallResult(
                        id="call-1",
                        name="update_request_status",
                        args={"request_id": "R1", "status": "resolved"},
                    )
                ],
                usage=Usage(prompt_tokens=12, completion_tokens=4, cost_usd=0.0009),
                model="fake-generation",
            )
        return LLMResult(
            content="Request R1 has been resolved and the customer notified.",
            tool_calls=[],
            usage=Usage(prompt_tokens=9, completion_tokens=7, cost_usd=0.0006),
            model="fake-generation",
        )

    def predict_explain(features: dict) -> MLExplainResponse:
        if degenerate:
            interval, confidence = (0.0, 240.0), 0.3
        elif uncertain:
            interval, confidence = (2.0, 48.0), 0.6
        else:
            interval, confidence = (11.0, 13.0), 0.95
        width = interval[1] - interval[0]
        return MLExplainResponse(
            prediction=12.0,
            conformal_interval=interval,
            conformal_confidence=confidence,
            interval_width=width,
            shap_attribution=[
                ShapFeature(feature="priority", value=1.0, contribution=0.42),
                ShapFeature(feature="queue_depth_at_open", value=8.0, contribution=-0.20),
            ],
        )

    def tool_definitions_for(persona: str) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "update_request_status",
                    "description": "Change a request's status.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

    def tool_risk(name: str) -> RiskLevel:
        return RiskLevel.HIGH if high_risk else RiskLevel.MEDIUM

    async def run_tool(persona, name, args, *, actor, model, trace_id, approver):  # noqa: ANN001
        return _Outcome()

    def render_system_prompt(persona: str, extra_context: str | None = None) -> str:
        base = "You are a helpful, grounded support assistant."
        return f"{base}\n\n{extra_context}" if extra_context else base

    def features_for(query: str, persona: str | None) -> dict:
        return {"priority": 1, "queue_depth_at_open": 8}

    kwargs = dict(
        complete=complete,
        retrieve=retrieve,
        check_input=check_input,
        check_output=check_output,
        predict_explain=predict_explain,
        tool_definitions_for=tool_definitions_for,
        run_tool=run_tool,
        tool_risk=tool_risk,
        render_system_prompt=render_system_prompt,
        features_for=features_for,
        describe_prediction=_describe_prediction,
        config=AgentConfig(stream_chunk_words=4),
    )
    if with_roster:
        kwargs["agent_roster"] = fake_roster
    return AgentDeps(**kwargs)


@pytest.fixture
def make_deps():
    """Return the :func:`build_fake_deps` factory."""
    return build_fake_deps
