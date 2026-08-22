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

    ``REJECTED`` is the outcome that used to have no name. A run whose high-risk
    action a human **refused** still reaches ``generate`` — the graph answers saying
    the action was not authorised, which is right — and so used to finish
    ``COMPLETED``, indistinguishable on status alone from a run that was approved and
    did the work. The refusal survived only in ``approvals.status`` and in an
    unchanged ``tool_call_count``, neither of which is on the run header a console
    lists runs from. A human's "no" is a terminal outcome of the run, not a footnote
    on another table.

    It is not ``BLOCKED``: that is a *guardrail* stopping a run, a machine decision
    about content. ``REJECTED`` is a person declining an action. Collapsing them would
    make "how often did our rails fire?" and "how often did a human say no?" the same
    number, and they are the two figures a governance dashboard exists to keep apart.
    """

    COMPLETED = "completed"
    BLOCKED = "blocked"  # a guardrail stopped the run
    AWAITING_APPROVAL = "awaiting_approval"
    #: A human refused the run's gated action. Terminal: the run answered, the action
    #: did not run, and ``tool_call_count`` proves it.
    REJECTED = "rejected"
    ERROR = "error"


class GuardStage(StrEnum):
    """Which rail stage produced a verdict.

    ``INPUT`` and ``OUTPUT`` are the two ends of a turn. ``TOOL_RESULT`` is the
    third, and it is the one that used to be missing: a tool pulls arbitrary
    third-party content (a web search result, a scraped page, a record from a
    system nobody here controls) straight into an agent's context, where it is read
    by the model as instructions-adjacent text. Screening the user and screening
    the answer leaves that whole surface unguarded, which is OWASP LLM01 exactly.
    """

    INPUT = "input"
    OUTPUT = "output"
    #: A tool's output, screened **before** it is allowed into any agent's context.
    TOOL_RESULT = "tool_result"


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
    """Structured output of the prompt-injection / jailbreak classifier.

    ``injection`` and ``checked`` are two different facts and conflating them is what
    made a dead gateway tell a judge their question looked like an attack. The screen
    fails closed either way — ``injection=True`` blocks — but only ``checked=True``
    means a screen actually reached a verdict *about the text*. ``checked=False`` is
    "we could not look", and every surface that renders a refusal must say so.
    """

    injection: bool
    reason: str = Field(description="Human-readable rationale, shown in the trace panel.")
    checked: bool = Field(
        default=True,
        description=(
            "Whether the screen actually ran and judged the text. False means the "
            "classifier was unreachable or answered unintelligibly and the request was "
            "refused unchecked — a statement about this deployment, never about the "
            "input."
        ),
    )


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
