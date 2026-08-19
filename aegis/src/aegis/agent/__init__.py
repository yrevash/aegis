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
- :func:`graph_topology` — the compiled graph's node/edge shape as plain data, so any
  UI or doc that draws the flow derives it instead of re-typing it.
- :class:`AgentDeps` / :class:`AgentConfig` / :class:`MemoryDeps` — the DI contract +
  bounded-autonomy thresholds.
- :class:`ApprovalRegistry` / :func:`get_approval_registry` /
  :class:`ApprovalOutcome` / :class:`ParkedRunRegistry` — the ``/approval`` rendezvous,
  with :class:`GateHandedOffError` / :class:`ResumeFailedError` as its exactly-once
  hand-off signals.
- :class:`RouterDecision` / :func:`route_query` — the supervisor router, and
  :func:`decide_depth` / :class:`DepthPolicy` — the width classifier and the seam an
  explicit user width is honoured through.
- :class:`SubAgentSpec` / :func:`run_subagent` — one bounded sub-agent that PROPOSES
  risky actions rather than taking them; :func:`run_team` / :func:`synthesise` — the
  concurrent fan-out inside one node and the merge that names its omissions.
- :class:`AgentState` — the typed graph state.
- :mod:`aegis.agent.events` — the wire-event dict builders.
"""

from __future__ import annotations

from . import events
from .approvals import (
    ApprovalOutcome,
    ApprovalRegistry,
    GateHandedOffError,
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
from .orchestrator import ResumeFailedError, resume_parked_run, run_agent
from .router import (
    Depth,
    DepthDecision,
    DepthMode,
    DepthPolicy,
    RouterDecision,
    classify_deterministic,
    decide_depth,
    load_roster,
    route_query,
)
from .state import AgentState
from .subagent import SubAgentResult, SubAgentSpec, SubAgentStatus, run_subagent
from .team import SharedRetrievalPool, TeamOutcome, TeamTask, run_team, synthesise
from .topology import GraphTopology, TopologyEdge, TopologyNode, graph_topology

__all__ = [
    "AgentConfig",
    "AgentDeps",
    "AgentState",
    "ApprovalOutcome",
    "ApprovalRegistry",
    "Depth",
    "DepthDecision",
    "DepthMode",
    "DepthPolicy",
    "GateHandedOffError",
    "GraphTopology",
    "MemoryDeps",
    "ParkedRun",
    "ParkedRunRegistry",
    "ResumeFailedError",
    "RouterDecision",
    "SharedRetrievalPool",
    "SubAgentResult",
    "SubAgentSpec",
    "SubAgentStatus",
    "TeamOutcome",
    "TeamTask",
    "ToolOutcome",
    "TopologyEdge",
    "TopologyNode",
    "UnknownApprovalError",
    "build_agent",
    "classify_deterministic",
    "decide_depth",
    "events",
    "get_approval_registry",
    "get_parked_runs",
    "graph_topology",
    "harness_config",
    "load_roster",
    "resume_parked_run",
    "risk_at_least",
    "risk_rank",
    "route_query",
    "run_agent",
    "run_subagent",
    "run_summary",
    "run_team",
    "synthesise",
]
