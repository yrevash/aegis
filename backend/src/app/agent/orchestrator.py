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
- :func:`_record_prompt_attribution` — note which LLM-Ops prompt version this run was
  served, so "which prompt produced this answer?" has an answer that is recorded rather
  than inferred (§7.7).

The API layer (``app.api.routes``) consumes :func:`run_agent`/:func:`decide_approval`
unchanged.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing
from datetime import UTC, datetime
from typing import Any

from aegis.agent.approvals import ApprovalRegistry
from aegis.agent.orchestrator import ResumeFailedError
from aegis.agent.orchestrator import resume_parked_run as _core_resume
from aegis.agent.orchestrator import run_agent as _core_run_agent
from aegis.core.types import RunStatus
from aegis.governance.context import governed

from app.agent.approvals import get_approval_registry, get_parked_runs
from app.agent.deps import AgentDeps, deps_for_run
from app.agent.run_log import TERMINAL_EVENT_TYPE, parked_run_owner
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


def _fire_run_record(
    run_id: str, streamed: list[Any], stamped_at: list[datetime]
) -> None:
    """Persist this run into the durable run record, off the hot path.

    **The fix for the platform's headline consistency defect.** ``aegis.runs`` — the
    append-only ``run_events`` log and its regenerable ``runs`` header — was reachable
    only from the demo seeder and the ingest pipeline, so a tenant's real questions were
    metered in ``usage_ledger``, audited in ``audit_log``, and recorded as runs nowhere
    at all. Every event of this run has just been streamed; this is where it becomes
    durable.

    The tenant and user are read **here**, while the request's sealed governance context
    is still bound — :mod:`app.agent.run_log` does the write on a background task, by
    which time it is not. Neither value is ever taken from the client.

    Best-effort in the strict sense: any failure to even schedule the write is logged and
    swallowed, because the answer this run produced has already been delivered.
    """
    try:
        from app.agent.run_log import fire_run_record

        gov = None
        try:
            from app.core.governance import get_governance_context

            gov = get_governance_context()
        except Exception:  # noqa: BLE001 - governance is optional at this seam
            gov = None
        fire_run_record(
            run_id=run_id,
            events=streamed,
            tenant_id=gov.tenant_id if gov is not None else None,
            user_id=gov.user_id if gov is not None else None,
            timestamps=stamped_at,
        )
    except Exception:  # noqa: BLE001 - recording must never disturb the stream
        logger.exception("Durable run-record kickoff failed for run %s", run_id)


