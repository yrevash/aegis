"""Shared fixtures for the aegis.agent tests — fakes only, no host, no infrastructure.

Everything the graph touches (the LLM gateway, retrieval, guardrails, the action
tools, the supervisor roster) is faked here and injected through the
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
from aegis.retrieval.models import GraphDelta, RetrievalResult, Source
from aegis.retrieval.types import GraphEdge, GraphNode, RetrievalScope


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


def build_fake_deps(
    *,
    propose_tool: bool = True,
    block_input: bool = False,
    high_risk: bool = False,
    with_roster: bool = True,
):
    """Build an ``aegis.agent.AgentDeps`` wired entirely to canned fakes.

    Args:
        propose_tool: Whether the planner proposes an action tool call.
        block_input: Whether the input rail blocks the query.
        high_risk: Whether the tool is reported as HIGH risk (forces the gate).
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

    async def retrieve(query: str, *, scope: RetrievalScope) -> RetrievalResult:
        return RetrievalResult(
            answer_context="Spotlighted context about request R1.",
            # Metadata, because the real pipeline's sources carry it: ``_assemble``
            # copies ``Candidate.metadata`` straight onto every ``Source``, and every
            # recall arm writes ``file_path`` into it. A fake that returned a bare
            # ``Source`` could not tell whether the graph forwards provenance to the
            # wire, which is precisely the field that was missing. The second source
            # carries no path on purpose: a chunk whose provenance was never recorded is
            # a real shape, and it must render as an absence rather than a guess.
            sources=[
                Source(
                    id="kb-1",
                    text="Escalation policy",
                    score=0.9,
                    metadata={
                        "file_path": "escalation-policy.pdf",
                        "tenant_id": "t1",
                        "origins": ["vector", "bm25"],
                    },
                ),
                Source(id="kb-2", text="Unattributed passage", score=0.4),
            ],
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
                    "The request is overdue and matches the escalation policy. "
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

    kwargs = dict(
        complete=complete,
        retrieve=retrieve,
        check_input=check_input,
        check_output=check_output,
        tool_definitions_for=tool_definitions_for,
        run_tool=run_tool,
        tool_risk=tool_risk,
        render_system_prompt=render_system_prompt,
        config=AgentConfig(stream_chunk_words=4),
    )
    if with_roster:
        kwargs["agent_roster"] = fake_roster
    return AgentDeps(**kwargs)


@pytest.fixture
def make_deps():
    """Return the :func:`build_fake_deps` factory."""
    return build_fake_deps
