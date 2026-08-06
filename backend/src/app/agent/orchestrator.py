"""Drive the agent graph and expose it as a single stamped event stream.

:func:`run_agent` is the one coroutine the API layer consumes. It runs the
LangGraph with ``astream(stream_mode=["custom", "updates"])``, forwarding each
node-emitted event after stamping it with the run id and a monotonic sequence
number, and it owns the human-in-the-loop rendezvous:

1. On the first interrupt it emits ``approval_required`` and **registers** the
   gate in the :class:`~app.agent.approvals.ApprovalRegistry` *before* awaiting,
   so a fast ``POST /approval`` can never race past the wait.
2. When the decision arrives it resumes the same checkpointed graph with
   ``Command(resume=...)`` and keeps streaming into the still-open SSE response.

The stream is bookended by ``run_started`` and ``run_finished`` (carrying the
final usage/cost/cache-hit), and any failure is surfaced as an ``error`` event
followed by a terminal ``run_finished``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from langgraph.types import Command

from app.api.schemas import (
    ApprovalDecision,
    ApprovalDecisionResponse,
    RiskLevel,
    RunStatus,
    StreamEvent,
)
from app.core.llm import BudgetExceededError
from app.observability import get_tracer, semconv
from app.observability.otel import current_trace_id

from . import events
from .approvals import ApprovalRegistry, get_approval_registry, get_parked_runs
from .deps import AgentDeps
from .graph import build_agent

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Post-run trace-eval kickoff (the Eval + Observe stage of the LLM-Ops loop)
#
# After a run reaches its terminal ``run_finished`` we grade it OFF the hot path,
# mirroring the memory-consolidation pattern in ``agent.deps``: a tracked
# ``create_task`` (so the loop cannot GC it mid-flight) with a done-callback that
# surfaces any swallowed exception. The grade opens its own DB session and writes
# ``EvalResult`` rows via ``app.ops.trace_eval.evaluate_run``. It is best-effort:
# a failure is logged, never raised, and it never blocks or delays the stream.
# ─────────────────────────────────────────────────────────────────────────────

#: Live post-run trace-eval tasks, kept referenced so the event loop cannot GC one
#: mid-flight; the done-callback logs any exception (honest over fire-and-forget).
_TRACE_EVAL_TASKS: set[asyncio.Task[Any]] = set()


def _on_trace_eval_done(task: asyncio.Task[Any]) -> None:
    """Discard a finished trace-eval task and surface any swallowed exception."""
    _TRACE_EVAL_TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:  # pragma: no cover - defensive logging of a background failure
        logger.warning("Background post-run trace-eval failed", exc_info=exc)


def _eval_tenant_id() -> int | None:
    """Return the request's tenant id from the governance context (``None`` if unset)."""
    try:
        from app.core.governance import get_governance_context

        gov = get_governance_context()
        return gov.tenant_id if gov is not None else None
    except Exception:  # noqa: BLE001 - governance is optional at this seam
        return None


