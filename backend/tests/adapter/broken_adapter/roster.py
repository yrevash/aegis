"""A third specialist, and a default role, that the graph has no node for."""

from __future__ import annotations

from dataclasses import dataclass, field

from aegis.agent import SubAgentSpec


@dataclass(frozen=True)
class RosterSpecialist:
    """One declared specialist: a role, a description, and how to reach it."""

    role: str
    description: str
    keywords: tuple[str, ...] = ()
    is_default: bool = False


@dataclass(frozen=True)
class AgentRoster:
    """The specialists the supervisor may route between."""

    specialists: tuple[RosterSpecialist, ...] = field(default_factory=tuple)

    @property
    def default_role(self) -> str:
        """The role an unmatched turn lands on (first declared when none is marked)."""
        for specialist in self.specialists:
            if specialist.is_default:
                return specialist.role
        return self.specialists[0].role if self.specialists else ""

    def roles(self) -> list[str]:
        """The declared roles, in declaration order."""
        return [s.role for s in self.specialists]

    def named(self) -> list[RosterSpecialist]:
        """The declared specialists."""
        return list(self.specialists)


_ROSTER = AgentRoster(
    specialists=(
        RosterSpecialist(role="qa", description="General question answering."),
        RosterSpecialist(role="memory", description="Answers from long-term memory."),
        # THE BREAK: two roles the graph has no handler node for, one of them default.
        RosterSpecialist(
            role="triage",
            description="Triages an incoming item before anything else runs.",
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
        # THE BREAK: a typo of 'record_note', which fails open into an empty tool set.
        tool_allowlist=frozenset({"record_notes"}),
    ),
)


def agent_roster() -> AgentRoster:
    """Return the declared roster."""
    return _ROSTER


def sub_agent_roster() -> tuple[SubAgentSpec, ...]:
    """Return the fan-out team."""
    return _SUB_AGENTS
