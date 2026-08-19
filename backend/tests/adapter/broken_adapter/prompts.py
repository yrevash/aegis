"""A system prompt composed INSTEAD of the platform floor rather than over it."""

from __future__ import annotations

from typing import Any

PLATFORM_FLOOR = (
    "You operate inside a governed platform. State what you did and what you only "
    "proposed. Never claim a write that has not been approved."
)

SYSTEM_PROMPTS: dict[str, str] = {
    "desk_lead": "You run the desk. Triage what arrives and keep the record straight.",
    "requester": "You help one caller with their own records and nothing else.",
}


def render_platform_floor(persona: Any) -> str:
    """Return the half of the prompt no tenant may edit."""
    scope = persona.data_scope.get("kind", "unknown")
    return f"{PLATFORM_FLOOR}\nData scope: {scope}."


def render_system_prompt(persona: Any, *, extra_context: str | None = None) -> str:
    """THE BREAK: composes the task prompt *instead of* the floor, not over it."""
    base = SYSTEM_PROMPTS.get(persona.prompt_key, next(iter(SYSTEM_PROMPTS.values())))
    return f"{base}\n\n{extra_context}" if extra_context else base
