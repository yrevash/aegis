"""Composed input/output guardrail pipeline that emits its work as events.

Mirrors the legacy layered order (schema → PII redaction → injection on input;
schema → content filter → PII on output), but is LLM-agnostic (an injected
:class:`ChatCompleter`) and streams :class:`StepStarted` → :class:`GuardrailEvent`
→ :class:`StepFinished` so the frontend can render each step live.
"""

from __future__ import annotations

import inspect
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING

from aegis.core.events import AegisEvent, GuardrailEvent, SpanKind, StepFinished, StepStarted
from aegis.core.interfaces import ChatCompleter
from aegis.core.registry import register
from aegis.core.types import GuardResult, GuardVerdict
from aegis.guardrails import pii, schema
from aegis.guardrails.classifier import detect_injection
from aegis.guardrails.content_safety import screen_content
from aegis.guardrails.grounding import check_grounding
from aegis.guardrails.topical import screen_topic

if TYPE_CHECKING:
    from aegis.core.stream import AegisEmitter

_MODULE_ID = "guardrails"

#: A custom rail: given the (already PII-redacted) text, return a GuardResult to
#: block/redact/flag, or ``None`` to abstain. Sync or async. Give it a distinct
#: ``layer`` and it streams to the console like any built-in rail — this is the
#: extension seam for domain-specific policies ("block competitor names",
#: "enforce a JSON contract", "no medical advice") without forking the pipeline.
Rail = Callable[[str], "GuardResult | None | Awaitable[GuardResult | None]"]


