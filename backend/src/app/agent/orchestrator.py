"""Backend composition root for the agent orchestrator.

The **pure** run loop (stream + gate rendezvous) lives in
``aegis.agent.orchestrator.run_agent`` as a host-agnostic coroutine over five injected
seams. This module binds those seams to the platform's durable machinery and keeps the
durable decision glue that could not move:

- :func:`run_agent` — a thin wrapper that injects the real event-validator
  (``app.agent.events.stamp``), the shared/durable checkpointer
  (``get_agent_checkpointer``), the durable approvals-inbox writer (:func:`_enqueue_gate`),
  the post-run trace-eval hook (:func:`_fire_trace_eval`) and the approver tier.
- :func:`decide_approval` — the one shared resolve path (live gate + async inbox) over
  the durable ``app.data`` optimistic transition.
- :func:`resume_parked_run` — rehydrate a parked run from the shared checkpoint (by
  in-process handle or by ``thread_id``) and drive it headless via the core helper.

The API layer (``app.api.routes``) consumes :func:`run_agent`/:func:`decide_approval`
unchanged.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any

from aegis.agent.approvals import ApprovalRegistry
from aegis.agent.orchestrator import ResumeFailedError
from aegis.agent.orchestrator import resume_parked_run as _core_resume
from aegis.agent.orchestrator import run_agent as _core_run_agent

from app.agent.approvals import get_approval_registry, get_parked_runs
from app.agent.deps import AgentDeps
from app.api.schemas import (
    ApprovalDecision,
    ApprovalDecisionResponse,
    StreamEvent,
)

from . import events

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Post-run trace-eval kickoff (the Eval + Observe stage of the LLM-Ops loop)
#
# After a run reaches its terminal ``run_finished`` we grade it OFF the hot path,
# mirroring the memory-consolidation pattern in ``agent.deps``: a tracked
# ``create_task`` (so the loop cannot GC it mid-flight) with a done-callback that
# surfaces any swallowed exception. The grade opens its own DB session and writes
# ``EvalResult`` rows via ``app.ops.trace_eval.evaluate_run``. It is best-effort:
# a failure is logged, never raised, and it never blocks or delays the stream. It is
# wired into the core loop as the injected ``on_terminal`` hook.
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
    swallowed: the grade must never disturb the run's terminal event. This is the
    injected ``on_terminal`` hook the core loop fires after ``run_finished``.
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
    """Run one query end-to-end, yielding the ordered stream of ``StreamEvent``.

    A thin composition-root wrapper over ``aegis.agent.run_agent`` that binds the five
    injected seams to this platform's durable machinery: the locked ``StreamEvent``
    validator, the shared/durable checkpointer, the durable approvals-inbox writer, the
    post-run trace-eval hook and the approver tier.

    Args:
        query: The user's question.
        persona: The adapter persona id scoping data and tools.
        role: The caller's RBAC role (recorded on the run).
        deps: Injected capabilities; defaults to the live wiring.
        registry: The approval rendezvous; defaults to the process-wide registry.
        run_id: An explicit run id; a random one is minted when omitted.
        session_id: Conversation/session id for multi-turn memory (``None`` → single-shot).
        memory_subject: The adapter-resolved subject memory is scoped to.

    Yields:
        Validated :data:`~app.api.schemas.StreamEvent` variants in wire order.
    """
    from app.data.session import get_agent_checkpointer

    deps = deps or AgentDeps.default()
    # ``aclosing`` is load-bearing, not tidiness: when the SSE client disconnects, this
    # wrapper is closed at its ``yield`` and a bare ``async for`` would leave the inner
    # generator to garbage collection — deferring the gate cleanup that discards the
    # notify future. Closing it here propagates the disconnect immediately, so a decision
    # arriving next can never mistake a dead run's registration for a live waiter.
    async with aclosing(
        _core_run_agent(
            query,
            persona=persona,
            role=role,
            deps=deps,
            registry=registry,
            run_id=run_id,
            session_id=session_id,
            memory_subject=memory_subject,
            checkpointer=get_agent_checkpointer(),
            stamp=events.stamp,
            enqueue_approval=_enqueue_gate,
            on_terminal=_fire_trace_eval,
            default_tier=_default_tier(),
            parked_runs=get_parked_runs(),
        )
    ) as stream:
        async for event in stream:
            yield event


def _durable_graph(deps: AgentDeps) -> Any:  # noqa: ANN401 - CompiledStateGraph
    """Compile the agent graph bound to the process-wide durable checkpoint store.

    The graph topology comes from :func:`app.agent.graph.build_agent`; the shared
    checkpointer from :func:`app.data.session.get_agent_checkpointer` is what lets a
    *fresh* graph resume a parked run by ``thread_id`` without the originating
    in-process ``ParkedRun`` handle (finding #8).
    """
    from app.agent.graph import build_agent
    from app.data.session import get_agent_checkpointer

    return build_agent(deps, checkpointer=get_agent_checkpointer())


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
    risk: Any,  # noqa: ANN401 - RiskLevel
    rationale: str,
) -> str | None:
    """Persist the durable approvals-inbox row (best-effort), returning its SLA ISO.

    The durable-inbox seam ``aegis.agent``'s ``run_agent`` calls at the gate. Best-effort
    so the offline "lite" demo (no database) still runs the live gate: a write failure is
    logged, the ``approval_queued`` event simply carries no SLA deadline, and the
    in-process notify path resolves the gate.

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
    2. **Notify the live socket.** :meth:`ApprovalRegistry.notify_live` wakes a still-open
       ``/query`` run — and reports ``True`` only once that run has actually **taken**
       the outcome. A merely *registered* future is not enough: the streaming generator
       registers before it emits and only awaits several ``yield``s later, so a client
       that disconnects in that window would otherwise look "live", get the row finalised
       APPROVED, and leave the tool unexecuted with no resumer able to claim it. An
       unacknowledged decision is disowned back to step 3 instead.
    3. **Async resume.** If no live waiter took it (the run parked, or its socket died),
       an approve resumes the run headless from its checkpoint; the ``approval_id``-keyed
       ``RESUMING`` lock guarantees the tool runs exactly once.

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
    registry = registry if registry is not None else get_approval_registry()
    resolution = await _safe_resolve(approval_id, decision, approver)
    # Acknowledged hand-off, NOT a bare ``resolve``: ``live_woken`` must mean "a live run
    # consumed this decision", never "a future for it existed".
    live_woken = await registry.notify_live(approval_id, decision, approver=approver)

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
        # A still-open /query socket TOOK this decision and is executing the approved
        # action right now, under the SAME PENDING→RESUMING lock (so exactly-once
        # execution holds). Finalize the durable row to APPROVED — previously the live
        # path left it stuck in RESUMING forever, an audit defect for the inbox.
        #
        # Residual, deliberately not closed here: the ack proves the run consumed the
        # outcome, not that the tool has finished. A socket that dies in the microseconds
        # between the two still finalises APPROVED. Narrowing it further needs the graph
        # to ack after ``act``, which no HTTP decision can wait for; the failure mode is
        # at-most-once (never double execution), which is the direction that matters.
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
       :class:`~aegis.agent.approvals.ParkedRun` handle is consumed and its graph resumed.
    2. **No handle** (a *fresh* worker — after a restart, or a different process took
       the decision). We **rehydrate**: rebuild the compiled graph bound to the shared
       durable checkpointer (:func:`_durable_graph`) and resume the run *by
       ``thread_id == run_id``* straight from the checkpoint (finding #8). If the shared
       store holds no resumable checkpoint for that ``thread_id`` there is nothing to
       rehydrate and we report ``False``.

    Either way the headless drive runs the gated tool **exactly once** (the caller has
    already won the optimistic ``PENDING → RESUMING`` transition keyed by
    ``approval_id``). On completion the row is finalised to ``APPROVED``.

    **A failed resume must not strand the run.** The handle is only *peeked* here and
    popped after the drive returns, and a :class:`ResumeFailedError` (a tool raising
    mid-resume, a worker losing its store) releases the row back to ``PENDING`` — the
    handle stays parked and the checkpoint stays reachable, so a retry or the SLA sweeper
    can still act on it. Popping first and swallowing the failure left the row wedged in
    ``RESUMING``, which matches neither the decision path nor the sweeper: neither
    approved nor rejected, and unreachable forever.

    Returns:
        ``True`` if the run was resumed to completion (via handle *or* rehydration);
        ``False`` when there is nothing resumable, or when the resume failed and the row
        was released for another attempt.
    """
    if run_id is None:
        return False

    handle = get_parked_runs().get(run_id)
    if handle is not None:
        graph, config = handle.graph, handle.config
    else:
        # Fresh worker: no in-process handle. Rebuild the graph on the shared durable
        # checkpointer and resume from the checkpoint keyed by ``thread_id``.
        graph = _durable_graph(deps or AgentDeps.default())
        config = {"configurable": {"thread_id": run_id}}

    try:
        resumed = await _core_resume(
            run_id, decision, graph=graph, config=config, approver=approver
        )
    except ResumeFailedError:
        logger.warning(
            "Resume of run %s failed; releasing approval %s back to pending",
            run_id,
            approval_id,
            exc_info=True,
        )
        await _safe_release(approval_id)
        return False

    get_parked_runs().pop(run_id)
    if resumed:
        await _safe_finalize(approval_id)
    return resumed


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


async def _safe_release(approval_id: str) -> None:
    """Release a wedged ``RESUMING`` row back to ``PENDING`` (best-effort).

    The compensating half of the optimistic ``PENDING → RESUMING`` lock. Only that exact
    transition is reversed (the ``status == RESUMING`` predicate makes this idempotent and
    safe against a concurrent finalise), and the decision stamps are cleared so the row is
    genuinely pending again — visible to the inbox, matched by a later decision, and swept
    by the SLA sweeper. Without it a failed resume is terminal: ``RESUMING`` is matched by
    neither :func:`app.data.resolve_approval` nor :func:`app.data.sweep_expired`.
    """
    try:
        from sqlalchemy import update

        from app.data.models import Approval, ApprovalStatus
        from app.data.session import get_sessionmaker

        async with get_sessionmaker()() as session:
            await session.execute(
                update(Approval)
                .where(
                    Approval.id == approval_id,
                    Approval.status == ApprovalStatus.RESUMING,
                )
                .values(
                    status=ApprovalStatus.PENDING, decided_at=None, decided_by=None
                )
            )
            await session.commit()
    except Exception:  # noqa: BLE001 - best-effort status bookkeeping
        logger.warning("Approval release failed for %s", approval_id, exc_info=True)
