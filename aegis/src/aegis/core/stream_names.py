"""Canonical CustomEvent names — the single source of truth shared by every module.

AG-UI carries domain payloads via ``CustomEvent(name, value)``. These constants are the
agreed ``name`` strings; the frontend mirrors them in ``frontend/src/agui/streamNames.ts``.
"""

from __future__ import annotations

REASONING = "reasoning"
GUARDRAIL_VERDICT = "guardrail_verdict"
SHAP_EXPLANATION = "shap_explanation"
CONFORMAL_INTERVAL = "conformal_interval"
RETRIEVAL_CITATIONS = "retrieval_citations"
ROUTING = "routing"
MEMORY_RECALL = "memory_recall"

ALL: frozenset[str] = frozenset(
    {REASONING, GUARDRAIL_VERDICT, SHAP_EXPLANATION, CONFORMAL_INTERVAL,
     RETRIEVAL_CITATIONS, ROUTING, MEMORY_RECALL}
)


def is_known(name: str) -> bool:
    """Return whether ``name`` is a registered CustomEvent name."""
    return name in ALL
