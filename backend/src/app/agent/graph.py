"""The plan-and-execute LangGraph with a bounded self-repair loop and a human gate.

The graph makes the agent's plan *visible and auditable*: each node is one step in
the money-shot trace (guardrail → retrieve → ml_predict → plan → gate → act →
reflect → guardrail → answer). Nodes emit :data:`~app.api.schemas.StreamEvent`
payloads through the LangGraph **custom stream writer**; the orchestrator stamps and
forwards them to the SSE client.

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

**ML is a solution signal, never a flow decider (founder decision).** An optional,
best-effort ``ml_predict`` step runs *before* planning: when the adapter resolves a
subject for the query it produces a calibrated prediction + interval + top SHAP
drivers, which are injected into the planner and the final answer as *supporting
evidence* — the answer can cite "the model predicts X (90% coverage)". No subject /
no adapter model → the agent answers normally with zero ML involvement. A failed or
low-confidence prediction is simply omitted; ML never blocks, defers, or terminates.

The ``gate`` node's human-in-the-loop pause is driven by **tool risk only**: a
proposed action at or above ``config.gate_min_risk`` routes to the ``approval`` node,
which calls :func:`langgraph.types.interrupt` and pauses on a checkpointer until
``POST /approval`` resumes it (the money-shot). ML uncertainty is *not* a gate.

Verified against LangGraph 1.2 (``StateGraph``, ``add_conditional_edges``,
``interrupt`` / ``Command``, ``InMemorySaver``, ``get_stream_writer`` and
``astream(stream_mode=["custom", "updates"])``), August 2026.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from app.adapter import DEFAULT_PERSONA_ID
from app.api.schemas import (
    GuardStage,
    GuardVerdict,
    MLExplainResponse,
    RiskLevel,
    RunStatus,
)
from app.core.models import ModelRole
from app.observability import SpanKind, semconv, set_span_attributes, span

from . import events
from .deps import (
    AgentDeps,
    risk_at_least,
    risk_rank,
)
from .router import route_query
from .state import AgentState

logger = logging.getLogger(__name__)


def _persona(state: AgentState) -> str:
    """Return the persona id for ``state`` (falling back to the default)."""
    return state.get("persona") or DEFAULT_PERSONA_ID


# Splits plan text into sentences for chunked ``reasoning`` events (no true
# token-streaming needed — just a few demoable chunks).
_SENTENCE = re.compile(r"[^.!?]+[.!?]*")


def _sentences(text: str) -> list[str]:
    """Split ``text`` into trimmed, non-empty sentence chunks."""
    return [s.strip() for s in _SENTENCE.findall(text) if s.strip()]


def _route_gate(state: AgentState) -> str:
    """Route out of the ``gate`` node by the risk-driven gating decision.

    Returns:
        ``"approval"`` to route a risky action to the human gate, or ``"act"`` to
        execute a within-ceiling action autonomously. ML never routes here.
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
    return "plan" if state.get("reflect_retry") else "generate"


_NodeBody = Callable[[AgentState], Awaitable[dict[str, Any]]]


