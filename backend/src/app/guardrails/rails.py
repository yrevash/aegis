"""Backward-compatible import path for the rail orchestration entry points.

The layered input/output rail orchestration used to live in this module; it now
lives in :mod:`app.guardrails` (which delegates to ``aegis.guardrails``). Kept as
a thin re-export so ``app.guardrails.rails`` — the module path
``app.capabilities`` declares for the "Aegis Guardrails" platform capability —
still imports and stays factual.
"""

from __future__ import annotations

from app.guardrails import check_input, check_output

__all__ = ["check_input", "check_output"]
