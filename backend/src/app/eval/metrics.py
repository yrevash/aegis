"""Strangler shim: ``app.eval.metrics`` delegates to :mod:`aegis.evals.metrics`.

The deterministic RAGAS-style lexical proxies now live in the standalone ``aegis.evals``
package; re-exported here under their historical names.
"""

from __future__ import annotations

from aegis.evals.metrics import (
    AggregateScore,
    CaseScore,
    aggregate,
    score_case,
)

__all__ = [
    "AggregateScore",
    "CaseScore",
    "aggregate",
    "score_case",
]
