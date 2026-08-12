"""Aegis agent — the LangGraph plan-and-execute orchestration core.

A pure graph-over-injected-deps: the plan → gate → act → reflect flow, the
supervisor router, the bounded self-repair loop and the human-in-the-loop gate,
all driven through the :class:`AgentDeps` seam. It depends only on ``langgraph`` +
the injected capability callables + the injected tracing (``aegis.observability``)
+ an injected event validator — never on any host's API schema, config, adapter or
data layer. A host wires the real capabilities in its composition root (mirroring
``gateway.configure(...)``) and injects the checkpointer, event validator, durable
approvals-inbox writer and post-run hook into :func:`run_agent`.

Public surface:

- :func:`run_agent` — the async generator of stamped events; owns the pause/resume.
- :func:`build_agent` — compile the graph with injected :class:`AgentDeps` +
  checkpointer.
- :func:`resume_parked_run` — headless resume of a checkpointed parked run.
- :func:`harness_config` — the tweakable-config schema (every knob + type/default/allowed)
  as data for the harness UI; :func:`run_summary` — the structured per-run trace record
  folded from the SAME emitted events.
- :class:`AgentDeps` / :class:`AgentConfig` / :class:`MemoryDeps` — the DI contract +
  bounded-autonomy thresholds.
- :class:`ApprovalRegistry` / :func:`get_approval_registry` /
  :class:`ApprovalOutcome` / :class:`ParkedRunRegistry` — the ``/approval`` rendezvous.
- :class:`RouterDecision` / :func:`route_query` — the supervisor router.
- :class:`AgentState` — the typed graph state.
- :mod:`aegis.agent.events` — the wire-event dict builders.
"""

from __future__ import annotations

from . import events
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
    MemoryDeps,
    ToolOutcome,
    risk_at_least,
    risk_rank,
)
from .graph import build_agent
from .harness import harness_config, run_summary
from .orchestrator import resume_parked_run, run_agent
from .router import RouterDecision, classify_deterministic, load_roster, route_query
from .state import AgentState

__all__ = [
    "AgentConfig",
    "AgentDeps",
    "AgentState",
    "ApprovalOutcome",
    "ApprovalRegistry",
    "MemoryDeps",
    "ParkedRun",
    "ParkedRunRegistry",
    "RouterDecision",
    "ToolOutcome",
    "UnknownApprovalError",
    "build_agent",
    "classify_deterministic",
    "events",
    "get_approval_registry",
    "get_parked_runs",
    "harness_config",
    "load_roster",
    "resume_parked_run",
    "risk_at_least",
    "risk_rank",
    "route_query",
    "run_agent",
    "run_summary",
]
