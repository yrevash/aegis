"""Structural interfaces (Protocols) the core depends on — impls are swappable.

The core never imports a concrete implementation; components satisfy these
Protocols structurally. :class:`ChatCompleter` is how guardrails stays
LLM-agnostic: callers inject any async completion function (LiteLLM, OpenAI, a
local stub) rather than the package hard-wiring a provider.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from aegis.core.types import GuardResult


@runtime_checkable
class ChatCompleter(Protocol):
    """An async chat-completion callable returning the assistant's text."""

    async def __call__(
        self, messages: list[dict], *, response_format: dict | None = None
    ) -> str:
        """Return the assistant's text for ``messages`` (optionally JSON-formatted)."""
        ...


@runtime_checkable
class Guardrail(Protocol):
    """An input/output guardrail producing a :class:`GuardResult`."""

    async def check_input(self, text: str) -> GuardResult:
        """Screen inbound text before it reaches the model."""
        ...

    async def check_output(self, text: str) -> GuardResult:
        """Screen outbound text before it reaches the user."""
        ...
