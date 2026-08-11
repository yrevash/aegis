"""Backend shim: the injection/jailbreak classifier now lives in ``aegis.guardrails.classifier``.

**API change**: the classifier is now LLM-agnostic. ``classify_injection`` and
``detect_injection`` take an injected ``completer``
(:class:`aegis.core.interfaces.ChatCompleter`) instead of reaching into this
platform's LLM gateway themselves — the old ``_cheap_completion`` gateway seam
is gone, on purpose (see ``docs`` for the migration rationale). Callers that want
the platform's cheap model should go through :func:`app.guardrails.check_input` /
:func:`app.guardrails.check_output` (which wire the real gateway in via
``app.guardrails._gateway_completer``), or pass their own completer directly to
the functions re-exported here.
"""

from __future__ import annotations

from aegis.guardrails.classifier import (
    classify_injection,
    detect_injection,
    deterministic_injection,
)

__all__ = ["classify_injection", "deterministic_injection", "detect_injection"]
