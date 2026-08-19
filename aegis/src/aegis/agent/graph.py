"""The plan-and-execute LangGraph with a bounded self-repair loop and a human gate.

The graph makes the agent's plan *visible and auditable*: each node is one step in
the money-shot trace (guardrail → route → retrieve → plan → gate → act →
reflect → guardrail → answer). Nodes emit event payloads through the LangGraph
**custom stream writer**; the orchestrator stamps and forwards them to the SSE
client (via an injected event validator, so the graph never imports a host schema).

**Self-repair loop (Reflexion-style, bounded).** After an action executes, the
``reflect`` node judges the outcome from the executed ``ToolOutcome`` results
(``.ok``/``.summary`` — domain-agnostic, never hardcoded domain logic). When the goal
is not yet met (an action failed or was insufficient) *and* the iteration budget
(``config.max_plan_iterations``) still allows it, the graph loops back to ``plan`` for
another round — re-planning with the previous failure fed back, re-gating on risk, and
acting again. When the goal is met or the hard cap is reached it proceeds to
``generate``. The counter is incremented in ``plan``, so the loop is guaranteed to
terminate. On the common/tested happy path (the first action succeeds) the loop adds a
single ``reflection`` event and routes straight to ``generate`` — the money-shot trace
is otherwise unchanged.

**No machine-learning step runs in this graph.** The ML spine (``aegis.ml``) is a
*tenant-facing capability* — served by the host's ``/ml/*`` endpoints and its forecast
dashboard — not a stage of the agent pipeline. It was removed from the graph because it
decorated a decision it never made: its output was injected into two prompts and emitted
as one informational event, and nothing routed, gated, or branched on it.

The ``gate`` node's human-in-the-loop pause is driven by **tool risk**: a proposed
action at or above ``config.gate_min_risk`` routes to the ``approval`` node, which
calls :func:`langgraph.types.interrupt` and pauses on a checkpointer until
``POST /approval`` resumes it (the money-shot). Tool risk is the only gating signal.

The checkpointer (required for the gate's ``interrupt``/resume) is **injected** —
``build_agent(deps, *, checkpointer=...)`` — defaulting to an in-memory saver; the
durable Postgres saver stays a host/backend concern wired at the composition root.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RetryPolicy, interrupt

from aegis.core.models import ModelRole
from aegis.core.types import GuardStage, GuardVerdict, RiskLevel, RunStatus
from aegis.observability import SpanKind, semconv, set_span_attributes, span
from aegis.retrieval.agentic import agentic_retrieve
from aegis.retrieval.corpus import corpus_version
from aegis.retrieval.query_rewrite import CallUsage, rewrite_query
from aegis.retrieval.types import RetrievalScope

from . import events
from .deps import (
    AgentDeps,
    risk_at_least,
    risk_rank,
)
from .retry import call_with_retry
from .router import (
    Depth,
    DepthMode,
    DepthPolicy,
    decide_depth,
    load_roster,
    route_query,
)
from .state import AgentState
from .subagent import SubAgentResult, SubAgentStatus
from .team import (
    SharedRetrievalPool,
    TeamOutcome,
    TeamTask,
    build_team,
    plan_team_tasks,
    run_team,
    synthesis_note,
    synthesise,
)

logger = logging.getLogger(__name__)


#: The human label for every executable node, keyed by its stable node id.
#:
#: This is the ONE place a node's display name is written. The wiring below feeds
#: it to :func:`_timed` (so ``node_started``/``node_finished`` carry it) and
#: :func:`aegis.agent.topology.graph_topology` reads it to label the served
#: topology — which is why anything that draws the graph (the console's
#: orchestration map) can no longer drift from what actually runs. The three nodes
#: wired *plain* (``recall_memory``, ``persist_memory``, ``approval``) emit no node
#: events of their own but still appear in the topology, so they are labelled here
#: too.
NODE_LABELS: dict[str, str] = {
    "guard_input": "Input guardrail",
    "route": "Route intent",
    "answer_memory": "Answer from memory",
    "recall_memory": "Recall memory",
    "persist_memory": "Persist memory",
    "plan_team": "Plan the team",
    "run_team": "Run agents concurrently",
    "synthesize": "Synthesise findings",
    "retrieve": "Agentic retrieval",
    "plan": "Reason & plan",
    "gate": "Risk gate",
    "approval": "Human approval",
    "act": "Execute actions",
    "reflect": "Reflect & self-repair",
    "generate": "Generate answer",
    "guard_output": "Output guardrail",
    "stream": "Stream answer",
}


# Splits plan text into sentences for chunked ``reasoning`` events. Like the answer
# chunking in ``stream_answer``, this paces out already-produced text rather than
# streaming from the model -- see that node's docstring for why the gateway call is
# deliberately non-streaming.
_SENTENCE = re.compile(r"[^.!?]+[.!?]*")


def _sentences(text: str) -> list[str]:
    """Split ``text`` into trimmed, non-empty sentence chunks."""
    return [s.strip() for s in _SENTENCE.findall(text) if s.strip()]


#: Roster specialist role -> the graph node that handles it.
#:
#: The supervisor router (``aegis.agent.router``) already classifies into an
#: arbitrary number of roster specialists, but the *graph* can only dispatch to a
#: node that exists. This table is that seam, and it is deliberately explicit:
#: adding a specialist to an adapter roster is not enough to make it routable —
#: it needs a handler node and an entry here.
#:
#: Before this existed the edge was a hardcoded ``"memory" -> answer_memory, else
#: recall_memory`` binary, so a third specialist an adapter declared was silently
#: swallowed into the qa pipeline with no signal anywhere.
SPECIALIST_NODES: dict[str, str] = {
    "qa": "recall_memory",  # the full retrieve -> plan -> gate -> act pipeline
    "memory": "answer_memory",  # answers from long-term memory, skipping RAG/tools
    # The adaptive fan-out. NOT a roster role an adapter declares — the router writes
    # it when the depth classifier (or the user's explicit width) says TEAM, which is
    # why it is absent from every roster and still must be dispatchable.
    "team": "plan_team",
}

#: The role used when the roster names one the graph cannot dispatch.
_FALLBACK_ROLE = "qa"

#: The pseudo-role the router writes for a fan-out turn (see ``SPECIALIST_NODES``).
_TEAM_ROLE = "team"


def _route_specialist(state: AgentState) -> str:
    """Dispatch the turn to its specialist's handler node.

    Falls back to the qa pipeline for an unmapped role — but **loudly**. A roster
    that declares a specialist the graph has no node for is a wiring mistake, and
    silently answering as qa is exactly how such a mistake stays invisible.
    """
    role = state.get("agent_role") or _FALLBACK_ROLE
    node = SPECIALIST_NODES.get(role)
    if node is None:
        logger.warning(
            "Roster specialist %r has no handler node; falling back to %r. Add a node "
            "and a SPECIALIST_NODES entry to make it routable.",
            role,
            _FALLBACK_ROLE,
        )
        return SPECIALIST_NODES[_FALLBACK_ROLE]
    return node


def _warn_unroutable_specialists(deps: AgentDeps) -> None:
    """Log once at build time for any roster role the graph cannot dispatch.

    Surfaces the wiring gap at startup rather than once per run, and never raises:
    a roster is host data, and a bad entry must not stop the agent from serving.
    """
    try:
        roster = _resolve_roster(deps)
        # ``roles`` is a METHOD on every roster implementation, not an attribute.
        # Iterating the bound method raised TypeError, which the except below then
        # swallowed — so this warning could never fire for any roster, which is
        # exactly the silence it exists to break. Call it.
        declared = roster.roles()
        roles = {str(r) for r in declared or ()}
    except Exception:  # noqa: BLE001 - roster read is defensive by design
        logger.warning(
            "Could not read the roster to check for unroutable specialists.",
            exc_info=True,
        )
        return
    unroutable = sorted(roles - SPECIALIST_NODES.keys())
    if unroutable:
        logger.warning(
            "Roster declares specialist(s) %s with no handler node; they will be "
            "answered by the %r pipeline.",
            unroutable,
            _FALLBACK_ROLE,
        )


def _route_gate(state: AgentState) -> str:
    """Route out of the ``gate`` node by the risk-driven gating decision.

    Returns:
        ``"approval"`` to route a risky action to the human gate, or ``"act"`` to
        execute a within-ceiling action autonomously.
    """
    if state.get("gated"):
        return "approval"
    return "act"


def _route_reflect(state: AgentState) -> str:
    """Route out of the ``reflect`` node: loop back to ``plan`` or finalise.

    Returns:
        ``"plan"`` when the self-repair loop decided to try another bounded round
        (action failed/insufficient and the iteration budget still allows it), or
        ``"generate"`` to compose the final answer (goal met or budget exhausted).
    """
    if _is_team_run(state):
        return "generate"
    return "plan" if state.get("reflect_retry") else "generate"


def _is_team_run(state: AgentState) -> bool:
    """Whether this turn is a fan-out run (``route`` wrote the ``team`` role)."""
    return state.get("agent_role") == _TEAM_ROLE


_NodeBody = Callable[[AgentState], Awaitable[dict[str, Any]]]


async def _call_with_retry(
    body: _NodeBody, state: AgentState, node: str, policy: RetryPolicy | None
) -> dict[str, Any]:
    """Invoke ``body`` under ``policy``, retrying transient failures in place.

    The retry lives *inside* the timing/emit wrapper on purpose. Wiring the same policy
    as LangGraph's node-level ``retry_policy=`` re-invokes the whole wrapper, which emits
    ``node_started`` **before** the body — so a transient failure produced a second
    ``node_started`` for one logical node execution, and ``run_summary`` folded that into
    an extra, permanently unpaired node record with ``duration_ms: None``. Retrying only
    the body keeps the start/finish pair exactly one-to-one with the node execution, and
    the measured duration spans every attempt (which is the honest wall clock).

    The retry mechanism itself lives in :mod:`aegis.agent.retry` because the sub-agent
    loop needs the same policy honoured the same way; this wrapper is only the
    node-shaped adapter over it.
    """
    return await call_with_retry(
        lambda: body(state), policy=policy, label=f"Node {node}"
    )


def _timed(
    node: str, label: str, kind: SpanKind = SpanKind.CHAIN, *, retry: RetryPolicy | None = None
) -> Callable[[_NodeBody], _NodeBody]:
    """Wrap a node body to emit ``node_started`` / ``node_finished`` with timing.

    The wrapper times the body's wall-clock duration and emits one
    ``node_finished`` per run. A node that made a model call can surface its
    per-call model/usage by returning a ``_telemetry`` dict, which is popped off
    the update here (never written to graph state).

    The **same** wrapper also opens one OpenTelemetry span of OpenInference
    ``kind`` around the body, so every node emits BOTH its stream event and a
    span. The span is the current span while the body runs, so retrieval/rerank,
    guardrail, tool and LLM spans opened inside the body nest beneath it and the
    trace reads as a tree. The instrumentation is a no-op when no tracer is
    configured (tests / lite mode); ``node`` becomes a RETRIEVER/GUARDRAIL span
    for those steps and a CHAIN span otherwise.

    Args:
        node: The node id stamped on the events and the span.
        label: The human-readable node label.
        kind: The OpenInference span kind for this node.
        retry: An optional :class:`~langgraph.types.RetryPolicy` applied to the **body**
            (see :func:`_call_with_retry`). Passing it here rather than to
            ``add_node(..., retry_policy=...)`` is what keeps one node execution to
            exactly one ``node_started``/``node_finished`` pair across retries.
    """

    def decorator(body: _NodeBody) -> _NodeBody:
        @wraps(body)
        async def wrapper(state: AgentState) -> dict[str, Any]:
            writer = get_stream_writer()
            writer(events.node_started(node, label))
            start = time.perf_counter()
            with span(
                kind,
                f"node.{node}",
                attributes={
                    semconv.GRAPH_NODE: node,
                    semconv.GRAPH_NODE_LABEL: label,
                },
            ) as node_span:
                update = await _call_with_retry(body, state, node, retry)
                node_span.set_attribute(
                    semconv.GRAPH_NODE_DURATION_MS,
                    int(round((time.perf_counter() - start) * 1000)),
                )
            duration_ms = int(round((time.perf_counter() - start) * 1000))
            tel = update.pop("_telemetry", None) if isinstance(update, dict) else None
            if tel is not None:
                writer(
                    events.node_finished(
                        node,
                        label,
                        duration_ms,
                        model=tel.get("model"),
                        prompt_tokens=int(tel.get("prompt_tokens", 0)),
                        completion_tokens=int(tel.get("completion_tokens", 0)),
                        cost_usd=float(tel.get("cost_usd", 0.0)),
                    )
                )
            else:
                writer(events.node_finished(node, label, duration_ms))
            return update

        return wrapper

    return decorator


def build_agent(
    deps: AgentDeps, *, checkpointer: Any = None  # noqa: ANN401 - BaseCheckpointSaver
) -> CompiledStateGraph:
    """Compile the agent graph, closing over injected ``deps``.

    Args:
        deps: The capabilities (gateway, retrieval, guardrails, tools, memory) the
            nodes call. Injecting fakes here drives the whole graph offline.
        checkpointer: The LangGraph checkpoint store the graph compiles against
            (required for the human gate's ``interrupt``/resume). Defaults to an
            :class:`~langgraph.checkpoint.memory.InMemorySaver`; a host injects a
            shared/durable saver so a run parked on one compiled graph resumes by
            ``thread_id`` from any other.

    Returns:
        A compiled graph bound to ``checkpointer``.
    """
    config = deps.config

    def _persona(state: AgentState) -> str:
        """Return the persona id for ``state`` (falling back to the configured default)."""
        return state.get("persona") or config.default_persona_id

    def _retrieval_scope(state: AgentState) -> RetrievalScope:
        """Build the retrieval scope for ``state`` from the request's tenant + persona.

        This is the single place the graph turns "who is asking" into the value object
        the retrieval path requires. The tenant comes from the same
        ``deps.current_tenant_id()`` the answer cache has always used — the isolation was
        already available here; it just was not being passed on.
        """
        tenant = deps.current_tenant_id()
        return RetrievalScope(
            tenant_id=tenant,
            persona=state.get("persona"),
            corpus_version=corpus_version(tenant),
        )

    def _cache_scope(state: AgentState) -> str:
        """Build the answer-cache partition key from tenant + persona + role + corpus.

        Folding tenant, persona and specialist role into one opaque scope guarantees a
        cached answer can never be served across tenants/personas/roles (a correctness +
        isolation requirement, not an optimisation).

        Two deliberate differences from the *retrieval* cache's
        :meth:`~aegis.retrieval.types.RetrievalScope.partition_key`, rather than
        accidental drift:

        * The routed specialist role is in **this** key only. Two specialists asked the
          same question produce different *answers* from the same retrieved passages, so
          the role partitions answers and would only fragment retrieval pointlessly.
        * This key is a readable colon-joined string because it is the host's opaque
          scope argument, not a Redis key segment; the retrieval cache digests its
          partition because a persona would otherwise put arbitrary bytes into a key.

        Everything that must match — the tenant and the corpus version — is derived from
        the same sources, so an ingest invalidates both caches for that tenant together.
        """
        tenant = deps.current_tenant_id()
        return (
            f"{tenant}:{state.get('persona', '')}:{state.get('agent_role', '')}"
            f":c{corpus_version(tenant)}"
        )

    async def guard_input(state: AgentState) -> dict[str, Any]:
        """Input rail: block/redact before anything reaches the model."""
        writer = get_stream_writer()
        result = await deps.check_input(state["query"])
        _stamp_guardrail(GuardStage.INPUT, result)
        writer(
            events.guardrail(
                GuardStage.INPUT, result.verdict, result.reason, **_guard_detail(result)
            )
        )
        if result.verdict is GuardVerdict.BLOCK:
            return {
                "blocked": True,
                "status": RunStatus.BLOCKED.value,
                "answer": result.reason,
            }
        return {"blocked": False, "query": result.text}

    async def route(state: AgentState) -> dict[str, Any]:
        """Supervisor: classify the turn's intent and dispatch to a specialist.

        Runs right after the input rail. It resolves the adapter roster (defensively),
        classifies the query DETERMINISTICALLY (keyword hints), escalating to a cheap-LLM
        tiebreak **only** on a genuine tie, then makes the hand-off auditable three ways:
        it writes ``agent_role``/``route_reason`` to state, emits ONE ``routing`` event
        (the visible hand-off), stamps this CHAIN/ROUTER span with the decision, and
        records a best-effort audit row. ``qa`` (the default) continues the existing
        pipeline byte-for-byte; ``memory`` is dispatched to the memory specialist.
        """
        writer = get_stream_writer()
        roster = _resolve_roster(deps)
        decision = await route_query(state["query"], roster, complete=deps.complete)
        # WIDTH, decided second and separately from WHICH specialist. The classifier
        # runs only in AUTO; an explicit width from the user is honoured exactly and
        # the classifier is SKIPPED rather than overruled after the fact, so a user who
        # asked for one lane never pays for the cheap call they were avoiding.
        depth = await decide_depth(
            state["query"],
            policy=_depth_policy(deps, state),
            complete=deps.complete,
            role_is_default=decision.role == roster.default_role,
        )
        decision = decision.with_depth(depth)
        set_span_attributes(
            {
                semconv.ROUTER_ROLE: decision.role,
                semconv.ROUTER_REASON: decision.reason,
                semconv.ROUTER_USED_LLM: decision.used_llm,
            }
        )
        # Make the hand-off an explicit, labelled A2A span so the trace shows the
        # supervisor → specialist edge (from/to/reason/protocol) as its own node.
        with span(
            SpanKind.AGENT,
            f"handoff → {decision.role}",
            attributes={
                semconv.A2A_FROM: "supervisor",
                semconv.A2A_TO: decision.role,
                semconv.A2A_REASON: decision.reason,
                semconv.A2A_PROTOCOL: "a2a",
            },
        ):
            writer(
                events.routing(
                    role=decision.role,
                    reason=decision.reason,
                    used_llm=decision.used_llm,
                    depth=decision.depth.value,
                    fanout=decision.fanout,
                    decided_by=decision.decided_by,
                )
            )
            await _record_route_audit(deps, state, decision)
        # A TEAM turn is dispatched to the fan-out rather than to the roster specialist.
        # The specialist role is kept on ``route_reason`` (which names it) — nothing
        # downstream of here needs it, and ``agent_role`` is what the graph dispatches
        # on, so overloading it is what keeps the dispatch table the single source.
        role = _TEAM_ROLE if decision.depth is Depth.TEAM else decision.role
        return {
            "agent_role": role,
            "route_reason": decision.reason,
            "team_fanout": decision.fanout,
        }

    async def plan_team(state: AgentState) -> dict[str, Any]:
        """Turn the effective width into one sub-task per roster agent.

        The width is already decided — by the classifier in Auto, or by the user in an
        explicit mode — so this node only allocates it. One cheap model call splits the
        query; a deterministic fallback gives every agent the whole query framed by its
        own remit, which is a working team rather than a degraded one.

        A roster that cannot field at least two agents degrades **loudly** onto the qa
        pipeline rather than fanning out to one agent and calling it a team.
        """
        writer = get_stream_writer()
        width = max(2, int(state.get("team_fanout") or 0))
        specs = build_team(deps, width)
        if len(specs) < 2:
            logger.warning(
                "Team dispatch asked for %d agents but the roster fielded %d; "
                "falling back to the single-pass pipeline.",
                width,
                len(specs),
            )
            writer(
                events.reasoning(
                    "No sub-agent team is available for this run; answering in one pass."
                )
            )
            return {"agent_role": _FALLBACK_ROLE, "team_degraded": True}
        # The user's durable facts, selected by the ADAPTER through the same
        # ``deps.memory.assemble`` the single-pass path uses (§5.9c). The fan-out lane
        # never reaches the ``recall_memory`` node — ``route`` dispatches it straight
        # here — so without this every sub-agent would run blind to everything the
        # platform knows about the person asking, which would make the four-agent run a
        # DOWNGRADE on the one it replaced.
        memory_delta = await _recall(state)
        tasks = await plan_team_tasks(
            state["query"], specs, deps=deps, retry=_MODEL_RETRY
        )
        for task in tasks:
            writer(
                events.agent_status(
                    agent_id=task.spec.agent_id,
                    role=task.spec.role,
                    label=task.spec.label,
                    status="queued",
                    detail=task.task,
                )
            )
        return {
            **memory_delta,
            # Plain dicts, not the specs: state is checkpointed, and a checkpoint is not
            # the place to keep live objects. ``run_team`` re-reads the roster by id.
            "team_tasks": [
                {"agent_id": t.spec.agent_id, "task": t.task} for t in tasks
            ],
            "team_degraded": False,
        }

    async def run_team_node(state: AgentState) -> dict[str, Any]:
        """Run every sub-agent concurrently and fan in to ONE state delta.

        The gather lives here, inside a single node, which is what lets the node return
        one summed ``{prompt_tokens, completion_tokens, cost_usd}`` and leaves the
        existing ``operator.add`` reducers untouched.

        Every HIGH-risk thing any lane wanted arrives as ``tool_calls`` and flows into
        the graph's ONE ``gate → approval → act`` path. No lane executed any of it.
        """
        writer = get_stream_writer()
        planned = list(state.get("team_tasks") or [])
        by_id = {
            spec.agent_id: spec
            for spec in build_team(deps, config.max_parallel_agents)
        }
        tasks = [
            TeamTask(spec=by_id[item["agent_id"]], task=item["task"])
            for item in planned
            if item.get("agent_id") in by_id
        ]
        # ONE retrieval for the run, shared by every lane (Amendment A's supply-side
        # rule): four agents must not retrieve the tenant's chunks four times.
        pool = SharedRetrievalPool(deps, state["query"], _retrieval_scope(state))
        outcome = await run_team(
            tasks,
            deps=deps,
            persona=_persona(state),
            writer=writer,
            pool=pool,
            working_memory=state.get("working_memory", ""),
            trace_id=state.get("trace_id"),
            retry=_MODEL_RETRY,
        )
        if outcome.budget_error is not None:
            # Captured from a gathered task and re-raised only AFTER fan-in, so the
            # siblings still finished and the orchestrator's existing handler ends the
            # run cleanly as blocked. The tenant's own cap is the one thing that may
            # refuse a run.
            raise outcome.budget_error
        totals = outcome.totals()
        return {
            "team_results": [r.as_dict() for r in outcome.results],
            "tool_calls": outcome.proposed_actions,
            # The SAME numbers, twice on purpose and never double-counted: the plain keys
            # are the one summed delta the ``operator.add`` reducers fold into the run,
            # and ``_telemetry`` is popped by ``_timed`` (never written to state) so the
            # node's ``node_finished`` reports the fan-out's spend instead of zero. That
            # is what makes the per-agent harness records RECONCILE against a number on
            # the wire rather than against a total only the reducers can see.
            "_telemetry": {"model": None, **totals},
            **totals,
        }

    async def synthesize(state: AgentState) -> dict[str, Any]:
        """Merge the lanes into one answer, naming contributors **and omissions**.

        Emitted with no ``agent_id``: this is supervisor-level work after fan-in, and a
        stale identity here would put the merge inside somebody's lane.
        """
        writer = get_stream_writer()
        outcome = _outcome_from_state(state)
        text, result = await synthesise(
            state["query"],
            outcome,
            deps=deps,
            persona=_persona(state),
            working_memory=state.get("working_memory", ""),
            retry=_MODEL_RETRY,
        )
        writer(
            events.synthesis(
                contributing=[
                    {"agent_id": r.agent_id, "role": r.role, "label": r.label}
                    for r in outcome.contributing
                ],
                omitted=[
                    {
                        "agent_id": r.agent_id,
                        "role": r.role,
                        "label": r.label,
                        "status": r.status.value,
                        "reason": r.error or "returned nothing usable",
                    }
                    for r in outcome.omitted
                ],
                summary=synthesis_note(outcome),
            )
        )
        update: dict[str, Any] = {
            "answer": text,
            # The merged findings become the run's context, so if the gate approves a
            # proposed action the existing ``generate`` node grounds the final answer in
            # what the agents actually found rather than in nothing.
            "context": "\n\n".join(
                f"[{r.label}] {r.findings.strip()}" for r in outcome.contributing
            ),
        }
        if result is not None:
            update["_telemetry"] = _telemetry(result)
            update.update(_accrue(result.usage))
        return update

    async def answer_memory(state: AgentState) -> dict[str, Any]:
        """Memory specialist: answer "what do you know about me" DIRECTLY from memory.

        A genuinely distinct handler — NOT a copy of qa. It skips RAG, the planner,
        the risk gate and tools entirely. Instead it recalls the subject's
        profile + facts through the SAME memory deps the qa path uses, emits the glass-box
        ``memory`` event, and grounds a direct answer in that recalled block. When memory
        is inactive (no deps / no session / no subject) it answers honestly that nothing
        is stored yet. Best-effort recall: a store failure degrades to an empty block.
        """
        persona = _persona(state)
        assembled_text = ""
        fact_ids: list[int] = []
        message_ids: list[int] = []
        tokens_used = 0
        subject = state.get("memory_subject")
        if deps.memory is not None and state.get("session_id") and subject:
            try:
                assembled = await deps.memory.assemble(
                    subject_id=subject,
                    session_id=state["session_id"],
                    persona=state.get("persona"),
                    query=state["query"],
                    query_vec=await _recall_vector(deps, state),
                )
                assembled_text = assembled.text
                fact_ids = assembled.recalled_fact_ids
                message_ids = assembled.recalled_message_ids
                tokens_used = assembled.tokens_used
            except Exception:  # noqa: BLE001 - recall is best-effort; never fail the run
                logger.warning(
                    "Memory specialist recall unavailable; answering from empty memory",
                    exc_info=True,
                )
        # Glass-box: same event the qa recall node emits, so the memory read is visible.
        get_stream_writer()(
            events.memory(
                recalled_fact_count=len(fact_ids),
                recalled_message_count=len(message_ids),
                tokens_used=tokens_used,
            )
        )
        if assembled_text.strip():
            user_content = (
                "The user is asking what you know or remember about them. Answer ONLY "
                "from the stored memory below — do not invent facts, and if it does not "
                "cover the question say so plainly.\n\n"
                f"Stored memory about the user:\n{assembled_text}\n\n"
                f"Question: {state['query']}"
            )
        else:
            user_content = (
                "The user is asking what you know or remember about them, but there is "
                "no stored memory for them yet. Tell them honestly that you have nothing "
                "on record about them so far, and invite them to share.\n\n"
                f"Question: {state['query']}"
            )
        messages = [
            {
                "role": "system",
                "content": deps.render_system_prompt(
                    persona, extra_context=assembled_text
                ),
            },
            {"role": "user", "content": user_content},
        ]
        result = await deps.complete(ModelRole.GENERATION, messages)
        return {
            "answer": result.content,
            "working_memory": assembled_text,
            "recalled_fact_ids": fact_ids,
            "recalled_message_ids": message_ids,
            "_telemetry": _telemetry(result),
            **_accrue(result.usage),
        }

    async def retrieve(state: AgentState) -> dict[str, Any]:
        """Agentic retrieval: fetch context and stream the graph delta.

        Depending on config this is either a single-shot retrieval (today's behaviour),
        a single retrieval preceded by a context-aware query rewrite, or the bounded
        Self-RAG/FLARE loop (retrieve → judge → reformulate → re-retrieve, merging
        evidence). Whichever ran, the DOWNSTREAM stream/provenance emissions and the
        returned ``context``/``graph``/``query_vec`` are on the final merged result, so
        the qa pipeline is unchanged from here on.
        """
        writer = get_stream_writer()
        writer(events.retrieval("started"))
        # The tenant boundary for every retrieval this node performs, built once so the
        # single-shot, rewrite and agentic branches provably share one scope.
        scope = _retrieval_scope(state)
        # Rewriter history: prefer the REAL conversation transcript recalled by
        # ``recall_memory`` (the node immediately upstream). ``messages`` is only a
        # per-planning-round scratch buffer written by ``plan`` — which runs after this
        # node — so on the memory path it is always empty here and pronoun/ellipsis
        # resolution would never fire. It stays as the fallback so the single-shot /
        # no-memory path is byte-identical to before (both empty → ``None``).
        history = state.get("conversation") or state.get("messages") or None
        if config.agentic_retrieval_enabled:
            # Bounded agentic loop; the rewriter (if enabled) resolves the entry query
            # against conversation history before round 1.
            if config.query_rewrite_enabled:

                async def rewrite_fn(q: str, *, history=history):  # noqa: ANN001, ANN202
                    return await rewrite_query(q, history=history, complete=deps.complete)

            else:
                rewrite_fn = None
            agentic = await agentic_retrieve(
                state["query"],
                retrieve_fn=deps.retrieve,
                complete=deps.complete,
                rewrite_fn=rewrite_fn,
                history=history,
                max_rounds=config.agentic_retrieval_max_rounds,
                scope=scope,
            )
            result = agentic.result
            rounds = [
                {
                    "query": r.query,
                    "num_candidates": r.num_candidates,
                    "sufficient": r.sufficient,
                }
                for r in agentic.rounds
            ]
            rewritten_query = agentic.rounds[0].query if agentic.rounds else state["query"]
            # Internal rewrite+judge spend from the loop (accrued into telemetry below).
            retrieval_usage = agentic.usage
        elif config.query_rewrite_enabled:
            rw = await rewrite_query(
                state["query"], history=history, complete=deps.complete
            )
            rewritten_query = rw.rewritten if rw.changed else state["query"]
            result = await deps.retrieve(rewritten_query, scope=scope)
            rounds = []
            retrieval_usage = rw.usage
        else:
            rewritten_query = state["query"]
            result = await deps.retrieve(state["query"], scope=scope)
            rounds = []
            retrieval_usage = CallUsage()  # no internal model call on the plain path
        # Stamp the RETRIEVER node span (opened by ``_timed``) with the query and the
        # honest recall funnel (N candidates → K sources) so Phoenix shows them.
        set_span_attributes(
            {
                semconv.RETRIEVAL_QUERY: rewritten_query,
                semconv.RETRIEVAL_RESULT_COUNT: len(result.sources),
                semconv.RETRIEVAL_CANDIDATE_COUNT: int(result.num_candidates),
                semconv.RETRIEVAL_CACHE_HIT: bool(result.cache_hit),
                semconv.RETRIEVAL_ROUNDS: len(rounds) or 1,
                semconv.RETRIEVAL_REWRITTEN: rewritten_query.strip()
                != state["query"].strip(),
            }
        )
        # Glass-box: make the retrieval-intelligence behaviour visible in the live trace
        # (real consumption of what used to be write-only state). Surface a context-aware
        # rewrite, and any agentic re-retrieval with its follow-up queries.
        if rewritten_query.strip() != state["query"].strip():
            writer(
                events.reasoning(
                    f"Rewrote the query for retrieval: {rewritten_query!r}"
                )
            )
        if len(rounds) > 1:
            followups = ", ".join(repr(r["query"]) for r in rounds[1:])
            writer(
                events.reasoning(
                    f"Agentic retrieval ran {len(rounds)} rounds "
                    f"(first round judged insufficient); follow-up queries: {followups}"
                )
            )
        nodes = [n.model_dump() for n in result.graph_delta.nodes]
        edges = [e.model_dump() for e in result.graph_delta.edges]
        scored = [
            {"id": s.id, "label": _snippet(s.text), "score": s.score}
            for s in result.sources
        ]
        # Wide-recall pool size (N recalled), then the reranked, scored survivors
        # (K). num_candidates is the honest pre-rerank count, not len(sources).
        writer(events.retrieval("candidates", num_candidates=result.num_candidates))
        writer(events.retrieval("reranked", scored_sources=scored))
        writer(
            events.retrieval(
                "done",
                num_candidates=result.num_candidates,
                scored_sources=scored,
                nodes=nodes,
                edges=edges,
            )
        )
        # Honest provenance (§4.3): emit where the context came from. The Retrieval
        # agent populates ``result.provenance`` in parallel; an empty default here is
        # fine until it does.
        writer(events.provenance(result.provenance, cache_hit=result.cache_hit))
        return {
            "context": result.answer_context,
            "cache_hit": result.cache_hit,
            "graph_nodes": nodes,
            "graph_edges": edges,
            # Surface the query embedding for the "free" episodic write reuse — only a
            # real gateway vector (dim EMBED_DIM); None on cache-exact / lite / 256-dim.
            "query_vec": result.query_vec,
            # Accrue the loop's internal rewrite+judge spend into the run's per-run
            # telemetry so cost_usd/tokens reflect reality (the span attrs + reasoning
            # events above carry the rewrite/rounds provenance — no write-only state).
            **_accrue(retrieval_usage),
        }

    async def recall_memory(state: AgentState) -> dict[str, Any]:
        """Silent long-term-memory READ node (inert unless memory + a session are active).

        Wired **plain** (not via ``_timed``) so it emits NOTHING when inactive — the
        golden single-shot trace is byte-for-byte unchanged. When active it assembles the
        working-memory block, writes it (+ recalled ids) into state, and emits ONE
        ``memory`` glass-box event. Best-effort: a store failure degrades to no recall.

        The body is :func:`_recall` because the fan-out lane needs the SAME recall: a
        sub-agent that cannot see the user's durable facts is a worse agent than the
        single one it replaced, and a second recall path is how the two would drift.
        """
        return await _recall(state)

    async def _recall(state: AgentState) -> dict[str, Any]:
        """Assemble the user's working memory for this turn (the ONE recall path).

        Called by the ``recall_memory`` node on the single-pass path and by ``plan_team``
        on the fan-out path. The selection is the **adapter's** — ``deps.memory.assemble``
        reaches ``memory_spec.render_profile`` / ``select_skills`` — so there is exactly
        one selector in the codebase, for the same reason there is one tool-allowlist
        intersection.
        """
        if deps.memory is None or state.get("session_id") is None:
            return {}
        subject = state.get("memory_subject")
        if subject is None:
            return {}
        try:
            assembled = await deps.memory.assemble(
                subject_id=subject,
                session_id=state["session_id"],
                persona=state.get("persona"),
                query=state["query"],
                query_vec=await _recall_vector(deps, state),
            )
        except Exception:  # noqa: BLE001 - recall is best-effort; never fail the run
            logger.warning("Memory recall unavailable; continuing without it",
                           exc_info=True)
            return {}
        get_stream_writer()(
            events.memory(
                recalled_fact_count=len(assembled.recalled_fact_ids),
                recalled_message_count=len(assembled.recalled_message_ids),
                tokens_used=assembled.tokens_used,
            )
        )
        return {
            "working_memory": assembled.text,
            "recalled_fact_ids": assembled.recalled_fact_ids,
            "recalled_message_ids": assembled.recalled_message_ids,
            # The real multi-turn transcript for the pre-retrieval query rewriter, which
            # runs in ``retrieve`` immediately after this node. ``state["messages"]`` can
            # never serve it: that key is a per-planning-round scratch buffer written by
            # ``plan``, i.e. strictly AFTER retrieve, so it is empty at rewrite time.
            # ``getattr`` because ``MemoryDeps`` is structural — a host facade that
            # predates this field simply yields no history (today's behaviour).
            "conversation": list(getattr(assembled, "conversation", None) or []),
        }

    async def persist_memory(state: AgentState) -> dict[str, Any]:
        """Silent long-term-memory WRITE node (inert unless memory + a session are active).

        Wired **plain** (not via ``_timed``) so it emits NOTHING at all — the trace is
        unchanged. When active it persists the user + assistant turns and cadence-fires
        consolidation off the hot path. Best-effort: a store failure is logged, never
        raised, so the stream always finishes cleanly.
        """
        if deps.memory is None or state.get("session_id") is None:
            return {}
        subject = state.get("memory_subject")
        if subject is None:
            return {}
        try:
            await deps.memory.persist(
                subject_id=subject,
                session_id=state["session_id"],
                turn_index=state.get("turn_index", 0),
                user_text=state["query"],
                assistant_text=state.get("answer", ""),
                query_vec=state.get("query_vec"),
                run_id=state.get("run_id"),
                trace_id=state.get("trace_id"),
            )
        except Exception:  # noqa: BLE001 - persist is best-effort; never fail the run
            logger.warning("Memory persist failed; turn not stored", exc_info=True)
        return {}

    async def plan(state: AgentState) -> dict[str, Any]:
        """Reason over the retrieved context and propose action tool calls.

        Before doing any of that, a qa turn first consults the answer-level semantic
        cache: on a hit the expensive generation call is skipped entirely and the cached
        answer short-circuits planning (the output rail still runs). The cache is scoped
        per tenant + persona + role so an answer can never cross those boundaries. The
        lookup is skipped on a self-repair re-plan (a retry means the first answer was
        insufficient) and whenever no real query embedding is available.
        """
        writer = get_stream_writer()
        persona = _persona(state)
        if (
            config.answer_cache_enabled
            and deps.answer_cache is not None
            and state.get("agent_role") == "qa"
            and not state.get("reflect_retry")
            and state.get("query_vec")
        ):
            try:
                hit = await deps.answer_cache.get(
                    state["query_vec"], scope=_cache_scope(state)
                )
            except Exception:  # noqa: BLE001 - cache read is best-effort; never fail a run
                logger.warning("Answer cache read failed; planning normally",
                               exc_info=True)
                hit = None
            if hit is not None:
                set_span_attributes(
                    {
                        semconv.ANSWER_CACHE_HIT: True,
                        semconv.ANSWER_CACHE_SIMILARITY: float(hit.similarity),
                    }
                )
                return {
                    "messages": [],
                    "tool_calls": [],
                    "answer": hit.answer,
                    "answer_cached": True,
                    "plan_iterations": 1,  # reducer-summed (operator.add)
                }
        user_content = (
            f"Context:\n{state.get('context', '')}\n\n"
            f"Question: {state['query']}"
        )
        # Self-repair: on a re-plan, feed back the previous attempt's failed/insufficient
        # outcomes so the planner can correct course (Reflexion-style reflection input).
        prior = state.get("tool_results")
        if prior and state.get("reflect_retry"):
            attempts = "\n".join(
                f"- {r['summary']} ({'ok' if r['ok'] else 'FAILED'})" for r in prior
            )
            user_content += (
                "\n\nA previous action attempt did not fully achieve the goal:\n"
                f"{attempts}\n"
                "Reconsider and propose a corrected next action, or answer directly "
                "if no further action can help."
            )
        messages = [
            {
                "role": "system",
                "content": deps.render_system_prompt(
                    persona, extra_context=state.get("working_memory", "")
                ),
            },
            {"role": "user", "content": user_content},
        ]
        tools = deps.tool_definitions_for(persona)
        result = await deps.complete(
            ModelRole.GENERATION, messages, tools=tools or None
        )
        # Glass-box: surface the planner's reasoning/plan text, chunked by sentence.
        for sentence in _sentences(result.content):
            writer(events.reasoning(sentence))
        tool_calls = [
            {"id": tc.id, "name": tc.name, "args": tc.args} for tc in result.tool_calls
        ]
        update: dict[str, Any] = {
            "messages": messages,
            "model": result.model,
            "tool_calls": tool_calls,
            # Self-repair counter: one increment per planning round (hard-capped by
            # config.max_plan_iterations in the reflect node's re-plan decision).
            "plan_iterations": 1,  # reducer-summed (operator.add)
            "_telemetry": _telemetry(result),
            **_accrue(result.usage),
        }
        if not tool_calls:
            update["answer"] = result.content
        return update

    async def gate(state: AgentState) -> dict[str, Any]:
        """Decide the human gate from the proposed action's **tool risk**.

        Gates when a proposed action's risk tier is at or above
        ``config.gate_min_risk`` (the money-shot: a HIGH-risk action pauses for a
        human). ``ToolSpec.risk`` is the only input — the declared risk of the tool
        the planner chose. There is no second, softer signal that can also gate, which
        is why an unregistered tool name is resolved to HIGH by the host's
        ``tool_risk``: the gate can only be escaped by a tool that declares itself safe.
        """
        calls = state.get("tool_calls", [])
        risk_of = {c["id"]: deps.tool_risk(c["name"]) for c in calls}
        top_risk = max(risk_of.values(), default=RiskLevel.LOW, key=risk_rank)

        gated = any(risk_at_least(r, config.gate_min_risk) for r in risk_of.values())
        reason = f"Proposed action is {top_risk.value}-risk." if gated else ""

        return {
            "gated": gated,
            "gate_reason": reason,
            "gate_risk": top_risk.value,
        }

    def _gated_call(state: AgentState) -> dict[str, Any]:
        """Return the representative (highest-risk) call for the gate."""
        calls = state.get("tool_calls", [])
        return max(calls, key=lambda c: risk_rank(deps.tool_risk(c["name"])), default={})

    async def approval(state: AgentState) -> dict[str, Any]:
        """Human-in-the-loop gate: pause via ``interrupt`` until a decision.

        No events are emitted before ``interrupt`` because the node re-executes on
        resume; the orchestrator emits ``approval_required`` from the interrupt
        value exactly once.
        """
        call = _gated_call(state)
        decision = interrupt(
            {
                "action": call.get("name", "unknown"),
                "args": call.get("args", {}),
                "risk": state.get("gate_risk", RiskLevel.LOW.value),
                "rationale": state.get("gate_reason", "Approval required."),
            }
        )
        return {
            "approved": bool(decision.get("approved")),
            "approver": decision.get("approver"),
        }

    async def act(state: AgentState) -> dict[str, Any]:
        """Execute the approved (or low-risk) tool calls, auditing each."""
        writer = get_stream_writer()
        persona = _persona(state)
        results: list[dict[str, Any]] = []
        for call in state.get("tool_calls", []):
            risk = deps.tool_risk(call["name"])
            writer(events.tool_call(call["id"], call["name"], call["args"], risk))
            # One TOOL span per execution so the trace shows each action as a
            # nested tool node (name + risk + ok), no-op when untraced.
            with span(
                SpanKind.TOOL,
                f"tool.{call['name']}",
                attributes={
                    semconv.TOOL_NAME: call["name"],
                    semconv.TOOL_RISK: risk.value,
                },
            ) as tool_span:
                try:
                    outcome = await deps.run_tool(
                        persona,
                        call["name"],
                        call["args"],
                        actor=persona,
                        model=state.get("model"),
                        trace_id=state.get("trace_id"),
                        approver=state.get("approver"),
                    )
                    ok, summary = outcome.ok, outcome.summary
                except Exception as exc:  # noqa: BLE001 - surface any tool failure as an event
                    ok, summary = False, f"Tool error: {exc}"
                tool_span.set_attribute(semconv.TOOL_OK, bool(ok))
            writer(events.tool_result(call["id"], ok, summary))
            results.append({"call_id": call["id"], "ok": ok, "summary": summary})
        return {"tool_results": results}

    async def reflect(state: AgentState) -> dict[str, Any]:
        """Bounded self-repair (Reflexion-style): judge the outcome and maybe re-plan.

        Domain-agnostic decision, driven by the executed :class:`ToolOutcome` results
        (``.ok``/``.summary``) — never hardcoded domain logic. The goal is judged met
        when every action in the latest round succeeded. If an action failed or was
        insufficient **and** the iteration budget (``config.max_plan_iterations``)
        still allows another round, the graph loops back to ``plan`` (which re-gates
        on risk and acts again). Otherwise — goal met, or the hard cap reached — it
        proceeds to ``generate``. The counter is incremented in ``plan``, so this node
        can only ever *reduce* the remaining budget: the loop is guaranteed to
        terminate. A ``reflection`` event is streamed for the glass-box UI.
        """
        writer = get_stream_writer()
        results = state.get("tool_results", [])
        iteration = state.get("plan_iterations", 0)
        budget = config.max_plan_iterations

        done = bool(results) and all(r["ok"] for r in results)
        budget_left = iteration < budget
        # Self-repair master switch: when disabled, the reflect node still runs and
        # reports the outcome but NEVER routes back to plan (a single linear pass).
        will_retry = config.self_repair_enabled and (not done) and budget_left
        # A team run has no ``plan`` round to go back TO — its answer came from the
        # fan-out and the synthesis, not from the planner — so re-planning would drop
        # the synthesis and answer the question a second time, single-pass. The loop is
        # therefore closed here rather than at the edge alone, so the reported decision
        # and the routed decision cannot disagree.
        if _is_team_run(state):
            will_retry = False

        if _is_team_run(state):
            reason = (
                "team run: the synthesis is the answer; the self-repair loop does not "
                "re-plan a fan-out."
            )
        elif done:
            reason = "goal met: every action succeeded."
        elif not config.self_repair_enabled:
            reason = (
                "self-repair disabled; finalising with the best available result "
                "(no re-plan)."
            )
        elif not budget_left:
            reason = (
                f"iteration budget exhausted ({iteration}/{budget}); "
                "finalising with the best available result."
            )
        else:
            reason = (
                "an action failed or was insufficient; re-planning "
                f"(round {iteration}/{budget})."
            )

        writer(
            events.reflection(
                iteration=iteration,
                max_iterations=budget,
                done=done,
                will_retry=will_retry,
                reason=reason,
            )
        )
        return {"reflect_retry": will_retry}

    async def generate(state: AgentState) -> dict[str, Any]:
        """Compose the final answer from context and any tool results."""
        if state.get("answer") and not state.get("tool_results"):
            return {}  # planner already answered (pure Q&A, no actions).

        persona = _persona(state)
        outcome_lines = "\n".join(
            f"- {r['summary']}" for r in state.get("tool_results", [])
        )
        if state.get("gated") and not state.get("approved"):
            outcome_lines = "The proposed action was NOT approved by the human gate."
        messages = [
            {
                "role": "system",
                "content": deps.render_system_prompt(
                    persona, extra_context=state.get("working_memory", "")
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Context:\n{state.get('context', '')}\n\n"
                    f"Question: {state['query']}\n\n"
                    f"Actions taken:\n{outcome_lines or 'none'}\n\n"
                    "Write the final answer for the user; ground every claim in the "
                    "retrieved context and the actions above."
                ),
            },
        ]
        result = await deps.complete(ModelRole.GENERATION, messages)
        return {
            "answer": result.content,
            "_telemetry": _telemetry(result),
            **_accrue(result.usage),
        }

    async def guard_output(state: AgentState) -> dict[str, Any]:
        """Output rail: scan the complete answer before it is streamed.

        On the way out it also POPULATES the answer-level semantic cache: a clean,
        freshly-generated qa answer (not itself served from cache, no tool actions, not
        gated, and with a real query embedding) is stored under the tenant+persona+role
        scope so a future equivalent question can skip the generation call. A BLOCKed
        answer is never cached, and the write is best-effort (a cache failure never
        breaks the run).
        """
        writer = get_stream_writer()
        # Ground the answer against the SAME retrieved context it was generated from
        # (state["context"], set by the retrieve node from result.answer_context) — not
        # a re-fetch. Empty context ⇒ no contexts ⇒ the grounding rail is a no-op PASS.
        # The output rail emits an advisory FLAG (verdict="flag", layer="grounding") that
        # streams via events.guardrail below, so the console can surface "ungrounded".
        answer_context = state.get("context", "")
        contexts = [answer_context] if answer_context.strip() else None
        result = await deps.check_output(state.get("answer", ""), contexts=contexts)
        _stamp_guardrail(GuardStage.OUTPUT, result)
        writer(
            events.guardrail(
                GuardStage.OUTPUT, result.verdict, result.reason, **_guard_detail(result)
            )
        )
        if result.verdict is GuardVerdict.BLOCK:
            final_answer = None  # withheld answers are never cached
            update: dict[str, Any] = {
                "answer": "[response withheld by the output guardrail]"
            }
        elif result.verdict is GuardVerdict.REDACT:
            final_answer = result.text
            update = {"answer": result.text}
        else:
            final_answer = state.get("answer", "")
            update = {}

        if (
            final_answer
            and config.answer_cache_enabled
            and deps.answer_cache is not None
            and state.get("agent_role") == "qa"
            and not state.get("answer_cached")
            and not state.get("tool_results")
            and not state.get("gated")
            and state.get("query_vec")
        ):
            try:
                await deps.answer_cache.set(
                    query=state["query"],
                    embedding=state["query_vec"],
                    answer=final_answer,
                    scope=_cache_scope(state),
                    sources=None,
                )
            except Exception:  # noqa: BLE001 - cache write must never break a run
                logger.warning("Answer cache write failed; answer not cached",
                               exc_info=True)
        return update

    async def stream_answer(state: AgentState) -> dict[str, Any]:
        """Emit the already-guarded answer to the client in word chunks.

        **This is not token streaming from the model, and deliberately so.** The
        answer is generated in full by ``generate``, cleared by ``guard_output``,
        and only then chunked onto the SSE socket here. The chunks are real
        transport-level streaming — the client renders progressively — but they are
        paced out of a finished string, not produced token-by-token by the gateway.

        The ordering is the reason. ``generate -> guard_output -> stream`` means no
        model output reaches the user until the output rail has passed it. Streaming
        raw tokens as the model produced them would put unguarded text on screen and
        make a block unenforceable after the fact — you cannot unsay a leaked
        secret. That trade (a cosmetic typing effect for a real safety property) is
        not one this platform makes.

        If true token streaming is ever wanted, it needs a streaming-aware output
        rail — incremental scanning with the ability to withhold — not just a
        streaming gateway call.
        """
        writer = get_stream_writer()
        answer = state.get("answer", "")
        words = answer.split()
        step = max(1, config.stream_chunk_words)
        for i in range(0, len(words), step):
            chunk = " ".join(words[i : i + step])
            suffix = " " if i + step < len(words) else ""
            writer(events.token(chunk + suffix))
        return {"status": RunStatus.COMPLETED.value}

    # Transient-failure policy for the nodes whose bodies are a network call to the
    # model gateway. LangGraph's ``default_retry_on`` already limits this to transient
    # classes (connection/timeout/5xx), so a deterministic failure still surfaces
    # immediately instead of being retried three times.
    #
    # Deliberately NOT applied to:
    #   ``act``      - executes real, externally-visible tool actions. Exactly-once is
    #                  guaranteed by the approvals DB lock, not by the graph; retrying
    #                  here could update a request's status twice.
    #   ``approval`` - re-executes on resume by design; a retry would re-interrupt.
    #   memory nodes - already best-effort with their own degrade-to-nothing path.
    _MODEL_RETRY = RetryPolicy(max_attempts=3)

    # ── Wiring ───────────────────────────────────────────────────────────────
    # Every node body is wrapped to time it and emit node_started/node_finished.
    # ``approval`` is the exception: it re-executes on interrupt/resume, so the
    # orchestrator emits its node_started once from the interrupt value instead.
    builder: StateGraph = StateGraph(AgentState)
    builder.add_node(
        "guard_input",
        _timed("guard_input", NODE_LABELS["guard_input"], SpanKind.GUARDRAIL)(guard_input),
    )
    # Supervisor router: classify intent → dispatch to a specialist. Wrapped in
    # ``_timed`` so it stamps a CHAIN/ROUTER span and reports its own node timing; its
    # single ``routing`` event is the visible hand-off.
    builder.add_node(
        "route", _timed("route", NODE_LABELS["route"], retry=_MODEL_RETRY)(route)
    )
    # Memory specialist: answers self-referential turns straight from long-term memory,
    # skipping RAG/plan/gate/act. A genuinely distinct handler, not a qa copy.
    builder.add_node(
        "answer_memory",
        _timed("answer_memory", NODE_LABELS["answer_memory"], retry=_MODEL_RETRY)(
            answer_memory
        ),
    )
    # Memory nodes are wired PLAIN (not via ``_timed``): a ``_timed`` wrapper emits
    # node_started/node_finished even on a no-op, which would break the golden trace.
    # A plain node that returns ``{}`` emits nothing, so the single-shot stream is
    # byte-for-byte identical to today when memory is inactive (BACKWARD-COMPAT).
    builder.add_node("recall_memory", recall_memory)
    builder.add_node("persist_memory", persist_memory)
    # The adaptive fan-out. Three nodes, one lane of the graph: allocate the width,
    # run the agents concurrently INSIDE ``run_team``, merge. It lands on the existing
    # gate → approval → act tail, so the human gate is still the only way a
    # consequential action happens and there is still exactly one of it.
    builder.add_node(
        "plan_team",
        _timed("plan_team", NODE_LABELS["plan_team"], retry=_MODEL_RETRY)(plan_team),
    )
    # Deliberately NO retry policy: retrying a whole fan-out re-runs every lane that
    # already succeeded, at N times the cost, to recover one that did not. The per-lane
    # retry inside ``run_subagent`` is the right granularity, and it is already wired.
    builder.add_node(
        "run_team",
        _timed("run_team", NODE_LABELS["run_team"], SpanKind.AGENT)(run_team_node),
    )
    builder.add_node(
        "synthesize",
        _timed("synthesize", NODE_LABELS["synthesize"], retry=_MODEL_RETRY)(synthesize),
    )
    builder.add_node(
        "retrieve",
        _timed(
            "retrieve", NODE_LABELS["retrieve"], SpanKind.RETRIEVER, retry=_MODEL_RETRY
        )(retrieve),
    )
    builder.add_node(
        "plan", _timed("plan", NODE_LABELS["plan"], retry=_MODEL_RETRY)(plan)
    )
    builder.add_node("gate", _timed("gate", NODE_LABELS["gate"])(gate))
    builder.add_node("approval", approval)
    builder.add_node("act", _timed("act", NODE_LABELS["act"])(act))
    builder.add_node("reflect", _timed("reflect", NODE_LABELS["reflect"])(reflect))
    builder.add_node(
        "generate",
        _timed("generate", NODE_LABELS["generate"], retry=_MODEL_RETRY)(generate),
    )
    builder.add_node(
        "guard_output",
        _timed("guard_output", NODE_LABELS["guard_output"], SpanKind.GUARDRAIL)(guard_output),
    )
    builder.add_node("stream", _timed("stream", NODE_LABELS["stream"])(stream_answer))

    builder.add_edge(START, "guard_input")
    # guard_input → route (a blocked input short-circuits straight to END, so the router
    # never runs on a blocked run and the blocked golden trace is unchanged).
    builder.add_conditional_edges(
        "guard_input",
        lambda s: END if s.get("blocked") else "route",
        {END: END, "route": "route"},
    )
    # Supervisor dispatch: the memory intent goes to the memory specialist; everything
    # else (the qa default, and any unknown role) falls through to the existing pipeline
    # via recall_memory — so the qa path is byte-identical to before.
    # Roster-driven dispatch: the path map is derived from SPECIALIST_NODES rather
    # than hardcoding the memory/qa binary, so adding a specialist is a table entry
    # plus a node, and an unmapped role is warned about instead of swallowed.
    builder.add_conditional_edges(
        "route",
        _route_specialist,
        {node: node for node in sorted(set(SPECIALIST_NODES.values()))},
    )
    # Memory specialist finalises through the SAME output rail + stream + persist tail as
    # qa, so the answer is guarded and the turn is still written to long-term memory.
    builder.add_edge("answer_memory", "guard_output")
    # plan_team → run_team, unless the roster could not field a team: that degrades
    # onto the qa pipeline (loudly, with a reasoning event) rather than pretending a
    # single agent is a fan-out.
    builder.add_conditional_edges(
        "plan_team",
        lambda s: "recall_memory" if s.get("team_degraded") else "run_team",
        {"run_team": "run_team", "recall_memory": "recall_memory"},
    )
    builder.add_edge("run_team", "synthesize")
    # The team path joins the EXISTING tail at the gate: one gate, always, whatever
    # proposed the action. This edge is the whole security argument of the phase.
    builder.add_edge("synthesize", "gate")
    builder.add_edge("recall_memory", "retrieve")
    builder.add_edge("retrieve", "plan")
    builder.add_conditional_edges(
        "plan",
        lambda s: "gate" if s.get("tool_calls") else "generate",
        {"gate": "gate", "generate": "generate"},
    )
    builder.add_conditional_edges(
        "gate",
        _route_gate,
        {"approval": "approval", "act": "act"},
    )
    builder.add_conditional_edges(
        "approval",
        lambda s: "act" if s.get("approved") else "generate",
        {"act": "act", "generate": "generate"},
    )
    # After acting, reflect: loop back to plan for a bounded self-repair round, or
    # finalise. The counter incremented in ``plan`` (capped by max_plan_iterations)
    # guarantees termination.
    builder.add_edge("act", "reflect")
    builder.add_conditional_edges(
        "reflect",
        _route_reflect,
        {"plan": "plan", "generate": "generate"},
    )
    builder.add_edge("generate", "guard_output")
    builder.add_edge("guard_output", "stream")
    # stream → persist_memory → END (persist_memory is a silent pass-through when memory
    # is inactive: it emits nothing, so ``stream`` remains the last event as it is today).
    builder.add_edge("stream", "persist_memory")
    builder.add_edge("persist_memory", END)

    _warn_unroutable_specialists(deps)
    return builder.compile(checkpointer=checkpointer or InMemorySaver())


def _depth_policy(deps: AgentDeps, state: AgentState) -> DepthPolicy:
    """Build the width policy for this turn from the request and the tenant's config.

    This is the seam Phase 6's composer mode control writes to: it sets ``depth_mode``
    (and, in Custom, ``requested_fanout``) on the run, and the rule is one line —
    ``effective_depth = user_mode if user_mode != AUTO else classifier_decision``.

    **The failure default is SINGLE on both paths.** An unreadable mode resolves to
    :attr:`DepthMode.SINGLE`, not to AUTO: a settings resolver that cannot be read must
    not hand the decision to a classifier, because the manual path must never introduce
    a second, more permissive default than the automatic one.

    Team availability is read from the roster itself, not from a flag: a host that has
    not declared sub-agents cannot fan out, whatever anybody asked for.
    """
    raw = state.get("depth_mode")
    if raw is None:
        mode = DepthMode.AUTO
    else:
        try:
            mode = DepthMode(str(raw))
        except ValueError:
            logger.warning(
                "Unknown depth mode %r on run %s; defaulting to SINGLE.",
                raw,
                state.get("run_id"),
            )
            mode = DepthMode.SINGLE
    requested = state.get("requested_fanout")
    return DepthPolicy(
        mode=mode,
        requested_fanout=int(requested) if requested else None,
        max_parallel_agents=deps.config.max_parallel_agents,
        team_enabled=deps.config.team_enabled,
        # Read from the roster itself, not from a flag: a host that never declared
        # sub-agents cannot fan out, whatever anybody asked for.
        available_agents=len(build_team(deps, deps.config.max_parallel_agents)),
    )


def _outcome_from_state(state: AgentState) -> TeamOutcome:
    """Rebuild the fan-out's results from the checkpointed ``team_results`` rows.

    ``run_team`` writes plain dicts to state (a checkpoint is no place for live
    objects); the synthesis needs them back as results so ``contributing``/``omitted``
    is computed in ONE place rather than re-derived from raw dicts here.
    """
    results = []
    for row in state.get("team_results") or []:
        try:
            status = SubAgentStatus(row.get("status", SubAgentStatus.OK.value))
        except ValueError:  # pragma: no cover - defensive against a hand-edited row
            status = SubAgentStatus.FAILED
        results.append(
            SubAgentResult(
                agent_id=str(row.get("agent_id", "")),
                role=str(row.get("role", "")),
                label=str(row.get("label", "")),
                status=status,
                findings=str(row.get("findings", "") or ""),
                proposed_actions=list(row.get("proposed_actions") or []),
                tool_calls=list(row.get("tool_calls") or []),
                steps=int(row.get("steps", 0) or 0),
                error=row.get("error"),
                prompt_tokens=int(row.get("prompt_tokens", 0) or 0),
                completion_tokens=int(row.get("completion_tokens", 0) or 0),
                cost_usd=float(row.get("cost_usd", 0.0) or 0.0),
            )
        )
    return TeamOutcome(results=results)


def _resolve_roster(deps: AgentDeps) -> Any:  # noqa: ANN401 - AgentRoster duck-type
    """Return the supervisor roster from ``deps``, degrading to the core fallback.

    Reads the injected ``agent_roster`` hook defensively so a run never fails on a
    missing/misbehaving adapter contract: any error yields the core's ``qa``-only
    :func:`load_roster` fallback (the supervisor then only routes qa).
    """
    try:
        roster = deps.agent_roster()
        if roster is not None and roster.roles():
            return roster
    except Exception:  # noqa: BLE001 - roster is an optional adapter contract
        logger.warning("Agent roster hook failed; falling back to qa-only", exc_info=True)
    return load_roster()


async def _record_route_audit(
    deps: AgentDeps, state: AgentState, decision: Any  # noqa: ANN401 - RouterDecision
) -> None:
    """Persist a best-effort audit row for the supervisor hand-off (never fails the run).

    The routing decision is already visible on the wire (the ``routing`` event) and in
    the trace (the ROUTER span); this adds a durable accountability row through the
    injected ``deps.record_audit`` sink when the host wired one (already gated on
    whether a durable store is configured). A missing sink → a silent no-op; a write
    failure is logged, never raised.
    """
    if deps.record_audit is None:
        return
    try:
        await deps.record_audit(
            action="router:route",
            actor=state.get("persona"),
            model=None,
            trace_id=state.get("trace_id"),
            payload={
                "role": decision.role,
                "reason": decision.reason,
                "used_llm": decision.used_llm,
                "query": state.get("query", ""),
            },
        )
    except Exception:  # noqa: BLE001 - audit is best-effort at this seam
        logger.warning("Router audit write failed", exc_info=True)


async def _recall_vector(deps: AgentDeps, state: AgentState) -> list[float] | None:
    """Return the query embedding memory recall should rank semantic facts against.

    Prefers a vector already in state, then falls back to the injected
    ``embed_query`` hook. Both memory branches need this: ``recall_memory`` runs
    **upstream** of ``retrieve`` (the only node that sets ``query_vec``), and
    ``answer_memory`` sits on a branch that never reaches ``retrieve`` at all — so
    before this helper existed both always passed ``query_vec=None`` and
    ``assemble`` silently degraded to recency-only facts.

    Best-effort by design: no hook, or a failing hook, returns ``None`` and recall
    degrades exactly as it did before rather than failing the run.
    """
    existing = state.get("query_vec")
    if existing:
        return existing
    if deps.embed_query is None:
        return None
    try:
        return await deps.embed_query(state["query"])
    except Exception:  # noqa: BLE001 - recall is best-effort; never fail the run
        logger.warning("Query embedding for memory recall failed", exc_info=True)
        return None


def _accrue(usage: Any) -> dict[str, Any]:  # noqa: ANN401
    """Return ONE model call's token/cost contribution as a state delta.

    The three keys carry ``operator.add`` reducers (see :mod:`aegis.agent.state`), so
    a node returns only what its own call spent and LangGraph sums it into the run
    total. This deliberately replaces an earlier read-modify-write over ``state``:
    reading the running total and returning ``total + delta`` is correct only while
    no two nodes ever run in the same superstep, and silently loses one branch's
    spend the moment anything runs in parallel.
    """
    return {
        "prompt_tokens": int(usage.prompt_tokens),
        "completion_tokens": int(usage.completion_tokens),
        "cost_usd": float(usage.cost_usd),
    }


def _telemetry(result: Any) -> dict[str, Any]:  # noqa: ANN401 - LLMResult duck-type
    """Extract per-call model/usage from an ``LLMResult`` for ``node_finished``."""
    usage = result.usage
    return {
        "model": result.model or None,
        "prompt_tokens": int(usage.prompt_tokens),
        "completion_tokens": int(usage.completion_tokens),
        "cost_usd": float(usage.cost_usd),
    }


def _snippet(text: str, *, limit: int = 80) -> str:
    """Return a short, single-line snippet of ``text`` for a scored-source label."""
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def _stamp_guardrail(stage: GuardStage, result: Any) -> None:  # noqa: ANN401 - GuardResult
    """Stamp the current GUARDRAIL node span with the rail's stage/verdict/layer.

    A no-op when no tracer is configured (writes hit a non-recording span).
    """
    set_span_attributes(
        {
            semconv.GUARDRAIL_STAGE: stage.value,
            semconv.GUARDRAIL_VERDICT: result.verdict.value,
            semconv.GUARDRAIL_LAYER: getattr(result, "layer", None),
        }
    )


def _guard_detail(result: Any) -> dict[str, Any]:  # noqa: ANN401 - GuardResult duck-type
    """Map a ``GuardResult`` into the extra ``guardrail`` event fields.

    Only masked/redacted text is surfaced — never raw PII. ``before_masked`` and
    ``after`` are populated only on a ``redact`` verdict (both carry the masked
    text); ``layer`` and ``redactions`` come straight off the rail result.
    """
    redactions = list(getattr(result, "redactions", []) or [])
    masked = result.text if redactions else None
    return {
        "layer": getattr(result, "layer", None),
        "redactions": redactions,
        "before_masked": masked,
        "after": masked,
    }
