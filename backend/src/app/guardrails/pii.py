"""Backend shim: PII detection/redaction now lives in ``aegis.guardrails.pii``.

Self-built, pure-code detection (no local model, no network) — see
``aegis.guardrails.pii`` for the detector table and design notes. Re-exports the
public API unchanged, plus the private ``_luhn_valid`` Luhn-checksum helper that
``tests/guardrails/test_pii.py`` exercises directly.
"""

from __future__ import annotations

from aegis.core.types import PIIMatch
from aegis.guardrails.pii import (
    _luhn_valid,  # noqa: F401 - exercised directly by tests/guardrails/test_pii.py
    contains_pii,
    redact,
    scan,
)

__all__ = ["PIIMatch", "contains_pii", "redact", "scan"]
