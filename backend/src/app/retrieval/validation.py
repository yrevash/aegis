"""Backend shim: content validation now lives in ``aegis.retrieval.validation``."""

from __future__ import annotations

from aegis.retrieval.validation import ValidationResult, validate_content

__all__ = ["ValidationResult", "validate_content"]
