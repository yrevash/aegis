"""Backend shim: schema/format validation now lives in ``aegis.guardrails.schema``.

The cheapest, most deterministic layers in the defense-in-depth stack (see
``aegis.guardrails.schema`` for the checks and their rationale). Re-exports the
public API unchanged, including the char-limit constants existing tests import.
"""

from __future__ import annotations

from aegis.guardrails.schema import (
    MAX_INPUT_CHARS,
    MAX_OUTPUT_CHARS,
    content_filter,
    validate_input_format,
    validate_output_format,
)

__all__ = [
    "MAX_INPUT_CHARS",
    "MAX_OUTPUT_CHARS",
    "content_filter",
    "validate_input_format",
    "validate_output_format",
]
