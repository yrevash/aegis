"""Core: the model registry and the LiteLLM gateway.

Public surface (per the shared contract in ``docs/AGENT_BRIEF.md``):

- :func:`complete` / :func:`embed` — the async LLM gateway, routing by role.
- :class:`LLMResult`, :class:`ToolCallResult`, :class:`Usage` — result types.
- :class:`~app.core.models.ModelRole` and :func:`~app.core.models.model_for` —
  the locked role→model registry (re-exported for convenience).
"""

from __future__ import annotations

from .governance import (
    GovernanceContext,
    GovernanceLimits,
    get_governance_context,
    reset_governance_context,
    set_governance_context,
)
from .llm import (
    LLMResult,
    ToolCallResult,
    Usage,
    complete,
    embed,
)
from .models import ModelRole, model_for, routing_table

__all__ = [
    "GovernanceContext",
    "GovernanceLimits",
    "LLMResult",
    "ModelRole",
    "ToolCallResult",
    "Usage",
    "complete",
    "embed",
    "get_governance_context",
    "model_for",
    "reset_governance_context",
    "routing_table",
    "set_governance_context",
]
