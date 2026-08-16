"""The compiled agent graph's topology, as plain serialisable data.

Anything that *draws* the agent's flow — the console's orchestration map, a doc
diagram, an architecture review — needs the node/edge list. Hand-maintaining a
second copy of that list is how a published architecture picture ends up
contradicting the implementation (e.g. drawing a step the graph no longer wires, or
hanging the human gate off something other than the **tool risk** that
:mod:`aegis.agent.graph` actually gates on). So this module derives the picture from
the ONE source of truth: it compiles the real graph and reads LangGraph's own
:meth:`~langgraph.graph.state.CompiledStateGraph.get_graph` view of it.

The compiled graph used here is a *shape-only* graph: it is built over inert
dependencies whose bodies raise if called, and it is never invoked. Nothing here
touches a model, a store or the network.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START
from langgraph.graph.state import CompiledStateGraph

from .deps import AgentDeps
from .graph import NODE_LABELS, build_agent

__all__ = [
    "GraphTopology",
    "TopologyEdge",
    "TopologyNode",
    "graph_topology",
]


class TopologyNode(TypedDict):
    """One executable node of the agent graph."""

    #: Stable node id — exactly the name carried on ``node_started``/``node_finished``.
    id: str
    #: Human label, the same string the ``_timed(...)`` wrapper streams with the events.
    label: str
    #: True when the graph's entrypoint routes straight into this node.
    entry: bool
    #: True when this node has an edge to ``END`` (a run can finish here).
    terminal: bool


class TopologyEdge(TypedDict):
    """One directed edge between two executable nodes."""

    source: str
    target: str
    #: True when LangGraph reports the edge as a branch of a conditional router
    #: (``add_conditional_edges``) rather than an unconditional ``add_edge``.
    conditional: bool


class GraphTopology(TypedDict):
    """The whole topology: executable nodes plus the edges between them."""

    nodes: list[TopologyNode]
    edges: list[TopologyEdge]


def _unreachable(*_args: Any, **_kwargs: Any) -> Any:  # noqa: ANN401 - inert stub
    """Stand in for an injected capability that must never actually be called.

    :func:`graph_topology` compiles the graph purely to read its shape, so every
    node body is dead code there. Raising (rather than returning a plausible
    fake) makes any accidental execution of this graph loud instead of silent.
    """
    raise RuntimeError("shape-only agent graph: node bodies are never executed")


def _inert_deps() -> AgentDeps:
    """Build an :class:`AgentDeps` whose every capability raises when called.

    The graph's wiring closes over ``deps`` but only *reads* ``deps.config`` at
    build time, so all-defaults config + raising callables is enough to compile a
    structurally identical graph with zero infrastructure.
    """
    return AgentDeps(
        complete=_unreachable,
        retrieve=_unreachable,
        check_input=_unreachable,
        check_output=_unreachable,
        tool_definitions_for=_unreachable,
        run_tool=_unreachable,
        tool_risk=_unreachable,
        render_system_prompt=_unreachable,
    )


def graph_topology(agent: CompiledStateGraph | None = None) -> GraphTopology:
    """Return the compiled graph's topology as plain, JSON-serialisable data.

    Args:
        agent: A compiled graph to describe. Defaults to a freshly compiled
            shape-only graph (see :func:`_inert_deps`), which is what a host
            serves: the topology is a property of the wiring, not of the
            particular capabilities injected into it.

    Returns:
        A :class:`GraphTopology` — ``nodes`` in the graph's own declaration order,
        each with its stable ``id``, the human ``label`` the node's events carry,
        and ``entry``/``terminal`` flags; ``edges`` restricted to the executable
        nodes (the ``START``/``END`` sentinels are folded into those two flags
        instead), each marked ``conditional`` when LangGraph reports it as a
        branch of an ``add_conditional_edges`` router.

    Raises:
        KeyError: If the graph gained a node with no entry in
            :data:`~aegis.agent.graph.NODE_LABELS` — a deliberate tripwire, so a
            new node cannot ship without its label.
    """
    drawable = (agent or build_agent(_inert_deps())).get_graph()
    sentinels = {START, END}
    ids = [nid for nid in drawable.nodes if nid not in sentinels]
    entries = {e.target for e in drawable.edges if e.source == START}
    terminals = {e.source for e in drawable.edges if e.target == END}
    nodes: list[TopologyNode] = [
        {
            "id": nid,
            "label": NODE_LABELS[nid],
            "entry": nid in entries,
            "terminal": nid in terminals,
        }
        for nid in ids
    ]
    edges: list[TopologyEdge] = [
        {
            "source": e.source,
            "target": e.target,
            "conditional": bool(e.conditional),
        }
        for e in drawable.edges
        if e.source not in sentinels and e.target not in sentinels
    ]
    return {"nodes": nodes, "edges": edges}
