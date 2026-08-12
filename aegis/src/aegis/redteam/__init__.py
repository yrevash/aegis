"""Aegis red-team — a real, importable harness that attacks the guardrails.

Unlike a garak-style scan (which drives a *live* LLM endpoint), this harness makes
the Aegis **guardrail rail** the target and attacks it directly, so it runs offline
and in-process. Every verdict is the rail's *actual* output — the harness never
fabricates a pass/fail.

Standalone usage::

    import asyncio
    from aegis.redteam import run_redteam

    report = asyncio.run(run_redteam())          # offline: deterministic backstops
    print(report.summary())
    print(report.block_rate, report.passed)

Wire a :class:`aegis.core.interfaces.ChatCompleter` to additionally exercise the
model-based injection / content-safety layers::

    report = asyncio.run(run_redteam(completer=my_completer))

Leaf-clean: this package imports only :mod:`aegis.guardrails` (+ ``aegis.core`` and
stdlib). See ``tests/redteam/test_isolation.py``.
"""

from __future__ import annotations

from aegis.redteam.battery import ATTACK_BATTERY, Attack, Category, Expectation
from aegis.redteam.runner import (
    DEFAULT_THRESHOLDS,
    AttackResult,
    CategoryReport,
    RedTeamReport,
    RedTeamThresholds,
    run_redteam,
)

__all__ = [
    "ATTACK_BATTERY",
    "DEFAULT_THRESHOLDS",
    "Attack",
    "AttackResult",
    "Category",
    "CategoryReport",
    "Expectation",
    "RedTeamReport",
    "RedTeamThresholds",
    "run_redteam",
]
