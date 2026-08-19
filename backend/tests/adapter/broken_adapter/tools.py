"""A tool registered before anyone decided its risk tier, and an allowlist with typos."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aegis.core.types import RiskLevel


@dataclass(frozen=True)
class FixtureToolSpec:
    """A tool spec: everything a real one carries, and ``risk`` may be missing."""

    name: str
    description: str
    risk: Any = None

    def definition(self) -> dict[str, Any]:
        """Return the OpenAI/MCP function definition for this tool."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", "properties": {}},
            },
        }


TOOL_REGISTRY: dict[str, FixtureToolSpec] = {
    "record_note": FixtureToolSpec(
        name="record_note",
        description="Append a note to a work item's timeline.",
        risk=RiskLevel.LOW,
    ),
    "reassign_item": FixtureToolSpec(
        name="reassign_item",
        description="Move a work item to a different owner.",
        risk=RiskLevel.MEDIUM,
    ),
    "close_item": FixtureToolSpec(
        name="close_item",
        description="Close a work item.",
        risk=RiskLevel.HIGH,
    ),
    # THE BREAK: registered before anyone decided its tier, so the gate has no input.
    "escalate_to_regulator": FixtureToolSpec(
        name="escalate_to_regulator",
        description="File a formal escalation with the regulator.",
    ),
}

ALLOWLIST: dict[str, frozenset[str]] = {
    # THE BREAK: 'supervisor' is not a declared persona, so it silently gets no tools.
    "supervisor": frozenset({"record_note"}),
    "desk_lead": frozenset({"close_item", "escalate_to_regulator"}),
}


def is_allowed(persona_id: str, tool_name: str) -> bool:
    """Return whether ``persona_id`` may call ``tool_name``."""
    return tool_name in ALLOWLIST.get(persona_id, frozenset())


def tools_for(persona_id: str) -> list[FixtureToolSpec]:
    """Return the tool specs ``persona_id`` may call."""
    return [TOOL_REGISTRY[n] for n in sorted(ALLOWLIST.get(persona_id, frozenset()))]


def tool_definitions_for(persona_id: str) -> list[dict[str, Any]]:
    """Return the model-facing tool definitions ``persona_id`` may call."""
    return [t.definition() for t in tools_for(persona_id)]


async def run_tool(
    persona_id: str, name: str, arguments: dict[str, Any], ctx: Any
) -> dict[str, Any]:
    """Execute a tool. Never called by the conformance suite — it reads, it does not act."""
    raise NotImplementedError("the broken adapter is read-only fixture data")
