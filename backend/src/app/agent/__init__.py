"""Agent orchestration — the LangGraph plan-and-execute core.

Public surface (consumed by :mod:`app.api.routes`):

- :func:`run_agent` — the async generator of :data:`~app.api.schemas.StreamEvent`
  the SSE endpoint streams; owns the human-in-the-loop pause/resume.
- :func:`build_agent` — compile the graph with injected :class:`AgentDeps`.
- :class:`AgentDeps` / :class:`AgentConfig` — dependency injection + bounded-
  autonomy thresholds.
- :class:`ApprovalRegistry` / :func:`get_approval_registry` /
  :class:`ApprovalOutcome` — the ``/approval`` rendezvous.
- :class:`AgentState` — the typed graph state.

The graph shape, event order, and gate design are documented in
:mod:`app.agent.graph` and :mod:`app.agent.orchestrator`.
"""

from __future__ import annotations

from .approvals import (
    ApprovalOutcome,
    ApprovalRegistry,
    ParkedRun,
    ParkedRunRegistry,
    UnknownApprovalError,
    get_approval_registry,
    get_parked_runs,
)
from .deps import (
    AgentConfig,
    AgentDeps,
    risk_at_least,
    risk_rank,
)
from aegis.agent.harness import harness_config, run_summary

from .graph import build_agent
from .orchestrator import decide_approval, resume_parked_run, run_agent
from .state import AgentState

__all__ = [
    "AgentConfig",
    "AgentDeps",
    "AgentState",
    "ApprovalOutcome",
    "ApprovalRegistry",
    "ParkedRun",
    "ParkedRunRegistry",
    "UnknownApprovalError",
    "build_agent",
    "decide_approval",
    "get_approval_registry",
    "get_parked_runs",
    "harness_config",
    "resume_parked_run",
    "risk_at_least",
    "risk_rank",
    "run_agent",
    "run_summary",
]
