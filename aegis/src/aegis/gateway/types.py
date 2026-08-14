"""Result types and the budget-refusal error — pydantic/stdlib only, no litellm.

Kept dependency-free (like :mod:`aegis.core.models`) so anything importing
``aegis.gateway`` for its types never pulls the heavy LiteLLM chain in.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "BudgetExceededError",
    "CostSource",
    "LLMResult",
    "ToolCallResult",
    "TranscriptionResult",
    "TranscriptionSegment",
    "Usage",
]


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


class CostSource(StrEnum):
    """Where a call's ``cost_usd`` came from — so a $0 is never ambiguous.

    A cost that cannot be determined must be *visible*, not silently zero:
    ``UNPRICED`` says "this call consumed billable work we could not price",
    which is a different statement from ``PROVIDER``/``ESTIMATED`` $0.
    """

    PROVIDER = "provider"  # the provider's own cost map priced the call
    ESTIMATED = "estimated"  # priced from measured units × the configured rate
    UNPRICED = "unpriced"  # billable units consumed but no rate/unit count known


class Usage(BaseModel):
    """Billable accounting for one model call — tokens *and* non-token units.

    Not every model bills per token: Whisper bills per minute of audio and an
    image-billed deployment bills per image. Both are carried here so a
    non-chat call ledgers real spend instead of ``prompt_tokens=0`` → ``$0.00``.
    Every field defaults to zero, so a token-only caller is unaffected.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    audio_seconds: float = Field(
        default=0.0, description="Seconds of audio billed (voice/transcription calls)."
    )
    images: int = Field(
        default=0, description="Images billed or sent as input (vision calls)."
    )
    cost_source: CostSource = Field(
        default=CostSource.PROVIDER,
        description="Provenance of ``cost_usd`` — never leave a $0 ambiguous.",
    )


class LLMResult(BaseModel):
    """The normalised result of a ``complete`` call."""

    content: str = Field(default="", description="Assistant text (may be empty).")
    tool_calls: list[ToolCallResult] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    model: str = Field(default="", description="Deployment id that responded.")


class TranscriptionSegment(BaseModel):
    """One provider-reported segment of a transcript (verbose responses only)."""

    id: int | None = Field(default=None, description="Provider segment index.")
    start: float | None = Field(default=None, description="Segment start, seconds.")
    end: float | None = Field(default=None, description="Segment end, seconds.")
    text: str = Field(default="", description="Transcribed text for the segment.")


class TranscriptionResult(BaseModel):
    """The normalised result of a ``transcribe`` call.

    ``segments`` / ``language`` / ``duration_seconds`` are populated only when the
    provider reports them (a ``verbose_json`` response); they stay empty/``None``
    rather than being invented.
    """

    text: str = Field(default="", description="The full transcript.")
    language: str | None = Field(
        default=None, description="Detected language, when the provider reports one."
    )
    duration_seconds: float | None = Field(
        default=None, description="Audio duration, when known — the billing unit."
    )
    segments: list[TranscriptionSegment] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    model: str = Field(default="", description="Deployment id that responded.")
