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


class RiskLevel(StrEnum):
    """Coarse risk tier for an action; drives the human-gate threshold.

    A shared cross-module contract (like :class:`GuardVerdict`): the approvals ORM
    row and the agent's human-gate logic both key on it, so it lives in
    ``aegis.core.types`` (pydantic/stdlib only) rather than any one consumer.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RunStatus(StrEnum):
    """Terminal status of a query run.

    Shared cross-module contract driven by :mod:`aegis.agent` (the orchestrator's
    terminal event) and re-exported by the host's API schema layer. Lives here so
    the agent core never imports the host's ``app.api.schemas``.
    """

    COMPLETED = "completed"
    BLOCKED = "blocked"  # a guardrail stopped the run
    AWAITING_APPROVAL = "awaiting_approval"
    ERROR = "error"


class GuardStage(StrEnum):
    """Which rail stage produced a verdict — input or output."""

    INPUT = "input"
    OUTPUT = "output"


class ApprovalDecision(StrEnum):
    """A human's decision at the approval gate."""

    APPROVE = "approve"
    REJECT = "reject"


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
