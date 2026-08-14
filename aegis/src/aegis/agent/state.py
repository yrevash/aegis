"""The typed graph state threaded through the LangGraph agent.

``AgentState`` is the single mutable record every node reads and updates. It is a
``TypedDict`` (LangGraph's native state container) with ``total=False`` so nodes
return only the keys they change. Wire-facing structures (graph nodes/edges, tool
calls, the ML explanation) are held as plain dicts so the state stays trivially
checkpointable by the ``InMemorySaver``.

**Reducers, and why they matter.** Most keys are last-write-wins: the node that
returns the key replaces its value. The accumulator keys are different — they carry
``Annotated[..., operator.add]`` so LangGraph *adds* each node's contribution
instead of overwriting it.

This is not decoration. Every key without a reducer is only safe while no
LangGraph superstep runs two nodes at once: if two parallel nodes each returned a
last-write-wins key, one update is silently dropped, and for the totals below
LangGraph raises ``InvalidUpdateError`` ("can receive only one value per step").
The accumulators are reduced so nodes may return a **delta** and remain correct
under a fan-out.

Three keys deliberately stay last-write-wins, and all three would be wrong as
reducers:

* ``messages`` is a per-planning-round scratch buffer, rebuilt from scratch each
  time ``plan`` runs — not a transcript accumulated across nodes. Accumulating it
  would duplicate the whole prompt on every self-repair round.
* ``conversation`` is the recalled prior-turn transcript, written once by
  ``recall_memory`` from the memory layer's raw window. It is a *snapshot of what
  the store already holds*, not something nodes contribute to, so a re-run of the
  node must replace it wholesale. Accumulating it would duplicate every prior turn
  on each write and silently defeat the memory layer's turn cap.
* ``tool_results`` is replaced wholesale by ``act``. The previous round's results
  are read *before* that overwrite to build the self-repair prompt, so the data is
  used, not lost. Accumulating it would make ``reflect`` re-see an already-repaired
  failure and burn the iteration budget.

Adding a parallel branch that writes either of those keys requires giving it a
reducer first.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class AgentState(TypedDict, total=False):
    """State carried through the plan-and-execute agent graph.

    Attributes:
        run_id: Correlation id for the whole run (also the checkpointer thread id).
        trace_id: OTel trace id, surfaced in ``run_started`` for observability.
        query: The (possibly guardrail-redacted) user query.
        persona: The adapter persona id scoping data and tools.
        role: The caller's coarse RBAC role.
        messages: OpenAI-style chat buffer for ONE planning round. Rebuilt from
            scratch each time ``plan`` runs -- it is not accumulated across nodes.
            It is NOT the conversation history (``plan`` runs after ``retrieve``, so
            this key is empty for everything upstream of it) -- see ``conversation``.
        conversation: The prior turns of THIS session, oldest-first, in OpenAI chat
            shape, as recalled from long-term memory by ``recall_memory`` (already
            budget- and turn-capped by the memory layer). Absent/empty on the
            single-shot path. Consumed by ``retrieve`` as the query rewriter's
            history so pronouns/ellipsis resolve against what was actually said.
        model: The deployment id that produced the plan (for audit correlation).
        agent_role: The specialist role the supervisor routed this turn to (``qa`` →
            the full pipeline, ``memory`` → the memory specialist). Absent → ``qa``.
        route_reason: Demoable explanation of the routing decision (the hand-off reason).
        context: Spotlighted retrieval context fed to generation.
        cache_hit: Whether retrieval was served from the semantic cache.
        graph_nodes: Serialised graph nodes touched by retrieval (for the viz).
        graph_edges: Serialised graph edges touched by retrieval (for the viz).
        tool_calls: Proposed action tool calls from the planner.
        tool_results: Executed tool outcomes for the CURRENT round (replaced wholesale
            by ``act``; the prior round is read before the overwrite to build the
            self-repair prompt).
        plan_iterations: How many planning rounds have run (the self-repair counter,
            reducer-summed: ``plan`` returns 1 per execution; hard-capped by
            ``AgentConfig.max_plan_iterations``).
        reflect_retry: Whether the last ``reflect`` decided to loop back to ``plan``.
        ml: The ML spine explanation event dict for the proposed action, if any.
        ml_response: The raw ML spine response (predict-then-plan), if a subject was
            resolved — injected into the planner and re-read by the gate.
        ml_summary: Decision-support text derived from ``ml_response`` and injected
            into the planner/generate prompts (ML-as-solver).
        gated: Whether the run routed through the human-approval gate.
        gate_reason: Why the gate triggered (risk or uncertainty).
        gate_risk: The risk level of the gated action.
        approved: The human decision once the gate resolves.
        approver: Who approved/rejected at the gate.
        answer: The final answer text (post output-rail).
        session_id: The conversation/session id for multi-turn memory (None → the
            single-shot path; both memory nodes no-op and the topology is unchanged).
        memory_subject: The adapter-resolved subject memory is scoped to (default the
            authenticated user); the primary app-level isolation key.
        working_memory: The assembled long-term-memory block (profile + facts + skills
            + running summary + recalled turns) injected as extra system context.
        query_vec: The user-query embedding reused from retrieval for episodic write
            (present only when it is a real gateway vector of dim ``EMBED_DIM``).
        recalled_fact_ids: Ids of the semantic facts recalled into working memory.
        recalled_message_ids: Ids of the episodic turns recalled into working memory.
        turn_index: This turn's ordinal within the session.
        prompt_tokens: Accumulated prompt tokens across model calls (reducer-summed;
            nodes return the per-call delta, not a running total).
        completion_tokens: Accumulated completion tokens (reducer-summed delta).
        cost_usd: Accumulated USD cost across model calls (reducer-summed delta).
        status: Terminal run status value (see ``RunStatus``).
        blocked: Whether an input rail blocked the run.
    """

    run_id: str
    trace_id: str
    query: str
    persona: str
    role: str
    messages: list[dict[str, Any]]
    conversation: list[dict[str, Any]]
    model: str
    agent_role: str
    route_reason: str
    context: str
    cache_hit: bool
    answer_cached: bool
    graph_nodes: list[dict[str, Any]]
    graph_edges: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    plan_iterations: Annotated[int, operator.add]
    reflect_retry: bool
    ml: dict[str, Any] | None
    ml_response: Any
    ml_summary: str
    gated: bool
    gate_reason: str
    gate_risk: str
    approved: bool
    approver: str | None
    answer: str
    session_id: str | None
    memory_subject: str | None
    working_memory: str
    query_vec: list[float] | None
    recalled_fact_ids: list[int]
    recalled_message_ids: list[int]
    turn_index: int
    prompt_tokens: Annotated[int, operator.add]
    completion_tokens: Annotated[int, operator.add]
    cost_usd: Annotated[float, operator.add]
    status: str
    blocked: bool
