"""Layered, defense-in-depth orchestration of the input and output rails.

This module is the runtime enforcement engine behind the public
:func:`check_input` / :func:`check_output` contract from ``docs/AGENT_BRIEF.md``.
It composes the individual rails (schema/format validation, self-built PII
redaction, and the API injection classifier) in a deliberate order so that no
single layer is a single point of failure — the "security is layers, not one
tool" principle of ``docs/security.md`` §3.

The same policy is *also* expressed declaratively as a NeMo Guardrails **Colang**
file (``config/rails/*.co``) whose custom actions delegate back to these very
functions. Colang is the human-readable security artifact for the jury; this
module is the fast, unit-testable engine the agent graph calls directly. Both
enforce one policy — see ``config/README`` note in ``__init__``.

Input-rail ordering (each layer can only *tighten* the verdict):
    1. **Schema/format validation** — reject malformed/oversized payloads first
       (cheapest, no I/O).
    2. **PII scan + redaction** — mask PII *before* step 3 so we never ship the
       user's secrets to the classifier API (avoiding a self-inflicted LLM06
       disclosure).
    3. **Injection/jailbreak classifier** — the one API call, on redacted text.

Output-rail ordering:
    1. **Schema/format validation** — structural well-formedness (LLM02).
    2. **Content filter** — backstop against obvious system-prompt leakage.
    3. **PII redaction** — mask any PII on the outbound path (LLM06).

Streaming caveat: see the note in :mod:`app.guardrails` (``README``) — the output
rail assumes the *complete* answer. For token streaming, buffer briefly or scan
post-hoc and redact; do not stream raw tokens straight past this rail.
"""

from __future__ import annotations

from app.api.schemas import GuardVerdict
from app.config import get_settings
from app.guardrails import classifier, pii, schema
from app.guardrails.models import GuardResult


def _use_nemo() -> bool:
    """Whether to enforce via the NeMo Colang engine (configured **and** available)."""
    if get_settings().guardrails_engine.lower() != "nemo":
        return False
    from app.guardrails import nemo

    return nemo.nemo_available()


async def check_input(text: str) -> GuardResult:
    """Run the full input rail over a user query.

    Enforcement runs through the fast programmatic rails by default, or the NeMo
    Guardrails **Colang** policy when ``GUARDRAILS_ENGINE=nemo`` — the same policy,
    two front doors (see module docstring).

    Args:
        text: The raw inbound query.

    Returns:
        A :class:`GuardResult`. ``block`` when the payload is malformed or judged
        to be prompt injection; ``redact`` when it was clean of injection but
        carried PII (``text`` is the redacted form); otherwise ``pass``.
    """
    if _use_nemo():
        from app.guardrails import nemo

        return await nemo.nemo_check_input(text)

    fmt = schema.validate_input_format(text)
    if not fmt.ok:
        return GuardResult(
            verdict=GuardVerdict.BLOCK, reason=fmt.reason, text=text, layer="schema"
        )

    # Redact PII *before* the classifier call so no secret leaves for the API.
    redacted, kinds = pii.redact(text)

    # Deterministic signature backstop → cheap-model classifier (defense in depth,
    # so injection defense never depends solely on the API call).
    verdict = await classifier.detect_injection(redacted)
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


async def check_output(text: str) -> GuardResult:
    """Run the full output rail over a model answer before it is returned.

    Args:
        text: The model's answer text (assumed complete; see streaming caveat).

    Returns:
        A :class:`GuardResult`. ``block`` when the output is malformed or trips
        the content filter; ``redact`` when it carried PII (``text`` is the
        redacted form); otherwise ``pass``.
    """
    if _use_nemo():
        from app.guardrails import nemo

        return await nemo.nemo_check_output(text)

    fmt = schema.validate_output_format(text)
    if not fmt.ok:
        return GuardResult(
            verdict=GuardVerdict.BLOCK, reason=fmt.reason, text=text, layer="schema"
        )

    filtered = schema.content_filter(text)
    if not filtered.ok:
        return GuardResult(
            verdict=GuardVerdict.BLOCK,
            reason=filtered.reason,
            text=text,
            layer="content",
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
