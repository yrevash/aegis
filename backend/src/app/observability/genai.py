"""Strangler shim: the ``gen_ai.*`` span helpers now live in ``aegis.observability.genai``.

Pure re-export — no host coupling to sever here.
"""

from __future__ import annotations

from aegis.observability.genai import GenAIOperation, genai_span, genai_span_sync, set_usage

__all__ = ["GenAIOperation", "genai_span", "genai_span_sync", "set_usage"]
