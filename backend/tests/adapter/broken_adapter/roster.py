"""A third specialist, and a default role, that the graph has no node for."""

from __future__ import annotations

from aegis.agent import SubAgentSpec

from app.adapter.roster import AgentRoster, RosterSpecialist

_ROSTER = AgentRoster(
    specialists=(
        RosterSpecialist(role="qa", description="General question answering."),
        RosterSpecialist(role="memory", description="Answers from long-term memory."),
        RosterSpecialist(
            role="triage",
            description="Triages an incoming request before anything else runs.",
            keywords=("triage", "urgent"),
        ),
        RosterSpecialist(
            role="answer",
            description="The catch-all this domain wants everything else to land on.",
            is_default=True,
        ),
    )
)

_SUB_AGENTS = (
    SubAgentSpec(
        agent_id="data",
        role="data",
        label="Data agent",
        system_prompt="Read the records and report.",
        tool_allowlist=frozenset({"update_request_state"}),
    ),
)


def agent_roster() -> AgentRoster:
    return _ROSTER


def sub_agent_roster() -> tuple[SubAgentSpec, ...]:
    return _SUB_AGENTS