def _timed(
    node: str, label: str, kind: SpanKind = SpanKind.CHAIN
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
                update = await body(state)
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


def build_agent(deps: AgentDeps) -> CompiledStateGraph:
    """Compile the agent graph, closing over injected ``deps``.

    Args:
        deps: The capabilities (gateway, retrieval, guardrails, ML, tools) the
            nodes call. Injecting fakes here drives the whole graph offline.

    Returns:
        A compiled graph bound to the process-wide checkpointer (required for the
        human gate's ``interrupt``/resume). The single checkpoint store is provided
        by :func:`app.data.session.get_agent_checkpointer`, so a run parked on one
        compiled graph resumes by ``thread_id`` from any other.
    """
    from app.data.session import get_agent_checkpointer

    config = deps.config

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
        set_span_attributes(
            {
                semconv.ROUTER_ROLE: decision.role,
                semconv.ROUTER_REASON: decision.reason,
                semconv.ROUTER_USED_LLM: decision.used_llm,
            }
        )
        writer(
            events.routing(
                role=decision.role, reason=decision.reason, used_llm=decision.used_llm
            )
        )
        await _record_route_audit(state, decision)
        return {"agent_role": decision.role, "route_reason": decision.reason}

    async def answer_memory(state: AgentState) -> dict[str, Any]:
        """Memory specialist: answer "what do you know about me" DIRECTLY from memory.

        A genuinely distinct handler — NOT a copy of qa. It skips RAG, the ML signal,
        the planner, the risk gate and tools entirely. Instead it recalls the subject's
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
                    query_vec=state.get("query_vec"),
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
            **_accrue(state, result.usage),
        }

    async def retrieve(state: AgentState) -> dict[str, Any]:
        """Agentic retrieval: fetch context and stream the graph delta."""
        writer = get_stream_writer()
        writer(events.retrieval("started"))
        result = await deps.retrieve(state["query"], persona=state.get("persona"))
        # Stamp the RETRIEVER node span (opened by ``_timed``) with the query and the
        # honest recall funnel (N candidates → K sources) so Phoenix shows them.
        set_span_attributes(
            {
                semconv.RETRIEVAL_QUERY: state["query"],
                semconv.RETRIEVAL_RESULT_COUNT: len(result.sources),
                semconv.RETRIEVAL_CANDIDATE_COUNT: int(result.num_candidates),
                semconv.RETRIEVAL_CACHE_HIT: bool(result.cache_hit),
            }
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
        }

    async def recall_memory(state: AgentState) -> dict[str, Any]:
        """Silent long-term-memory READ node (inert unless memory + a session are active).

        Wired **plain** (not via ``_timed``) so it emits NOTHING when inactive — the
        golden single-shot trace is byte-for-byte unchanged. When active it assembles the
        working-memory block, writes it (+ recalled ids) into state, and emits ONE
        ``memory`` glass-box event. Best-effort: a store failure degrades to no recall.
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
                query_vec=state.get("query_vec"),
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

    async def ml_predict(state: AgentState) -> dict[str, Any]:
        """Optional, best-effort ML **solution signal** (adapter-driven, non-blocking).

        When the adapter resolves a subject/feature row for the query, run the
        ensemble spine to obtain a calibrated prediction + interval + SHAP drivers,
        storing both the raw response and a domain-framed summary that later nodes
        inject into the planner and the answer as *supporting evidence*. This is
        purely additive: no subject / no adapter model → the run proceeds with zero
        ML. Any failure is swallowed (logged) so ML can never block or terminate the
        run — the agent still answers.
        """
        features = deps.features_for(state["query"], state.get("persona"))
        if not (config.run_ml and features):
            return {}
        try:
            resp: MLExplainResponse = deps.predict_explain(features)
            return {"ml_response": resp, "ml_summary": deps.describe_prediction(resp)}
        except Exception:  # noqa: BLE001 - ML is best-effort; never fail the run
            logger.warning("ML solution signal unavailable; continuing without it",
                           exc_info=True)
            return {}

    async def plan(state: AgentState) -> dict[str, Any]:
        """Reason over the context + ML prediction and propose action tool calls."""
        writer = get_stream_writer()
        persona = _persona(state)
        user_content = (
            f"Context:\n{state.get('context', '')}\n\n"
            f"Question: {state['query']}"
        )
        ml_summary = state.get("ml_summary")
        if ml_summary:
            user_content += f"\n\n{ml_summary}"
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
            "plan_iterations": state.get("plan_iterations", 0) + 1,
            "_telemetry": _telemetry(result),
            **_accrue(state, result.usage),
        }
        if not tool_calls:
            update["answer"] = result.content
        return update

    async def gate(state: AgentState) -> dict[str, Any]:
        """Decide the human gate on **tool risk only** — ML never gates.

        Gates when a proposed action's risk tier is at or above
        ``config.gate_min_risk`` (the money-shot: a HIGH-risk action pauses for a
        human). The ML prediction, if one ran, is surfaced as an *informational*
        ``ml_explanation`` event carrying **no gating semantics** — it is supporting
        evidence for the answer, not a routing signal.
        """
        writer = get_stream_writer()
        resp: MLExplainResponse | None = state.get("ml_response")
        calls = state.get("tool_calls", [])
        risk_of = {c["id"]: deps.tool_risk(c["name"]) for c in calls}
        top_risk = max(risk_of.values(), default=RiskLevel.LOW, key=risk_rank)

        gated = any(risk_at_least(r, config.gate_min_risk) for r in risk_of.values())
        reason = f"Proposed action is {top_risk.value}-risk." if gated else ""

        # Informational ML evidence only — no gating semantics attached.
        ml_event = None
        if resp is not None:
            ml_event = events.ml_explanation(resp)
            writer(ml_event)
        return {
            "ml": ml_event,
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
        will_retry = (not done) and budget_left

        if done:
            reason = "goal met: every action succeeded."
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
        ml_summary = state.get("ml_summary")
        ml_block = f"\n\n{ml_summary}" if ml_summary else ""
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
                    f"Question: {state['query']}"
                    f"{ml_block}\n\n"
                    f"Actions taken:\n{outcome_lines or 'none'}\n\n"
                    "Write the final answer for the user; ground any prediction or "
                    "recommendation in the ML decision-support above when present."
                ),
            },
        ]
        result = await deps.complete(ModelRole.GENERATION, messages)
        return {
            "answer": result.content,
            "_telemetry": _telemetry(result),
            **_accrue(state, result.usage),
        }

    async def guard_output(state: AgentState) -> dict[str, Any]:
        """Output rail: scan the complete answer before it is streamed."""
        writer = get_stream_writer()
        result = await deps.check_output(state.get("answer", ""))
        _stamp_guardrail(GuardStage.OUTPUT, result)
        writer(
            events.guardrail(
                GuardStage.OUTPUT, result.verdict, result.reason, **_guard_detail(result)
            )
        )
        if result.verdict is GuardVerdict.BLOCK:
            return {"answer": "[response withheld by the output guardrail]"}
        if result.verdict is GuardVerdict.REDACT:
            return {"answer": result.text}
        return {}

    async def stream_answer(state: AgentState) -> dict[str, Any]:
        """Stream the guarded answer as ``token`` events, chunked by words."""
        writer = get_stream_writer()
        answer = state.get("answer", "")
        words = answer.split()
        step = max(1, config.stream_chunk_words)
        for i in range(0, len(words), step):
            chunk = " ".join(words[i : i + step])
            suffix = " " if i + step < len(words) else ""
            writer(events.token(chunk + suffix))
        return {"status": RunStatus.COMPLETED.value}

    # ── Wiring ───────────────────────────────────────────────────────────────
    # Every node body is wrapped to time it and emit node_started/node_finished.
    # ``approval`` is the exception: it re-executes on interrupt/resume, so the
    # orchestrator emits its node_started once from the interrupt value instead.
    builder: StateGraph = StateGraph(AgentState)
    builder.add_node(
        "guard_input",
        _timed("guard_input", "Input guardrail", SpanKind.GUARDRAIL)(guard_input),
    )
    # Supervisor router: classify intent → dispatch to a specialist. Wrapped in
    # ``_timed`` so it stamps a CHAIN/ROUTER span and reports its own node timing; its
    # single ``routing`` event is the visible hand-off.
    builder.add_node("route", _timed("route", "Route intent")(route))
    # Memory specialist: answers self-referential turns straight from long-term memory,
    # skipping RAG/ml/plan/gate/act. A genuinely distinct handler, not a qa copy.
    builder.add_node(
        "answer_memory", _timed("answer_memory", "Answer from memory")(answer_memory)
    )
    # Memory nodes are wired PLAIN (not via ``_timed``): a ``_timed`` wrapper emits
    # node_started/node_finished even on a no-op, which would break the golden trace.
    # A plain node that returns ``{}`` emits nothing, so the single-shot stream is
    # byte-for-byte identical to today when memory is inactive (BACKWARD-COMPAT).
    builder.add_node("recall_memory", recall_memory)
    builder.add_node("persist_memory", persist_memory)
    builder.add_node(
        "retrieve",
        _timed("retrieve", "Agentic retrieval", SpanKind.RETRIEVER)(retrieve),
    )
    builder.add_node("ml_predict", _timed("ml_predict", "ML predict (solver)")(ml_predict))
    builder.add_node("plan", _timed("plan", "Reason & plan")(plan))
    builder.add_node("gate", _timed("gate", "Risk gate & ML evidence")(gate))
    builder.add_node("approval", approval)
    builder.add_node("act", _timed("act", "Execute actions")(act))
    builder.add_node("reflect", _timed("reflect", "Reflect & self-repair")(reflect))
    builder.add_node("generate", _timed("generate", "Generate answer")(generate))
    builder.add_node(
        "guard_output",
        _timed("guard_output", "Output guardrail", SpanKind.GUARDRAIL)(guard_output),
    )
    builder.add_node("stream", _timed("stream", "Stream answer")(stream_answer))

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
    builder.add_conditional_edges(
        "route",
        lambda s: "answer_memory" if s.get("agent_role") == "memory" else "recall_memory",
        {"answer_memory": "answer_memory", "recall_memory": "recall_memory"},
    )
    # Memory specialist finalises through the SAME output rail + stream + persist tail as
    # qa, so the answer is guarded and the turn is still written to long-term memory.
    builder.add_edge("answer_memory", "guard_output")
    builder.add_edge("recall_memory", "retrieve")
    # Predict-then-plan: ML runs before the planner so the plan is conditioned on it.
    builder.add_edge("retrieve", "ml_predict")
    builder.add_edge("ml_predict", "plan")
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

    return builder.compile(checkpointer=get_agent_checkpointer())