def _build_eval_steps(
    final: dict[str, Any], guardrail_events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Project the final run state + guardrail events into trace-eval ``steps``.

    Mirrors the graph's OTel span kinds: the retrieval that produced ``context`` →
    ``RETRIEVER``; each executed tool result → ``TOOL`` (carrying ``detail.ok``); each
    guardrail verdict emitted during the run → ``GUARDRAIL``.
    """
    steps: list[dict[str, Any]] = []

    context = str(final.get("context") or "")
    if context.strip():
        steps.append(
            {
                "node": "retrieve",
                "kind": "RETRIEVER",
                "detail": {"contexts": [context]},
            }
        )

    for result in final.get("tool_results") or []:
        steps.append(
            {
                "node": result.get("call_id") or "tool",
                "kind": "TOOL",
                "detail": {
                    "ok": bool(result.get("ok", True)),
                    "tool": result.get("call_id"),
                    "summary": result.get("summary"),
                },
            }
        )

    for event in guardrail_events:
        steps.append(
            {
                "node": event.get("stage") or "guardrail",
                "kind": "GUARDRAIL",
                "detail": {
                    "stage": event.get("stage"),
                    "verdict": event.get("verdict"),
                    "input": event.get("reason"),
                },
            }
        )

    return steps


async def _run_trace_eval(
    *,
    run_id: str,
    query: str,
    final: dict[str, Any],
    guardrail_events: list[dict[str, Any]],
) -> None:
    """Grade one completed run on its own DB session (off the hot path).

    Opens a tenant-scoped session, builds the trajectory ``steps`` from the real run
    state + guardrail verdicts, and writes ``EvalResult`` rows via ``evaluate_run`` with
    the live ``app.core.llm.complete`` judge. ``evaluate_run`` flushes but does not
    commit — the transaction boundary is ours, so we commit here.
    """
    from app.core.llm import complete
    from app.data.session import get_sessionmaker, set_tenant_scope
    from app.ops.trace_eval import evaluate_run

    tenant_id = _eval_tenant_id()
    context = str(final.get("context") or "")
    contexts = [context] if context.strip() else []
    steps = _build_eval_steps(final, guardrail_events)

    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, tenant_id)
        await evaluate_run(
            session,
            run_id=run_id,
            query=query,
            answer=str(final.get("answer") or ""),
            contexts=contexts,
            steps=steps,
            complete=complete,
            prompt_key=(str(final.get("persona")) if final.get("persona") else None),
            tenant_id=tenant_id,
        )
        await session.commit()


def _fire_trace_eval(
    *,
    run_id: str,
    query: str,
    final: dict[str, Any],
    guardrail_events: list[dict[str, Any]],
) -> None:
    """Schedule the post-run grade off the hot path (tracked; never blocks the stream).

    Gated on ``settings.stores_enabled`` — writing ``EvalResult`` needs the DB, so the
    offline "lite" demo (no database) skips it silently. Scheduling failures are
    swallowed: the grade must never disturb the run's terminal event.
    """
    try:
        from app.config import get_settings

        if not get_settings().stores_enabled:
            return
        task = asyncio.create_task(
            _run_trace_eval(
                run_id=run_id,
                query=query,
                final=final,
                guardrail_events=guardrail_events,
            )
        )
        _TRACE_EVAL_TASKS.add(task)
        task.add_done_callback(_on_trace_eval_done)
    except Exception:  # noqa: BLE001 - the kickoff must never disturb the stream
        logger.warning("Post-run trace-eval kickoff failed for %s", run_id, exc_info=True)


async def run_agent(
    query: str,
    *,
    persona: str | None = None,
    role: str | None = None,
    deps: AgentDeps | None = None,
    registry: ApprovalRegistry | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    memory_subject: str | None = None,
) -> AsyncIterator[StreamEvent]:
    """Run one query end-to-end, yielding the ordered stream of events.

    Args:
        query: The user's question.
        persona: The adapter persona id scoping data and tools.
        role: The caller's RBAC role (recorded on the run).
        deps: Injected capabilities; defaults to the live wiring.
        registry: The approval rendezvous; defaults to the process-wide registry.
        run_id: An explicit run id; a random one is minted when omitted.
        session_id: Conversation/session id for multi-turn memory. ``None`` (default)
            keeps the run single-shot — the memory nodes stay inert and the stream is
            identical to today.
        memory_subject: The adapter-resolved subject memory is scoped to (the app-level
            isolation key). ``None`` disables memory for this run.

    Yields:
        Validated :data:`~app.api.schemas.StreamEvent` variants in wire order.
    """
    deps = deps or AgentDeps.default()
    registry = registry or get_approval_registry()
    run_id = run_id or uuid4().hex
    graph = _durable_graph(deps)
    config: dict[str, Any] = {"configurable": {"thread_id": run_id}}

    seq = 0

    # Guardrail verdicts are emitted as stream events (not retained in graph state);
    # collect them so the post-run trace-eval can grade the GUARDRAIL trajectory steps.
    guardrail_events: list[dict[str, Any]] = []

    def emit(payload: dict[str, Any]) -> StreamEvent:
        nonlocal seq
        event = events.stamp(payload, run_id=run_id, seq=seq)
        seq += 1
        return event

    tracer = get_tracer()
    with tracer.start_as_current_span("agent.run") as run_span:
        # Root of the trace tree: mark it AGENT so Phoenix nests every child span
        # (nodes, retrieval, guardrails, tools, LLM/embedding calls) beneath it.
        run_span.set_attribute(
            semconv.OPENINFERENCE_SPAN_KIND, semconv.SpanKind.AGENT.value
        )
        trace_id = current_trace_id() or run_id
        yield emit(events.run_started(trace_id))

        stream_input: Any = {
            "run_id": run_id,
            "trace_id": trace_id,
            "query": query,
            "persona": persona,
            "role": role,
            # Long-term memory seeds (all None/0 on the single-shot path → nodes inert).
            "session_id": session_id,
            "memory_subject": memory_subject,
            "turn_index": 0,
            "messages": [],
            "tool_calls": [],
            "tool_results": [],
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cost_usd": 0.0,
        }

        try:
            while True:
                interrupt_value: dict[str, Any] | None = None
                async for mode, chunk in graph.astream(
                    stream_input, config, stream_mode=["custom", "updates"]
                ):
                    if mode == "custom":
                        if isinstance(chunk, dict) and chunk.get("type") == "guardrail":
                            guardrail_events.append(dict(chunk))
                        yield emit(chunk)
                    elif mode == "updates" and _is_interrupt(chunk):
                        interrupt_value = chunk["__interrupt__"][0].value

                if interrupt_value is None:
                    break

                # Human gate. Register the notify future BEFORE emitting so a fast
                # decision can never race past the wait.
                approval_id = uuid4().hex
                registry.register(approval_id)
                action = str(interrupt_value.get("action", "unknown"))
                args = dict(interrupt_value.get("args", {}))
                risk = RiskLevel(interrupt_value.get("risk", RiskLevel.LOW.value))
                rationale = str(interrupt_value.get("rationale", ""))

                # Persist the durable inbox row — the source of truth for the paused
                # run — and retain a resumable handle so an out-of-band decision can
                # continue this run from its checkpoint if the socket parks.
                sla_deadline = await _enqueue_gate(
                    approval_id,
                    run_id=run_id,
                    trace_id=trace_id,
                    persona=persona,
                    action=action,
                    args=args,
                    risk=risk,
                    rationale=rationale,
                    ml_snapshot=_ml_snapshot(graph, config),
                )
                get_parked_runs().register(run_id, graph, config)

                yield emit(events.node_started("approval", "Human approval gate"))
                yield emit(
                    events.approval_queued(
                        approval_id,
                        action=action,
                        args=args,
                        risk=risk,
                        rationale=rationale,
                        sla_deadline=sla_deadline,
                        assignee_tier=_default_tier(),
                    )
                )
                yield emit(
                    events.approval_required(
                        approval_id,
                        action=action,
                        args=args,
                        risk=risk,
                        rationale=rationale,
                    )
                )
                try:
                    outcome = await registry.wait(
                        approval_id, timeout=deps.config.approval_park_timeout
                    )
                except TimeoutError:
                    # Park: the run is NOT lost — it survives as a durable PENDING row
                    # plus a checkpoint, to be resumed out-of-band from the inbox.
                    logger.info("Run %s parked awaiting approval %s", run_id, approval_id)
                    yield emit(events.run_finished(RunStatus.AWAITING_APPROVAL))
                    return
                stream_input = Command(
                    resume={"approved": outcome.approved, "approver": outcome.approver}
                )

            # Live run completed within the socket — drop the resumable handle.
            get_parked_runs().pop(run_id)
            final = graph.get_state(config).values
            status = RunStatus(final.get("status", RunStatus.COMPLETED.value))
            yield emit(
                events.run_finished(
                    status,
                    prompt_tokens=int(final.get("prompt_tokens", 0)),
                    completion_tokens=int(final.get("completion_tokens", 0)),
                    cost_usd=float(final.get("cost_usd", 0.0)),
                    cache_hit=bool(final.get("cache_hit", False)),
                )
            )
            # Terminal run reached: grade it OFF the hot path (tracked task; best-effort).
            # This never blocks or delays the stream — the run_finished above is already
            # yielded — and a failure is logged, never raised.
            _fire_trace_eval(
                run_id=run_id,
                query=query,
                final=dict(final),
                guardrail_events=guardrail_events,
            )
        except BudgetExceededError as exc:
            # A per-tenant/user cap tripped at the LiteLLM chokepoint before spend:
            # end the run cleanly as blocked, not a crash (§3.3).
            logger.info("Agent run %s blocked by budget: %s", run_id, exc.message)
            get_parked_runs().pop(run_id)
            yield emit(
                events.budget_exceeded(
                    scope=exc.scope,
                    scope_id=exc.scope_id,
                    limit_type=exc.limit_type,
                    limit=exc.limit,
                    used=exc.used,
                    message=exc.message,
                )
            )
            yield emit(events.run_finished(RunStatus.BLOCKED))
        except Exception as exc:  # noqa: BLE001 - report any failure as an event
            logger.exception("Agent run %s failed", run_id)
            yield emit(events.error(str(exc)))
            yield emit(events.run_finished(RunStatus.ERROR))


def _is_interrupt(chunk: Any) -> bool:  # noqa: ANN401 - opaque astream chunk
    """Return whether an ``updates`` chunk carries a LangGraph interrupt."""
    return isinstance(chunk, dict) and "__interrupt__" in chunk


def _durable_graph(deps: AgentDeps) -> Any:  # noqa: ANN401 - CompiledStateGraph
    """Compile the agent graph bound to the process-wide durable checkpoint store.

    The graph *topology* comes from :func:`build_agent`; we re-bind it (via the public
    ``builder`` seam) to the shared checkpointer from
    :func:`app.data.session.get_agent_checkpointer`, so every run in the process — and,
    with the ``PostgresSaver``, every worker — checkpoints into ONE store. That shared
    store is what lets a *fresh* graph resume a parked run by ``thread_id`` without the
    originating in-process ``ParkedRun`` handle (finding #8). No graph node logic is
    touched; only the checkpointer it is compiled with.
    """
    from app.data.session import get_agent_checkpointer

    return build_agent(deps).builder.compile(checkpointer=get_agent_checkpointer())


def _ml_snapshot(graph: Any, config: dict[str, Any]) -> dict[str, Any]:  # noqa: ANN401
    """Return the ML explanation frozen in graph state at gate time (or ``{}``)."""
    try:
        return dict(graph.get_state(config).values.get("ml") or {})
    except Exception:  # noqa: BLE001 - snapshot is best-effort metadata
        return {}


def _default_tier() -> str:
    """Return the default approver tier for a freshly queued gate."""
    from app.config import get_settings

    return get_settings().approval_default_tier


async def _enqueue_gate(
    approval_id: str,
    *,
    run_id: str,
    trace_id: str | None,
    persona: str | None,
    action: str,
    args: dict[str, Any],
    risk: RiskLevel,
    rationale: str,
    ml_snapshot: dict[str, Any],
) -> str | None:
    """Persist the durable approvals-inbox row (best-effort), returning its SLA ISO.

    Best-effort so the offline "lite" demo (no database) still runs the live gate:
    a write failure is logged, the ``approval_queued`` event simply carries no SLA
    deadline, and the in-process notify path resolves the gate.

    Returns:
        The row's ``sla_deadline`` as an ISO string, or ``None`` if the write failed.
    """
    try:
        from app.core.governance import get_governance_context
        from app.data import enqueue_approval

        # Stamp the owning tenant from the per-request governance context so the
        # durable row can be tenant-scoped for the inbox and decision paths (C1).
        gov = get_governance_context()
        tenant_id = gov.tenant_id if gov is not None else None

        row = await enqueue_approval(
            approval_id=approval_id,
            run_id=run_id,
            action=action,
            args=args,
            risk=risk,
            rationale=rationale,
            ml_snapshot=ml_snapshot,
            persona=persona,
            trace_id=trace_id,
            tenant_id=tenant_id,
        )
        return row.sla_deadline
    except Exception:  # noqa: BLE001 - durable inbox is best-effort at the edge
        logger.warning("Approvals-inbox enqueue failed for %s", approval_id, exc_info=True)
        return None


async def decide_approval(
    approval_id: str,
    decision: ApprovalDecision,
    *,
    approver: str | None = None,
    registry: ApprovalRegistry | None = None,
    deps: AgentDeps | None = None,
) -> ApprovalDecisionResponse:
    """Resolve a gated action through the one shared path (live gate + async inbox).

    Both models converge here:

    1. **Durable transition.** :func:`app.data.resolve_approval` flips the row under an
       optimistic ``PENDING → RESUMING/REJECTED`` lock; only the winner proceeds, so a
       replayed decision is a no-op (idempotency).
    2. **Notify the live socket.** :meth:`ApprovalRegistry.resolve` wakes a still-open
       ``/query`` run instantly (the money-shot demo). When it does, that live run
       executes the tool — so the async resumer must stand down.
    3. **Async resume.** If no live waiter exists (the run parked), an approve resumes
       the run headless from its checkpoint; the ``approval_id``-keyed ``RESUMING`` lock
       guarantees the tool runs exactly once.

    Args:
        approval_id: The gate to resolve.
        decision: The human's verdict.
        approver: Who decided (recorded on the durable row and audit trail).
        registry: The notify cache; defaults to the process-wide registry.
        deps: Capabilities used to rebuild the graph when an async resume must
            rehydrate a parked run on a fresh worker; defaults to the live wiring.

    Returns:
        An :class:`ApprovalDecisionResponse` — ``accepted`` is ``True`` only when this
        call effected the decision (durably or by waking a live socket).
    """
    registry = registry or get_approval_registry()
    resolution = await _safe_resolve(approval_id, decision, approver)
    live_woken = registry.resolve(approval_id, decision, approver=approver)

    won = bool(resolution and resolution.won)
    accepted = won or live_woken
    status = resolution.status if resolution else None
    run_id = resolution.run_id if resolution else None

    if won and decision is ApprovalDecision.APPROVE and not live_woken:
        # The run parked: continue it out-of-band from the checkpoint (in-process
        # handle if present, else rehydrated by ``thread_id`` on a fresh worker).
        resumed = await resume_parked_run(
            approval_id, run_id, decision, approver=approver, deps=deps
        )
        if resumed:
            status = "approved"
    elif won and decision is ApprovalDecision.APPROVE and live_woken:
        # A still-open /query socket is executing the approved action right now, under
        # the SAME PENDING→RESUMING lock (so exactly-once execution holds). Finalize
        # the durable row to APPROVED — previously the live path left it stuck in
        # RESUMING forever, an audit defect for the inbox.
        await _safe_finalize(approval_id)
        status = "approved"
        if run_id:
            get_parked_runs().pop(run_id)
    elif (decision is ApprovalDecision.REJECT or live_woken) and run_id:
        # Rejected, or a live socket is executing it — drop any resumable handle.
        get_parked_runs().pop(run_id)

    return ApprovalDecisionResponse(
        id=approval_id, status=status or "unknown", accepted=accepted
    )


async def resume_parked_run(
    approval_id: str,
    run_id: str | None,
    decision: ApprovalDecision,
    *,
    approver: str | None = None,
    deps: AgentDeps | None = None,
) -> bool:
    """Continue a parked run from its durable checkpoint and drive it to completion.

    Two entry conditions converge on the *same* checkpoint-driven resume:

    1. **In-process handle present** (the run parked on *this* worker). The retained
       :class:`~app.agent.approvals.ParkedRun` handle is consumed and its graph resumed.
    2. **No handle** (a *fresh* worker — after a restart, or a different process took
       the decision). We **rehydrate**: rebuild the compiled graph bound to the shared
       durable checkpointer (:func:`_durable_graph`) and resume the run *by
       ``thread_id == run_id``* straight from the checkpoint — the real cross-worker
       path (finding #8). If the shared store holds no resumable checkpoint for that
       ``thread_id`` (e.g. a truly separate process on the in-memory saver, with no
       Postgres), there is nothing to rehydrate and we report ``False``.

    Either way the graph is resumed with ``Command(resume=...)`` and the gated tool
    executes **exactly once**: the caller has already won the optimistic ``PENDING →
    RESUMING`` transition keyed by ``approval_id``, so no second decision reaches here
    even across processes. On completion the row is finalised to ``APPROVED``.

    Args:
        approval_id: The gate id (the tool idempotency key) to finalise.
        run_id: The parked run to resume (``thread_id``); a no-op when ``None``.
        decision: The resolved decision to feed the gate node.
        approver: Who approved, threaded onto the audited action.
        deps: Capabilities used to rebuild the graph on the rehydrate path; defaults
            to the live wiring (:meth:`AgentDeps.default`).

    Returns:
        ``True`` if the run was resumed to completion (via handle *or* rehydration);
        ``False`` when there is nothing resumable.
    """
    if run_id is None:
        return False

    resume_cmd = Command(
        resume={"approved": decision is ApprovalDecision.APPROVE, "approver": approver}
    )

    handle = get_parked_runs().pop(run_id)
    if handle is not None:
        graph, config = handle.graph, handle.config
    else:
        # Fresh worker: no in-process handle. Rebuild the graph on the shared durable
        # checkpointer and resume from the checkpoint keyed by ``thread_id``.
        graph = _durable_graph(deps or AgentDeps.default())
        config = {"configurable": {"thread_id": run_id}}
        if not graph.get_state(config).next:
            # No resumable checkpoint in the shared store for this thread — a real
            # cross-process resume needs the durable (Postgres) saver to be enabled.
            logger.info(
                "No resumable checkpoint for run %s; cannot rehydrate", run_id
            )
            return False

    try:
        async for _mode, _chunk in graph.astream(
            resume_cmd, config, stream_mode=["custom", "updates"]
        ):
            pass  # drive headless to completion; the tool runs exactly once
    except Exception:  # noqa: BLE001 - a resume failure must not crash the decision
        logger.exception("Headless resume of run %s failed", run_id)
        return False
    await _safe_finalize(approval_id)
    return True


async def _safe_resolve(
    approval_id: str, decision: ApprovalDecision, approver: str | None
) -> Any:  # noqa: ANN401 - app.data.ApprovalResolution (lazy import)
    """Run the durable optimistic transition, tolerating an absent database."""
    try:
        from app.data import resolve_approval

        return await resolve_approval(approval_id, decision, approver)
    except Exception:  # noqa: BLE001 - durable inbox is best-effort at the edge
        logger.warning("Approval resolve failed for %s", approval_id, exc_info=True)
        return None


async def _safe_finalize(approval_id: str) -> None:
    """Finalise a resumed row to ``APPROVED`` (best-effort)."""
    try:
        from app.data import finalize_resumed

        await finalize_resumed(approval_id)
    except Exception:  # noqa: BLE001 - best-effort status bookkeeping
        logger.warning("Approval finalize failed for %s", approval_id, exc_info=True)
