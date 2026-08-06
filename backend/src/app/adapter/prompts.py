"""System prompts — the agent's voice and guardrails per persona.

This is **piece 5b of 5** of the adapter (paired with :mod:`app.adapter.personas`).
It holds the base system prompt for each persona and a small renderer that folds
in the persona's live **data scope** and **tool allowlist**, so the instructions
the model receives always match what it is actually permitted to do.

Keeping the prompt text here (not scattered through the core) means re-voicing the
agent for a new domain is a localised edit. The renderer never invents tools — it
reads them from :data:`app.adapter.tools.TOOL_REGISTRY`, filtered by the persona's
allowlist — so prompt, schema and enforcement can never drift apart.
"""

from __future__ import annotations

from app.adapter.personas import Persona, ScopeKind
from app.adapter.tools import TOOL_REGISTRY

SYSTEM_PROMPTS: dict[str, str] = {
    "operations_lead": (
        "You are an assistant to a customer-support Operations Lead. You help "
        "triage, assign, and resolve service requests across the whole desk. Be "
        "concise and decisive. Ground every claim in retrieved records or "
        "knowledge documents, and cite the request or document id you used. When "
        "you take an action, state what you changed and why. Never fabricate "
        "request ids, customer data, or resolution figures."
    ),
    "client": (
        "You are a friendly support assistant helping a customer with their own "
        "service requests. You may only view and discuss requests that belong to "
        "this customer. Answer plainly, avoid internal jargon, and never reveal "
        "other customers' data, internal agent names, or system internals. If a "
        "request is outside this customer's scope, say you cannot access it."
    ),
}
"""Persona id → its base system prompt."""


def _scope_clause(persona: Persona) -> str:
    """Describe the persona's data visibility in one sentence."""
    if persona.data_scope.kind is ScopeKind.ALL:
        return "Data scope: you may access every request on the desk."
    field = persona.data_scope.subject_field or "owner"
    return (
        "Data scope: you may only access records whose "
        f"'{field}' matches the current authenticated subject."
    )


def _tools_clause(persona: Persona) -> str:
    """List the tools the persona may call, or state it has none."""
    names = sorted(persona.tool_names)
    if not names:
        return "Tools: you have no action tools; you may only read and answer."
    lines = [
        f"- {name}: {TOOL_REGISTRY[name].description} (risk={TOOL_REGISTRY[name].risk.value})"
        for name in names
        if name in TOOL_REGISTRY
    ]
    return "Tools you may call:\n" + "\n".join(lines)


def render_system_prompt(persona: Persona, *, extra_context: str | None = None) -> str:
    """Build the full system prompt for a persona.

    Combines the base prompt with the persona's live data scope and tool
    allowlist, plus any run-time context (e.g. a dataset summary).

    Args:
        persona: The persona to render for.
        extra_context: Optional additional context appended verbatim.

    Returns:
        The assembled system prompt string.
    """
    base = SYSTEM_PROMPTS.get(persona.prompt_key, SYSTEM_PROMPTS["operations_lead"])
    parts = [base, _scope_clause(persona), _tools_clause(persona)]
    if extra_context:
        parts.append(extra_context.strip())
    return "\n\n".join(parts)
