"""Backend shim: the guardrail system now lives in ``aegis.guardrails``.

This package used to own the full layered, RAM-friendly, defense-in-depth
guardrail implementation described in ``docs/security.md`` §3. That
implementation has moved to the standalone, LLM-agnostic ``aegis.guardrails``
package (see ``/aegis``) so it can be imported by any component without pulling
in this platform's LLM gateway. This module is the **strangler shim**: it
re-exports the package's public API and wires the platform's LiteLLM gateway
(``app.core.llm.complete``) as the injected ``ChatCompleter``, preserving the
previous behaviour (and the previous no-arg ``check_input``/``check_output``
call sites) for the agent graph and the existing test suite.

Public contract (unchanged from before the migration):
    * :class:`GuardResult` — ``verdict`` / ``reason`` / ``text`` (text may be
      redacted).
    * :func:`check_input` — schema/format validation -> PII redaction -> API
      injection/jailbreak classifier.
    * :func:`check_output` — schema/format validation -> content filter -> PII
      redaction.

Two enforcement front doors over **one** policy:
    * The fast programmatic API here (:func:`check_input`/:func:`check_output`),
      which the agent graph calls directly and which the tests exercise offline.
    * A declarative **NeMo Guardrails / Colang** policy (loaded via
      :mod:`app.guardrails.nemo`, itself a shim over ``aegis.guardrails.nemo``)
      whose flows call custom actions that delegate back to the same rails. The
      Colang file doubles as a human-readable security artifact for the jury.

Streaming caveat: the output rail assumes the *complete* answer. When the
answer is streamed token-by-token the rail cannot scan-then-emit, so the caller
must either **buffer briefly** and run :func:`check_output` on the buffered
text, or stream optimistically and **scan post-hoc**, redacting/retracting on a
hit. Never stream raw tokens straight past the output rail.
"""

from __future__ import annotations

from aegis.core.types import GuardResult, InjectionVerdict, PIIMatch
from aegis.guardrails import Guardrails

from app.guardrails import classifier, nemo, pii, schema  # noqa: F401 - re-exported submodules


async def _gateway_completer(
    messages: list[dict], *, response_format: dict | None = None
) -> str:
    """Adapt ``app.core.llm.complete`` to the ``aegis`` ``ChatCompleter`` protocol.

    This is the **only** place under ``app.guardrails`` that references the
    platform's LLM gateway (``app.core.llm`` / ``app.core.models``); every other
    module here is LLM-agnostic, matching ``aegis.guardrails``. Imports are
    deferred so importing this package never requires the gateway or its
    dependencies (keeps the offline unit tests fast and network-free).

    Args:
        messages: OpenAI-style chat messages.
        response_format: Optional structured-output hint (e.g. JSON mode).

    Returns:
        The assistant's raw text.
    """
    from app.core.llm import complete
    from app.core.models import ModelRole

    result = await complete(
        ModelRole.CHEAP, messages, temperature=0.0, response_format=response_format
    )
    return result.content


#: The process-wide guardrail pipeline, wired with the platform's cheap-model
#: completer. A single instance is fine — ``Guardrails`` holds no per-call state.
_guard = Guardrails(completer=_gateway_completer)


async def check_input(text: str) -> GuardResult:
    """Run the full input rail (schema -> PII redaction -> injection) via aegis.

    Args:
        text: The raw inbound query.

    Returns:
        A :class:`GuardResult`. ``block`` when the payload is malformed or judged
        to be prompt injection; ``redact`` when it was clean of injection but
        carried PII (``text`` is the redacted form); otherwise ``pass``.
    """
    return await _guard.check_input(text)


async def check_output(text: str) -> GuardResult:
    """Run the full output rail (schema -> content filter -> PII) via aegis.

    Args:
        text: The model's answer text (assumed complete; see streaming caveat).

    Returns:
        A :class:`GuardResult`. ``block`` when the output is malformed or trips
        the content filter; ``redact`` when it carried PII (``text`` is the
        redacted form); otherwise ``pass``.
    """
    return await _guard.check_output(text)


__all__ = [
    "GuardResult",
    "Guardrails",
    "InjectionVerdict",
    "PIIMatch",
    "check_input",
    "check_output",
    "classifier",
    "nemo",
    "pii",
    "schema",
]
