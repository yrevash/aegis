"""Result types and the budget-refusal error — pydantic/stdlib only, no litellm.

Kept dependency-free (like :mod:`aegis.core.models`) so anything importing
``aegis.gateway`` for its types never pulls the heavy LiteLLM chain in.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

__all__ = ["BudgetExceededError", "LLMResult", "ToolCallResult", "Usage"]


class BudgetExceededError(Exception):
    """A per-tenant/user budget or rate cap was hit at the gateway chokepoint.

    Raised by ``complete``/``embed`` **before** the model call when the governed
    principal is over one of its caps, so the run degrades to "budget exceeded"
    instead of runaway cost. A host application's orchestrator catches this and
    surfaces a terminal "budget exceeded" event.

    Attributes:
        scope: Which level tripped — e.g. ``"tenant"`` or ``"user"``.
        scope_id: Id of the tripped scope.
        limit_type: Which cap tripped — e.g. ``"token_cap"`` | ``"usd_cap"`` |
            ``"rpm"`` | ``"tpm"``.
        limit: The configured cap value.
        used: Consumption at refusal time.
    """

    def __init__(
        self,
        *,
        scope: str,
        scope_id: int | None,
        limit_type: str,
        limit: float | None,
        used: float | None,
        message: str | None = None,
    ) -> None:
        """Capture the tripped cap so the wire event can be built from it."""
        self.scope = scope
        self.scope_id = scope_id
        self.limit_type = limit_type
        self.limit = limit
        self.used = used
        self.message = message or (
            f"{scope} {limit_type} exceeded: used {used} of {limit}."
        )
        super().__init__(self.message)


class ToolCallResult(BaseModel):
    """A single tool/function call the model asked to make."""

    id: str = Field(description="Provider-assigned tool-call id.")
    name: str = Field(description="Tool/function name.")
    args: dict[str, Any] = Field(
        default_factory=dict, description="Parsed JSON arguments."
    )


class Usage(BaseModel):
    """Token accounting and cost for one model call."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


class LLMResult(BaseModel):
    """The normalised result of a ``complete`` call."""

    content: str = Field(default="", description="Assistant text (may be empty).")
    tool_calls: list[ToolCallResult] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    model: str = Field(default="", description="Deployment id that responded.")