def _resolve_roster(deps: AgentDeps) -> Any:  # noqa: ANN401 - adapter AgentRoster duck-type
    """Return the supervisor roster from ``deps``, degrading to the core fallback.

    Reads the injected ``agent_roster`` hook defensively so a run never fails on a
    missing/misbehaving adapter contract: any error yields the core's ``qa``-only
    :func:`app.agent.router.load_roster` fallback (the supervisor then only routes qa).
    """
    from .router import load_roster

    try:
        roster = deps.agent_roster()
        if roster is not None and roster.roles():
            return roster
    except Exception:  # noqa: BLE001 - roster is an optional adapter contract
        logger.warning("Agent roster hook failed; falling back to qa-only", exc_info=True)
    return load_roster()


async def _record_route_audit(state: AgentState, decision: Any) -> None:  # noqa: ANN401 - RouterDecision
    """Persist a best-effort audit row for the supervisor hand-off (never fails the run).

    The routing decision is already visible on the wire (the ``routing`` event) and in
    the trace (the ROUTER span); this adds a durable accountability row when a store is
    configured. Gated on ``stores_enabled`` so the offline "lite"/test path skips it
    silently, and wrapped so a write failure is logged, never raised.
    """
    try:
        from app.config import get_settings

        if not get_settings().stores_enabled:
            return
        from app.data import record_audit

        await record_audit(
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


# The durable saver is a process-wide singleton: its Postgres connection stays open
# for the app's lifetime so every compiled graph shares one checkpoint store, and the
# context manager is retained so the connection is not closed by garbage collection.
_pg_checkpointer: Any = None
_pg_keepalive: list[Any] = []


def _build_postgres_checkpointer() -> Any:  # noqa: ANN401 - BaseCheckpointSaver
    """Build (once) the durable ``PostgresSaver`` bound to ``settings.postgres_dsn``.

    Lazily imports ``langgraph.checkpoint.postgres`` (so the default memory path never
    needs Postgres), opens a long-lived connection from the configured DSN, runs
    ``setup()`` to create the checkpoint tables idempotently, and caches the saver as
    a process-wide singleton. Each ``interrupt`` then checkpoints durably, so a paused
    run survives a restart or another worker and resumes by ``thread_id == run_id``.

    Returns:
        A ``PostgresSaver`` bound to ``settings.postgres_dsn``, schema initialised.

    Raises:
        RuntimeError: If ``langgraph-checkpoint-postgres`` is not installed.
    """
    global _pg_checkpointer
    if _pg_checkpointer is not None:  # pragma: no cover - prod singleton reuse
        return _pg_checkpointer

    from app.config import get_settings

    try:
        from langgraph.checkpoint.postgres import PostgresSaver
    except ImportError as exc:  # pragma: no cover - prod-only (no Postgres in tests)
        raise RuntimeError(
            "agent_checkpointer='postgres' requires the "
            "'langgraph-checkpoint-postgres' package; install it "
            "(pip install '.[agent]') or use the default 'memory' saver."
        ) from exc

    # ``from_conn_string`` is a context manager over an open connection; enter it and
    # retain the CM so the connection lives for the app's lifetime.
    cm = PostgresSaver.from_conn_string(get_settings().postgres_dsn)  # pragma: no cover
    saver = cm.__enter__()  # pragma: no cover - prod-only lifecycle
    saver.setup()  # pragma: no cover - creates checkpoint tables idempotently
    _pg_keepalive.append(cm)  # pragma: no cover
    _pg_checkpointer = saver  # pragma: no cover
    return saver  # pragma: no cover


def _accrue(state: AgentState, usage: Any) -> dict[str, Any]:  # noqa: ANN401
    """Return token/cost totals after adding one model call's ``usage``."""
    return {
        "prompt_tokens": state.get("prompt_tokens", 0) + int(usage.prompt_tokens),
        "completion_tokens": state.get("completion_tokens", 0)
        + int(usage.completion_tokens),
        "cost_usd": state.get("cost_usd", 0.0) + float(usage.cost_usd),
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
