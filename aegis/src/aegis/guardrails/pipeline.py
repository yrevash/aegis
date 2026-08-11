"""Composed input/output guardrail pipeline that emits its work as events.

Mirrors the legacy layered order (schema → PII redaction → injection on input;
schema → content filter → PII on output), but is LLM-agnostic (an injected
:class:`ChatCompleter`) and streams :class:`StepStarted` → :class:`GuardrailEvent`
→ :class:`StepFinished` so the frontend can render each step live.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from aegis.core.events import AegisEvent, GuardrailEvent, SpanKind, StepFinished, StepStarted
from aegis.core.interfaces import ChatCompleter
from aegis.core.registry import register
from aegis.core.types import GuardResult, GuardVerdict
from aegis.guardrails import pii, schema
from aegis.guardrails.classifier import detect_injection

_MODULE_ID = "guardrails"


@register("guardrail", "default")
class Guardrails:
    """SOTA, LLM-agnostic input/output guardrail pipeline."""

    def __init__(self, *, completer: ChatCompleter | None = None) -> None:
        """Create the pipeline, optionally with a completer for the model injection layer.

        Args:
            completer: Optional chat completer for model-based injection detection.
                If None, only deterministic injection signatures are checked.
        """
        self._completer = completer

    async def check_input(self, text: str) -> GuardResult:
        """Run the full input rail (schema → PII redaction → injection).

        Args:
            text: The inbound user input text to screen.

        Returns:
            A :class:`GuardResult` with verdict and potentially redacted text.
        """
        fmt = schema.validate_input_format(text)
        if not fmt.ok:
            return GuardResult(
                verdict=GuardVerdict.BLOCK, reason=fmt.reason, text=text, layer="schema"
            )
        redacted, kinds = pii.redact(text)
        verdict = await detect_injection(redacted, completer=self._completer)
        if verdict.injection:
            return GuardResult(
                verdict=GuardVerdict.BLOCK,
                reason=f"Prompt injection blocked: {verdict.reason}",
                text=redacted,
                layer="injection",
            )
        if kinds:
            return GuardResult(
                verdict=GuardVerdict.REDACT,
                reason=f"Redacted PII on the inbound path: {', '.join(kinds)}.",
                text=redacted,
                layer="pii",
                redactions=kinds,
            )
        return GuardResult(
            verdict=GuardVerdict.PASS,
            reason="Input passed schema, PII, and injection rails.",
            text=text,
        )

    async def check_output(self, text: str) -> GuardResult:
        """Run the full output rail (schema → content filter → PII redaction).

        Args:
            text: The outbound model response text to screen.

        Returns:
            A :class:`GuardResult` with verdict and potentially redacted text.
        """
        fmt = schema.validate_output_format(text)
        if not fmt.ok:
            return GuardResult(
                verdict=GuardVerdict.BLOCK, reason=fmt.reason, text=text, layer="schema"
            )
        filtered = schema.content_filter(text)
        if not filtered.ok:
            return GuardResult(
                verdict=GuardVerdict.BLOCK, reason=filtered.reason, text=text, layer="content"
            )
        redacted, kinds = pii.redact(text)
        if kinds:
            return GuardResult(
                verdict=GuardVerdict.REDACT,
                reason=f"Redacted PII on the outbound path: {', '.join(kinds)}.",
                text=redacted,
                layer="pii",
                redactions=kinds,
            )
        return GuardResult(
            verdict=GuardVerdict.PASS,
            reason="Output passed schema, content-filter, and PII rails.",
            text=text,
        )

    async def stream_check_input(self, text: str) -> AsyncIterator[AegisEvent]:
        """Run the input rail, yielding start → verdict → finish events.

        Args:
            text: The inbound user input text to screen.

        Yields:
            :class:`AegisEvent` objects in order: :class:`StepStarted`,
            :class:`GuardrailEvent`, and :class:`StepFinished`.
        """
        step_id = uuid.uuid4().hex
        yield StepStarted(
            module_id=_MODULE_ID, step_id=step_id, name="guard_input", span_kind=SpanKind.GUARDRAIL
        )
        result = await self.check_input(text)
        yield GuardrailEvent(
            module_id=_MODULE_ID,
            step_id=step_id,
            verdict=result.verdict.value,
            rules=[result.layer] if result.layer else [],
            rationale=result.reason,
            redactions=result.redactions,
        )
        yield StepFinished(
            module_id=_MODULE_ID,
            step_id=step_id,
            name="guard_input",
            span_kind=SpanKind.GUARDRAIL,
            ok=result.verdict is not GuardVerdict.BLOCK,
        )
