"""Typed results produced by the guardrail layer.

These models are deliberately tiny and dependency-free so they can be imported
anywhere (the agent graph, the API layer, tests) without pulling in the LLM
gateway or NeMo Guardrails. :class:`GuardResult` is the shared contract named in
``docs/AGENT_BRIEF.md`` and is what :func:`app.guardrails.check_input` /
:func:`app.guardrails.check_output` return.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.api.schemas import GuardVerdict


class PIIMatch(BaseModel):
    """One span of detected personally-identifiable information.

    Attributes:
        kind: The detector that fired (e.g. ``"EMAIL"``, ``"CREDIT_CARD"``).
        start: Inclusive start offset of the match in the scanned text.
        end: Exclusive end offset of the match in the scanned text.
        placeholder: The redaction token that replaces the span on the wire.
    """

    kind: str
    start: int
    end: int
    placeholder: str


class InjectionVerdict(BaseModel):
    """Structured output of the API injection/jailbreak classifier.

    Attributes:
        injection: ``True`` when the text is judged to be a prompt-injection or
            jailbreak attempt.
        reason: A short, demoable explanation for the verdict.
    """

    injection: bool
    reason: str = Field(description="Human-readable rationale, shown in the trace panel.")


class FormatCheck(BaseModel):
    """Result of a schema/format validation rail.

    Attributes:
        ok: ``True`` when the text satisfies the structural constraints.
        reason: Why it passed or failed (surfaced on ``block`` verdicts).
    """

    ok: bool
    reason: str


class GuardResult(BaseModel):
    """The verdict of an input or output rail (shared cross-module contract).

    Attributes:
        verdict: ``pass``, ``block`` or ``redact`` (:class:`GuardVerdict`).
        reason: Why the rails reached this verdict (safe to show a user/jury).
        text: The text after the rails ran — identical to the input on ``pass``,
            the redacted form on ``redact``, and the (possibly redacted) offending
            text on ``block``. Callers forward this, never the raw input.
    """

    verdict: GuardVerdict
    reason: str
    text: str
    layer: str | None = Field(
        default=None,
        description="Which rail produced the verdict: 'schema' | 'injection' | "
        "'content' | 'pii'.",
    )
    redactions: list[str] = Field(
        default_factory=list,
        description="Detector kinds redacted (kinds only — never raw PII values).",
    )
