"""Layered, RAM-friendly, defense-in-depth guardrails (no local model).

This package is the security spine described in ``docs/security.md`` §3. Every
model interaction is wrapped by an **input rail** (before text reaches the model)
and an **output rail** (before text reaches the user/downstream). All detection
is **pure code or a cheap API call** — there is deliberately *no local guardrail
model* (the 16 GB / no-GPU constraint drops Llama Guard et al.).

Public contract (see ``docs/AGENT_BRIEF.md``):
    * :class:`GuardResult` — ``verdict`` / ``reason`` / ``text`` (text may be
      redacted).
    * :func:`check_input` — schema/format validation → PII redaction → API
      injection/jailbreak classifier.
    * :func:`check_output` — schema/format validation → content filter → PII
      redaction.

Two enforcement front doors over **one** policy:
    * The fast programmatic API here (:func:`check_input`/:func:`check_output`),
      which the agent graph calls directly and which the tests exercise offline.
    * A declarative **NeMo Guardrails / Colang** policy under ``config/`` (loaded
      via :mod:`app.guardrails.nemo`) whose flows call custom actions that
      delegate back to the same functions. The Colang file doubles as a
      human-readable security artifact for the jury.

Streaming caveat (also in ``config/README``): the output rail assumes the
*complete* answer. When the answer is streamed token-by-token the rail cannot
scan-then-emit, so the caller must either **buffer briefly** and run
:func:`check_output` on the buffered text, or stream optimistically and **scan
post-hoc**, redacting/retracting on a hit. Never stream raw tokens straight past
the output rail.

Targeted library versions: NeMo Guardrails 0.23 with Colang 1.0 (see
:mod:`app.guardrails.nemo`). PII detection and schema validation are pure stdlib.
"""

from __future__ import annotations

from app.guardrails.classifier import classify_injection
from app.guardrails.models import GuardResult, InjectionVerdict, PIIMatch
from app.guardrails.pii import contains_pii, redact, scan
from app.guardrails.rails import check_input, check_output

__all__ = [
    "GuardResult",
    "InjectionVerdict",
    "PIIMatch",
    "check_input",
    "check_output",
    "classify_injection",
    "contains_pii",
    "redact",
    "scan",
]
