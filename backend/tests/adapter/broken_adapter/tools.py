"""A tool registered before anyone decided its risk tier, and an allowlist with typos."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.adapter.tools import TOOL_REGISTRY as _REFERENCE_REGISTRY
from app.adapter.tools import run_tool  # noqa: F401 - re-exported, as a real adapter does


@dataclass(frozen=True)
class UntieredToolSpec:
    """A tool spec whose ``risk`` was never filled in."""

    name: str
    description: str
    risk: Any = None

    def definition(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name}}


TOOL_REGISTRY = dict(_REFERENCE_REGISTRY) | {
    "escalate_to_regulator": UntieredToolSpec(
        name="escalate_to_regulator",
        description="File a formal escalation with the regulator.",
    )
}

ALLOWLIST = {
    "operations_lead": frozenset({"update_request_status", "escalate_to_regulator"}),
    "supervisor": frozenset({"add_case_note"}),
}


def is_allowed(persona_id: str, tool_name: str) -> bool:
    return tool_name in ALLOWLIST.get(persona_id, frozenset())


def tools_for(persona_id: str) -> list[Any]:
    return [TOOL_REGISTRY[n] for n in sorted(ALLOWLIST.get(persona_id, frozenset()))]


def tool_definitions_for(persona_id: str) -> list[dict[str, Any]]:
    return [t.definition() for t in tools_for(persona_id)]
