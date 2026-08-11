"""The canonical show-your-work event contract every Aegis module emits.

Events are a discriminated union keyed on ``type`` (start/delta/end discipline),
each stamped with an OpenInference :class:`SpanKind` so the same stream renders
live in the UI and exports as OTel/OpenInference spans. This file is the single
source of truth the frontend mirrors.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class SpanKind(StrEnum):
    """OpenInference span kinds (a module stamps the kind it is acting as)."""

    LLM = "LLM"
    EMBEDDING = "EMBEDDING"
    RETRIEVER = "RETRIEVER"
    RERANKER = "RERANKER"
    TOOL = "TOOL"
    GUARDRAIL = "GUARDRAIL"
    AGENT = "AGENT"
    CHAIN = "CHAIN"
    EVALUATOR = "EVALUATOR"


class _BaseEvent(BaseModel):
    """Fields common to every Aegis event."""

    module_id: str = Field(description="Emitting module, e.g. 'guardrails'.")
    step_id: str = Field(description="Correlates start/data/finish for one step.")
    span_kind: SpanKind = SpanKind.CHAIN
    trace_id: str | None = None
    parent_span_id: str | None = None


class StepStarted(_BaseEvent):
    """A module step began."""

    type: Literal["step.started"] = "step.started"
    name: str = Field(description="Human label for the step, e.g. 'guard_input'.")


class StepFinished(_BaseEvent):
    """A module step completed."""

    type: Literal["step.finished"] = "step.finished"
    name: str
    ok: bool = True
    duration_ms: float | None = None


class GuardrailEvent(_BaseEvent):
    """A guardrail verdict payload (renders as a verdict card)."""

    type: Literal["data-guardrail"] = "data-guardrail"
    span_kind: SpanKind = SpanKind.GUARDRAIL
    verdict: str
    rules: list[str] = Field(default_factory=list)
    score: float | None = None
    rationale: str = ""
    redactions: list[str] = Field(default_factory=list)


AegisEvent = StepStarted | StepFinished | GuardrailEvent
"""Union of every event an Aegis module may emit (extended per component)."""
