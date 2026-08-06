"""LLM-Ops: the closed feedback loop over the agent (see ``docs/HARNESS_LLMOPS_PLAN.md``).

Trace → Eval → Observe → Diagnose → Gate → Release, where Release writes a versioned,
reversible system prompt/config back into the harness. Every stage is domain-agnostic
core; what "good" means for a domain comes from the eval corpus + adapter prompt (the
floor the registry can only build on, never below).
"""

from __future__ import annotations
