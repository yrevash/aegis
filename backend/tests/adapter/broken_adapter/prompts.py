"""A system prompt composed INSTEAD of the platform floor rather than over it."""

from __future__ import annotations

from app.adapter.prompts import SYSTEM_PROMPTS, render_platform_floor  # noqa: F401


def render_system_prompt(persona, *, extra_context: str | None = None) -> str:
    base = SYSTEM_PROMPTS.get(persona.prompt_key, next(iter(SYSTEM_PROMPTS.values())))
    return f"{base}\n\n{extra_context}" if extra_context else base