@register("guardrail", "default")
class Guardrails:
    """SOTA, LLM-agnostic input/output guardrail pipeline.

    Extensible: pass ``input_rails`` / ``output_rails`` to bolt domain-specific
    rails onto the built-in defense-in-depth chain. Custom rails run after the
    built-ins and any non-PASS verdict they return short-circuits the chain and
    streams to the frontend with its own ``layer`` label.
    """

    def __init__(
        self,
        *,
        completer: ChatCompleter | None = None,
        input_rails: list[Rail] | None = None,
        output_rails: list[Rail] | None = None,
        allowed_topics: str | list[str] | None = None,
        topical_block: bool = False,
        ground_answers: bool = False,
        grounding_block: bool = False,
    ) -> None:
        """Create the pipeline.

        Args:
            completer: Optional chat completer for the model-based injection and
                content-safety self-checks. If None, only deterministic layers run.
            input_rails: Optional custom input rails, run after the built-ins.
            output_rails: Optional custom output rails, run after the built-ins.
            allowed_topics: Optional description/list of the permitted business
                domain. When set, the topical rail screens each inbound query; an
                off-topic query is an advisory FLAG (does not stop the request)
                unless ``topical_block`` is set. When None/empty the rail is off.
            topical_block: Make the topical rail a hard BLOCK instead of advisory.
            ground_answers: Enable the output grounding self-check. It runs only
                when ``check_output`` is given retrieval ``contexts``; an
                ungrounded answer is an advisory FLAG unless ``grounding_block``.
            grounding_block: Make the grounding rail a hard BLOCK instead of advisory.
        """
        self._completer = completer
        self._input_rails = list(input_rails or [])
        self._output_rails = list(output_rails or [])
        self._allowed_topics = allowed_topics
        self._topical_block = topical_block
        self._ground_answers = ground_answers
        self._grounding_block = grounding_block

    @staticmethod
    async def _run_custom(text: str, rails: list[Rail]) -> GuardResult | None:
        """Run custom rails in order; return the first non-PASS verdict, else None."""
        for rail in rails:
            result = rail(text)
            if inspect.isawaitable(result):
                result = await result
            if result is not None and result.verdict is not GuardVerdict.PASS:
                return result
        return None

    async def _screen_topical(self, text: str) -> GuardResult | None:
        """Run the topical rail; return a BLOCK/FLAG GuardResult, or None when off.

        Advisory by default (an off-topic query is a non-blocking FLAG); a hard
        BLOCK when ``topical_block`` is set. Returns None when unconfigured/on-topic.
        """
        if not self._allowed_topics:
            return None
        verdict = await screen_topic(
            text,
            allowed_topics=self._allowed_topics,
            completer=self._completer,
            block=self._topical_block,
        )
        if verdict.on_topic:
            return None
        outcome = GuardVerdict.BLOCK if self._topical_block else GuardVerdict.FLAG
        prefix = "Off-topic query blocked" if self._topical_block else "Off-topic query flagged"
        return GuardResult(
            verdict=outcome,
            reason=f"{prefix}: {verdict.reason}",
            text=text,
            layer="topical",
        )

    async def _screen_input(self, text: str) -> tuple[GuardResult, list[GuardResult]]:
        """Run the input rail, returning ``(primary, advisories)``.

        ``primary`` is the blocking / redaction / pass verdict (never FLAG); a
        BLOCK short-circuits the chain. ``advisories`` collects non-blocking FLAGs
        (e.g. off-topic) so they can be streamed without stopping the request.
        """
        fmt = schema.validate_input_format(text)
        if not fmt.ok:
            return (
                GuardResult(
                    verdict=GuardVerdict.BLOCK, reason=fmt.reason, text=text, layer="schema"
                ),
                [],
            )
        redacted, kinds = pii.redact(text)
        verdict = await detect_injection(redacted, completer=self._completer)
        if verdict.injection:
            return (
                GuardResult(
                    verdict=GuardVerdict.BLOCK,
                    reason=f"Prompt injection blocked: {verdict.reason}",
                    text=redacted,
                    layer="injection",
                ),
                [],
            )
        safety = await screen_content(redacted, completer=self._completer)
        if safety.unsafe:
            reason = f"Unsafe content blocked ({safety.label() or 'hazard'}): {safety.reason}"
            return (
                GuardResult(
                    verdict=GuardVerdict.BLOCK,
                    reason=reason,
                    text=redacted,
                    layer="content_safety",
                ),
                [],
            )
        advisories: list[GuardResult] = []
        topical = await self._screen_topical(redacted)
        if topical is not None:
            if topical.verdict is GuardVerdict.BLOCK:
                return topical, []
            advisories.append(topical)
        custom = await self._run_custom(redacted, self._input_rails)
        if custom is not None:
            return custom, advisories
        if kinds:
            return (
                GuardResult(
                    verdict=GuardVerdict.REDACT,
                    reason=f"Redacted PII on the inbound path: {', '.join(kinds)}.",
                    text=redacted,
                    layer="pii",
                    redactions=kinds,
                ),
                advisories,
            )
        return (
            GuardResult(
                verdict=GuardVerdict.PASS,
                reason="Input passed schema, PII, injection, and content-safety rails.",
                text=text,
            ),
            advisories,
        )

    async def check_input(self, text: str) -> GuardResult:
        """Run the full input rail (schema → PII → injection → content → topical).

        Args:
            text: The inbound user input text to screen.

        Returns:
            A :class:`GuardResult` with verdict and potentially redacted text. When
            the core rails pass but an advisory (e.g. off-topic) fired, the
            non-blocking FLAG is surfaced as the result so the single-result path
            (the agent graph) still shows it; a BLOCK always takes precedence.
        """
        primary, advisories = await self._screen_input(text)
        if primary.verdict is GuardVerdict.PASS and advisories:
            return advisories[0]
        return primary

    async def _screen_grounding(
        self, text: str, contexts: list[str] | None
    ) -> GuardResult | None:
        """Run the grounding rail; return a BLOCK/FLAG GuardResult, or None when off.

        Advisory by default (an ungrounded answer is a non-blocking FLAG); a hard
        BLOCK when ``grounding_block`` is set. Returns None when grounding is
        disabled, no contexts were supplied, or the answer is judged grounded.
        """
        if not self._ground_answers or not contexts:
            return None
        verdict = await check_grounding(
            text, contexts, completer=self._completer, block=self._grounding_block
        )
        if verdict.grounded:
            return None
        blocking = self._grounding_block
        outcome = GuardVerdict.BLOCK if blocking else GuardVerdict.FLAG
        prefix = "Ungrounded answer blocked" if blocking else "Ungrounded answer flagged"
        return GuardResult(
            verdict=outcome,
            reason=f"{prefix}: {verdict.reason}",
            text=text,
            layer="grounding",
        )

    async def check_output(
        self, text: str, contexts: list[str] | None = None
    ) -> GuardResult:
        """Run the full output rail (schema → content filter → grounding → PII).

        Args:
            text: The outbound model response text to screen.
            contexts: Optional retrieved passages the answer should be grounded in.
                When provided and grounding is enabled, the answer is checked
                against them (advisory FLAG by default). Omit to skip grounding.

        Returns:
            A :class:`GuardResult` with verdict and potentially redacted text. When
            the core rails pass but the grounding advisory fired, the non-blocking
            FLAG is surfaced as the result; a BLOCK/REDACT takes precedence.
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
        safety = await screen_content(text, completer=self._completer)
        if safety.unsafe:
            return GuardResult(
                verdict=GuardVerdict.BLOCK,
                reason=f"Unsafe output blocked ({safety.label() or 'hazard'}): {safety.reason}",
                text=text,
                layer="content_safety",
            )
        custom = await self._run_custom(text, self._output_rails)
        if custom is not None:
            return custom
        grounding = await self._screen_grounding(text, contexts)
        if grounding is not None and grounding.verdict is GuardVerdict.BLOCK:
            return grounding
        redacted, kinds = pii.redact(text)
        if kinds:
            return GuardResult(
                verdict=GuardVerdict.REDACT,
                reason=f"Redacted PII on the outbound path: {', '.join(kinds)}.",
                text=redacted,
                layer="pii",
                redactions=kinds,
            )
        if grounding is not None:  # a non-blocking FLAG; surface it on the clean path
            return grounding
        return GuardResult(
            verdict=GuardVerdict.PASS,
            reason="Output passed schema, content-filter, content-safety, and PII rails.",
            text=text,
        )

    async def stream_check_input(self, text: str) -> AsyncIterator[AegisEvent]:
        """Run the input rail, yielding start → verdict → finish events.

        Args:
            text: The inbound user input text to screen.

        Yields:
            :class:`AegisEvent` objects in order: :class:`StepStarted`, the
            primary :class:`GuardrailEvent`, one :class:`GuardrailEvent` per
            non-blocking advisory (e.g. an off-topic ``verdict="flag"``), and
            :class:`StepFinished`. An advisory FLAG is emitted but never sets
            ``ok=False`` — only a BLOCK stops the request.
        """
        step_id = uuid.uuid4().hex
        yield StepStarted(
            module_id=_MODULE_ID, step_id=step_id, name="guard_input", span_kind=SpanKind.GUARDRAIL
        )
        result, advisories = await self._screen_input(text)
        yield GuardrailEvent(
            module_id=_MODULE_ID,
            step_id=step_id,
            verdict=result.verdict.value,
            rules=[result.layer] if result.layer else [],
            rationale=result.reason,
            redactions=result.redactions,
        )
        for advisory in advisories:
            yield GuardrailEvent(
                module_id=_MODULE_ID,
                step_id=step_id,
                verdict=advisory.verdict.value,
                rules=[advisory.layer] if advisory.layer else [],
                rationale=advisory.reason,
                redactions=advisory.redactions,
            )
        yield StepFinished(
            module_id=_MODULE_ID,
            step_id=step_id,
            name="guard_input",
            span_kind=SpanKind.GUARDRAIL,
            ok=result.verdict is not GuardVerdict.BLOCK,
        )

    async def stream_check_input_agui(self, text: str, emitter: AegisEmitter) -> GuardResult:
        """Run the input rail, streaming a rich AG-UI guardrail verdict.

        Emits STEP_STARTED -> CustomEvent(guardrail_verdict, ...) -> STEP_FINISHED via the
        shared emitter. The verdict payload carries which rail fired, per-rail timing, the
        exact PII spans, and the rationale — the 'show your work' detail for the UI.

        Args:
            text: The inbound user input text to screen.
            emitter: The AG-UI emitter for streaming events.

        Returns:
            A :class:`GuardResult` with verdict and potentially redacted text.
        """
        from aegis.core import stream_names

        timing: dict[str, float] = {}
        async with emitter.step("guard_input", SpanKind.GUARDRAIL):
            t0 = time.monotonic()
            result = await self.check_input(text)
            timing["total"] = round((time.monotonic() - t0) * 1000, 3)
            spans = [
                {"kind": m.kind, "start": m.start, "end": m.end} for m in pii.scan(text)
            ]
            await emitter.custom(
                stream_names.GUARDRAIL_VERDICT,
                {
                    "verdict": result.verdict.value,
                    "rules": [result.layer] if result.layer else [],
                    "rationale": result.reason,
                    "redactions": result.redactions,
                    "redaction_spans": spans,
                    "per_rail_timing_ms": {
                        "schema": None,
                        "pii": None,
                        "injection": None,
                        "total": timing["total"],
                    },
                    "spanKind": SpanKind.GUARDRAIL.value,
                },
            )
        return result
