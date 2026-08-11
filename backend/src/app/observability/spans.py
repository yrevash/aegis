"""Strangler shim: the non-LLM span helper now lives in ``aegis.observability.spans``.

Pure re-export — no host coupling to sever here.
"""

from __future__ import annotations

from aegis.observability.spans import set_span_attribute, set_span_attributes, span

__all__ = ["set_span_attribute", "set_span_attributes", "span"]
