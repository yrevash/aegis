"""Backend shim: PII detection/redaction now lives in ``aegis.guardrails.pii``.

Detection is backed by Microsoft Presidio (industry-standard), with a pure-code regex
fallback — see ``aegis.guardrails.pii`` for the engine-selection logic and design
notes. Re-exports the public API unchanged, plus the private ``_luhn_valid``
Luhn-checksum helper that ``tests/guardrails/test_pii.py`` exercises directly.
"""

from __future__ import annotations

from aegis.core.types import PIIMatch
from aegis.guardrails.pii import (
    _luhn_valid,  # noqa: F401 - exercised directly by tests/guardrails/test_pii.py
    active_engine,
    contains_pii,
    redact,
    scan,
)

__all__ = ["PIIMatch", "active_engine", "contains_pii", "redact", "scan"]