def _record_abandoned_gate(streamed: list[Any], stamped_at: list[datetime]) -> None:
    """Record a run whose socket died **while it was parked at the approval gate**.

    ``approval_park_timeout`` is ``None`` in this deployment, which means the live
    ``/query`` socket holds a gate open indefinitely and never emits the
    ``run_finished(awaiting_approval)`` that :func:`run_agent` records on. So the way a
    run actually becomes parked here is that the client goes away: the route's task is
    cancelled while the core loop is inside ``registry.wait``, this generator unwinds,
    and — until now — the run left **no ``runs`` row at all** while its ``approvals`` row
    sat ``PENDING``. That is the same consistency defect as a header stuck at
    ``awaiting_approval``, one notch worse: not a stale row, no row.

    So the terminal event the socket never lived to carry is synthesised here, from the
    fact that decides it: the last event this run emitted was ``approval_required``, and
    both the durable ``approvals`` row and the checkpoint were written before it. The run
    is not abandoned — it is *waiting*, durably — and ``awaiting_approval`` says so.
    :func:`resume_parked_run` appends the rest when a human decides.

    **Only that case.** A stream that ends anywhere else is a client that hung up
    mid-answer, and there is no honest terminal status for it: ``error`` would blame the
    run for the browser, ``completed`` would invent an outcome. Those stay unrecorded, as
    they were.

    Synchronous throughout (:func:`_fire_run_record` only schedules a task), because this
    runs in an unwinding — usually *cancelled* — generator, where an ``await`` either
    raises immediately or turns a ``GeneratorExit`` into a ``RuntimeError``.

    Args:
        streamed: Everything the run emitted, in order.
        stamped_at: When each was emitted, positionally.
    """
    try:
        if not streamed or getattr(streamed[-1], "type", None) != "approval_required":
            return
        last = streamed[-1]
        run_id = str(last.run_id)
        logger.info(
            "Run %s was left parked at its approval gate by a departing client; "
            "recording it as awaiting_approval so the runs row and the approvals row "
            "agree until the gate is decided.",
            run_id,
        )
        streamed.append(
            events.stamp(
                events.run_finished(RunStatus.AWAITING_APPROVAL),
                run_id=run_id,
                seq=int(last.seq) + 1,
            )
        )
        stamped_at.append(datetime.now(UTC))
        _fire_run_record(run_id, streamed, stamped_at)
    except Exception:  # noqa: BLE001 - a teardown path may not raise
        logger.exception("Recording the parked run at stream teardown failed")


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
    depth_mode: str | None = None,
    requested_fanout: int | None = None,
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
        depth_mode: The user's REQUESTED width (``auto`` | ``single`` | ``team``), from
            ``QueryRequest.depth_mode``. Forwarded verbatim: this wrapper binds seams, it
            does not decide widths. Dropping it here — which is what this signature did
            until Phase 6 — is why the whole override mechanism was unreachable from a
            browser while every layer beneath it worked.
        requested_fanout: An explicit team width (the composer's Custom mode), clamped
            DOWN by the tenant's cap in :func:`aegis.agent.router.decide_depth` and never
            up. Also forwarded verbatim, for the same reason.

    Yields:
        Validated :data:`~app.api.schemas.StreamEvent` variants in wire order.
    """
    from app.data.session import get_agent_checkpointer

    deps = deps or AgentDeps.default()
    # The tenant's own floors, folded on **here** rather than in the composition root:
    # ``AgentConfig`` is built once and synchronously, the settings that govern it are
    # per tenant and async, and this is the first point in a run at which the tenant is
    # known. Without it ``agent.gate_min_risk`` was a control a tenant admin could write
    # and nothing read. Per run, never memoised — see ``app.agent.deps.deps_for_run``.
    deps = await deps_for_run(deps)
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
            depth_mode=depth_mode,
            requested_fanout=requested_fanout,
            checkpointer=get_agent_checkpointer(),
            stamp=events.stamp,
            enqueue_approval=_enqueue_gate,
            on_terminal=_fire_trace_eval,
            default_tier=_default_tier(),
            parked_runs=get_parked_runs(),
        )
    ) as stream:
        attributed = False
        # Every event this run streamed, retained so the run can be written to the
        # durable record (``run_events`` + the folded ``runs`` header) when it ends. This
        # is the only place with both the whole ordered stream and the request's
        # governance context, which is why the wiring is here and not in the route: a
        # second consumer of ``run_agent`` would otherwise have to remember to record.
        streamed: list[Any] = []
        # When each event was emitted, captured here because the wire schema carries no
        # timestamp: the whole run is written in one transaction at the end, so without
        # these every row would land at the flush instant and the header would fold
        # ``started_at == finished_at`` — a durable record saying every run was
        # instantaneous.
        stamped_at: list[datetime] = []
        recorded = False
        try:
            async for event in stream:
                if not attributed:
                    attributed = True
                    _record_prompt_attribution(event, persona)
                streamed.append(event)
                stamped_at.append(datetime.now(UTC))
                if not recorded and getattr(event, "type", None) == TERMINAL_EVENT_TYPE:
                    # Fired BEFORE the yield, deliberately: ``run_finished`` is the last
                    # event of every outcome (completed, blocked, errored, parked at a
                    # gate), and a client that disconnects while receiving it closes this
                    # generator at that yield. Scheduling first means the record of a run
                    # does not depend on the socket surviving long enough to hear that it
                    # finished.
                    recorded = True
                    _fire_run_record(event.run_id, streamed, stamped_at)
                yield event
        finally:
            if not recorded:
                _record_abandoned_gate(streamed, stamped_at)


def _record_prompt_attribution(event: Any, persona: str | None) -> None:  # noqa: ANN401
    """Note which prompt version this run was served (§7.7), best-effort.

    Recorded on the run's **first** event because that is the first moment the minted
    ``run_id`` is knowable here — the core mints it when the caller passes none — and it
    reads the version through the very same call the harness makes on the hot path,
    ``app.ops.registry.get_cached_active``, whose tenant comes from the sealed governance
    context. So the recorded attribution cannot disagree with what was actually sent:
    there is no second lookup with a second idea of the tenant.

    Best-effort in the strict sense — an attribution failure must never be why a run
    fails — and deliberately in-process; see :mod:`app.ops.prompt_runs` for what that
    does and does not promise.
    """
    try:
        from app.adapter import DEFAULT_PERSONA_ID
        from app.ops import prompt_runs, registry

        run_id = getattr(event, "run_id", None)
        if not run_id:
            return
        prompt_key = persona or DEFAULT_PERSONA_ID
        cached = registry.get_cached_active(prompt_key)
        prompt_runs.record(
            run_id=str(run_id),
            tenant_id=registry.current_tenant_id(),
            prompt_key=prompt_key,
            version=cached[2] if cached is not None else None,
        )
    except Exception:  # noqa: BLE001 - bookkeeping must never break a run
        logger.debug("Prompt-version attribution skipped.", exc_info=True)


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
    actions: list[dict[str, Any]] | None = None,
) -> str | None:
    """Persist the durable approvals-inbox row (best-effort), returning its SLA ISO.

    The durable-inbox seam ``aegis.agent``'s ``run_agent`` calls at the gate. Best-effort
    so the offline "lite" demo (no database) still runs the live gate: a write failure is
    logged, the ``approval_queued`` event simply carries no SLA deadline, and the
    in-process notify path resolves the gate.

    Args:
        approval_id: The gate id.
        run_id: The paused run.
        trace_id: The run's OTel trace id.
        persona: The persona that raised the run.
        action: The representative (highest-risk) proposed call.
        args: That call's arguments.
        risk: The gate's declared risk.
        rationale: Why the gate fired.
        actions: Every call approving authorises, when the graph enumerated them.

    Returns:
        The row's ``sla_deadline`` as an ISO string, or ``None`` if the write failed.
    """
    try:
        from app.core.governance import get_governance_context
        from app.data import enqueue_approval

        # Stamp the owning tenant *and the requester* from the per-request governance
        # context so the durable row can be tenant-scoped for the inbox and decision
        # paths (C1) — and so the person whose run raised the gate can be shown its
        # fate (§7.1) without being shown anybody else's. Both are read server-side
        # from the authenticated context; neither is ever accepted from a client.
        gov = get_governance_context()
        tenant_id = gov.tenant_id if gov is not None else None
        requested_by = gov.user_id if gov is not None else None

        row = await enqueue_approval(
            approval_id=approval_id,
            run_id=run_id,
            action=action,
            args=args,
            actions=actions,
            risk=risk,
            persona=persona,
            trace_id=trace_id,
            tenant_id=tenant_id,
            requested_by=requested_by,
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

    if won and decision is ApprovalDecision.APPROVE and _is_mcp_proposal(run_id):
        # An MCP-originated gate is not a parked graph run — there is no checkpoint to
        # resume — so the approved tool is executed directly, under the persona and the
        # tenant the row itself records. Without this the row wedged in ``RESUMING``:
        # gone from the pending inbox, unmatched by ``resolve_approval`` and by the SLA
        # sweeper, and rendered by the console as an action in flight that had not
        # happened and never would.
        executed = await _execute_mcp_proposal(approval_id, approver)
        if executed:
            await _safe_finalize(approval_id)
            status = "approved"
        else:
            # Nothing ran, so nothing may claim it did. Back to ``PENDING``: visible,
            # decidable again, and still swept — the same compensation a failed resume
            # gets, for the same reason.
            await _safe_release(approval_id)
            status = "pending"
            accepted = live_woken
    elif won and decision is ApprovalDecision.APPROVE and not live_woken:
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
    elif (
        won
        and decision is ApprovalDecision.REJECT
        and not live_woken
        and run_id
        and not _is_mcp_proposal(run_id)
    ):
        # A rejected gate is a run that has been DECIDED, not one that may be left
        # parked. The graph already knows what to do with a refusal — ``approval`` routes
        # an unapproved gate to ``generate``, which answers saying the action was not
        # authorised — and the live path has always driven exactly that. The parked path
        # dropped the handle and walked away, so the checkpoint sat unfinished and the
        # run header stayed ``awaiting_approval`` next to an ``approvals`` row reading
        # ``REJECTED``: the same two-tables-disagree defect as the approve path, with the
        # tool correctly not run. Driving it here gives the run a real terminal status.
        #
        # The row is already ``REJECTED`` (a terminal state ``resolve_approval`` reached
        # directly), so nothing is finalised afterwards — see ``resume_parked_run``.
        await resume_parked_run(
            approval_id, run_id, decision, approver=approver, deps=deps
        )
        get_parked_runs().pop(run_id)
    elif (decision is ApprovalDecision.REJECT or live_woken) and run_id:
        # Rejected on a path with nothing to resume (an MCP proposal, a replayed
        # decision), or a live socket is executing it — drop any resumable handle.
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

    **The continuation is recorded.** The events this headless drive produces are
    collected and appended to the run's durable log, then the ``runs`` header is re-folded
    from them (:func:`app.agent.run_log.save_run_continuation`). Without that the header
    a parked run wrote at the gate — ``awaiting_approval`` — was its *final* word: a
    dashboard showed a run still waiting for a human that had been approved, executed and
    finished, while the ``approvals`` row recorded the decision correctly. Two of our own
    tables disagreeing about one run is the one defect a platform that sells reconcilable
    figures cannot ship.

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
        # Same per-tenant floors as a fresh run: a resume plans and proposes further
        # actions, so it must gate against the tenant's floor, not the compiled default.
        graph = _durable_graph(await deps_for_run(deps or AgentDeps.default()))
        config = {"configurable": {"thread_id": run_id}}

    # The continuation as it happens, and when. The core hands each event here rather
    # than dropping it; the times are taken at collection because these events, like a
    # live run's, carry no timestamp of their own and the whole batch commits at the end.
    continuation: list[dict[str, Any]] = []
    continuation_at: list[datetime] = []

    def collect(payload: dict[str, Any]) -> None:
        continuation.append(payload)
        continuation_at.append(datetime.now(UTC))

    # The continuation is governed work, and until now it was not governed at all.
    # ``POST /v1/query`` binds a governance context around its stream; the decision
    # endpoints bind none, and the gateway skips **both** budget enforcement and the
    # usage ledger when no context is bound (``aegis.gateway.llm._record_usage`` returns
    # immediately on a ``None`` context). Measured on ``taif_run1``: a rejected run's
    # headless resume generated a real answer through the model and wrote *zero*
    # ``usage_ledger`` rows — money spent, capped by nothing, recorded nowhere, and
    # therefore impossible to reconcile against the run it belonged to.
    #
    # The principal comes from the platform's own records, never from the approver: the
    # tenant from the ``approvals`` row (see :func:`_parked_run_tenant`), the user from
    # the run's stored header. An admin clearing an inbox must not have another tenant's
    # continuation billed to them, and must not have their own caps applied to it.
    #
    # This makes a tenant's cap bind on a resume, which it did not before. That is the
    # intended consequence: a cap that stops applying the moment a human is involved is
    # not a cap. A tripped cap surfaces as ``ResumeFailedError`` below, which releases
    # the approval back to PENDING for a retry rather than stranding it.
    resume_tenant = await _parked_run_tenant(approval_id)
    resume_user = await parked_run_owner(run_id, tenant_id=resume_tenant)
    try:
        with governed(tenant_id=resume_tenant, user_id=resume_user):
            resumed = await _core_resume(
                run_id,
                decision,
                graph=graph,
                config=config,
                approver=approver,
                on_event=collect,
            )
    except ResumeFailedError:
        logger.warning(
            "Resume of run %s failed; releasing approval %s back to pending",
            run_id,
            approval_id,
            exc_info=True,
        )
        await _safe_release(approval_id)
        # Nothing recorded: the row goes back to PENDING, so the retry that follows will
        # drive this same continuation again and an append here would be counted twice.
        return False

    get_parked_runs().pop(run_id)
    if resumed:
        await _record_continuation(approval_id, run_id, continuation, continuation_at)
        if decision is ApprovalDecision.APPROVE:
            # Only an approval finalises to APPROVED. A rejection reached its terminal
            # ``REJECTED`` in ``resolve_approval`` already, and finalising it here would
            # overwrite a human's refusal with the opposite decision.
            await _safe_finalize(approval_id)
    return resumed


async def _parked_run_tenant(approval_id: str) -> int | None:
    """Return the tenant the parked run belongs to, from the server's own records.

    **Not the governance context, and that is the point.** ``POST /v1/query`` binds a
    governance context around its stream, so :func:`_fire_run_record` reads the run's
    tenant straight off it; the decision endpoints bind none, so reading it here yields
    ``None`` — and ``None`` is not "unknown", it is the *platform* scope
    (:func:`aegis.governance.rls.set_tenant_scope`). Writing the continuation under it
    filed a resumed run's events as platform rows: invisible to the tenant that made the
    run, and so invisible to :func:`aegis.runs.record.rebuild_run_header`, which then
    "reconciled" the header back to ``awaiting_approval``. Measured, not theorised — it
    is what the first end-to-end run of this fix did.

    So the tenant is taken from the ``approvals`` row, which is the right question asked
    of the right record: it is stamped server-side at enqueue time from the governance
    context of the run *that raised the gate* (:func:`_enqueue_gate`), it is the tenant
    ``_enforce_approval_tenant`` has already checked this approver against, and it is the
    tenant of the events being appended rather than of the person appending them. It is
    never accepted from a client on any path.

    Args:
        approval_id: The gate that was just decided.

    Returns:
        The owning tenant, or ``None`` for a genuinely platform-level gate (and when the
        row cannot be read, which is logged).
    """
    try:
        from app.data import get_approval

        row = await get_approval(approval_id)
        if row is not None:
            return row.tenant_id
        logger.warning(
            "Approval %s has no durable row, so the resumed run's continuation is "
            "recorded at platform scope.",
            approval_id,
        )
    except Exception:  # noqa: BLE001 - fall through to the governance context
        logger.warning(
            "Could not read approval %s to attribute the resumed run's continuation.",
            approval_id,
            exc_info=True,
        )
    try:
        from app.core.governance import get_governance_context

        gov = get_governance_context()
        return gov.tenant_id if gov is not None else None
    except Exception:  # noqa: BLE001 - governance is optional at this seam
        return None


async def _record_continuation(
    approval_id: str,
    run_id: str,
    continuation: list[dict[str, Any]],
    continuation_at: list[datetime],
) -> None:
    """Append a resumed run's continuation to its durable record and re-fold the header.

    The run's *user* is deliberately not passed on: the person who approved a gate is not
    the person whose run it is, and :func:`aegis.runs.record.record_events` preserves the
    owner already on the header. See :func:`_parked_run_tenant` for where the tenant
    comes from and why it is not the approver's.

    Awaited rather than fired, and wrapped: by the time the decision endpoint answers,
    ``runs`` and ``approvals`` agree — and a recording failure costs a loud log line, not
    the resume, which has already run the approved action.
    """
    try:
        from app.agent.run_log import save_run_continuation

        await save_run_continuation(
            run_id=run_id,
            events=continuation,
            tenant_id=await _parked_run_tenant(approval_id),
            timestamps=continuation_at,
        )
    except Exception:  # noqa: BLE001 - recording must never break the resume
        logger.exception(
            "Durable run-continuation kickoff failed for run %s; the approved action "
            "ran, but the run header still says awaiting_approval.",
            run_id,
        )


def _is_mcp_proposal(run_id: str | None) -> bool:
    """Return whether this gate came from an MCP call rather than from a graph run.

    Imported lazily and tolerantly: the ``mcp`` SDK is an optional extra, and a
    deployment without it has no MCP rows to recognise.
    """
    try:
        from app.mcp.server import is_mcp_proposal
    except ImportError:  # pragma: no cover - the MCP extra is not installed
        return False
    return is_mcp_proposal(run_id)


async def _execute_mcp_proposal(approval_id: str, approver: str | None) -> bool:
    """Carry out an approved MCP proposal; ``False`` if it did not run."""
    try:
        from app.mcp.server import execute_approved_proposal
    except ImportError:  # pragma: no cover - the MCP extra is not installed
        return False
    return await execute_approved_proposal(approval_id, approver=approver)


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
