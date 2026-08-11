"""Dependency-free guard result types (the shared guardrail contract).

Moved out of the legacy ``app.api.schemas`` / ``app.guardrails.models`` so any
component can import them without pulling in the API layer. Pydantic + stdlib
only. A ``FLAG`` verdict is added for non-blocking advisories (surfaced in the UI
but not enforced).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class GuardVerdict(StrEnum):
    """Outcome of an input or output rail."""

    PASS = "pass"
    BLOCK = "block"
    REDACT = "redact"
    FLAG = "flag"


class PIIMatch(BaseModel):
    """One span of detected personally-identifiable information."""

    kind: str
    start: int
    end: int
    placeholder: str


class InjectionVerdict(BaseModel):
    """Structured output of the prompt-injection / jailbreak classifier."""

    injection: bool
    reason: str = Field(description="Human-readable rationale, shown in the trace panel.")


class FormatCheck(BaseModel):
    """Result of a schema/format validation rail."""

    ok: bool
    reason: str


class GuardResult(BaseModel):
    """The verdict of an input or output rail (shared cross-module contract)."""

    verdict: GuardVerdict
    reason: str
    text: str
    layer: str | None = Field(
        default=None,
        description="Which rail produced the verdict, e.g. 'schema'|'injection'|'content'|'pii'.",
    )
    redactions: list[str] = Field(
        default_factory=list,
        description="Detector kinds redacted (kinds only — never raw PII values).",
    )
