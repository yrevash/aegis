"""Aegis security-posture surface — threats mapped to their *wired* controls.

The cross-cutting, honest security accessor. :func:`security_posture` returns one
:class:`PostureEntry` per major threat (OWASP LLM Top-10 2025 + key agentic
themes), each with a ``status`` derived from the live wiring at call time — never a
static list, never a fabricated ``enforced``.

Standalone usage::

    from aegis.security import security_posture
    for entry in security_posture():
        print(entry.threat_id, entry.control, entry.status)
"""

from __future__ import annotations

from aegis.security.posture import (
    PostureEntry,
    PostureSignals,
    PostureStatus,
    read_signals,
    resolve_symbol,
    security_posture,
)

__all__ = [
    "PostureEntry",
    "PostureSignals",
    "PostureStatus",
    "read_signals",
    "resolve_symbol",
    "security_posture",
]
