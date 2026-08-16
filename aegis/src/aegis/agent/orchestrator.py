"""Drive the agent graph and expose it as a single stamped event stream.

:func:`run_agent` is the one coroutine a host's API layer consumes. It runs the
LangGraph with ``astream(stream_mode=["custom", "updates"])``, forwarding each
node-emitted event after stamping it with the run id and a monotonic sequence
number, and it owns the human-in-the-loop rendezvous:

1. On the first interrupt it emits ``approval_required`` and **registers** the
   gate in the :class:`~aegis.agent.approvals.ApprovalRegistry` *before* awaiting,
   so a fast ``POST /approval`` can never race past the wait.
2. When the decision arrives it resumes the same checkpointed graph with
   ``Command(resume=...)`` and keeps streaming into the still-open SSE response.

The stream is bookended by ``run_started`` and ``run_finished`` (carrying the
final usage/cost/cache-hit), and any failure is surfaced as an ``error`` event
followed by a terminal ``run_finished``.

**The five injected seams** keep this coroutine host-agnostic:

- ``checkpointer`` — the LangGraph store the graph compiles against (a shared,
  possibly durable saver host-side; an in-memory saver by default).
- ``stamp`` — the event validator: ``(payload, run_id, seq) -> event``. Defaults
  to a plain dict stamp; a host injects its locked wire-schema validator.
- ``enqueue_approval`` — persist the durable approvals-inbox row (best-effort);
  defaults to a no-op returning ``None`` (no SLA), matching offline/lite.
- ``on_terminal`` — a best-effort post-run hook (e.g. trace-eval) fired after the
  terminal ``run_finished``; defaults to a no-op. Never blocks the stream.
- ``default_tier`` — the approver tier stamped on a freshly queued gate.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from aegis.core.types import ApprovalDecision, RiskLevel, RunStatus
from aegis.gateway.types import BudgetExceededError
from aegis.observability import get_tracer, semconv
from aegis.observability.latency import record_run_latency
from aegis.observability.otel import current_trace_id

from . import events
from .approvals import (
    ApprovalRegistry,
    ParkedRunRegistry,
    get_approval_registry,
    get_parked_runs,
)
from .deps import AgentDeps
from .graph import build_agent

logger = logging.getLogger(__name__)


# ── Injected-seam type aliases ────────────────────────────────────────────────
StampFn = Callable[..., Any]
EnqueueApprovalFn = Callable[..., Awaitable[str | None]]
OnTerminalFn = Callable[..., None]


def _dict_stamp(payload: dict[str, Any], *, run_id: str, seq: int) -> dict[str, Any]:
    """Default event validator: stamp correlation fields onto the plain dict.

    A host injects its locked wire-schema validator (e.g. a ``StreamEvent``
    ``TypeAdapter``) in its place; the offline default just returns the dict with
    ``run_id``/``seq`` added so :mod:`aegis.agent` never depends on any wire schema.
    """
    return {**payload, "run_id": run_id, "seq": seq}


async def _noop_enqueue_approval(approval_id: str, **_kwargs: Any) -> str | None:  # noqa: ANN401
    """Default durable-inbox seam: persist nothing, return no SLA deadline."""
    return None


def _noop_on_terminal(**_kwargs: Any) -> None:  # noqa: ANN401 - host hook kwargs
    """Default post-run hook: do nothing (no trace-eval wired)."""
    return None


class ResumeFailedError(RuntimeError):
    """Raised when driving a parked run from its checkpoint failed mid-flight.

    Distinct from "there is nothing to resume" (reported as ``False``): the caller holds
    a durable row already flipped to ``RESUMING`` and MUST release it, otherwise the run
    is stranded — ``RESUMING`` matches neither the decision path (``PENDING`` only) nor
    the SLA sweeper (``PENDING`` only).
    """


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
    checkpointer: Any = None,  # noqa: ANN401 - BaseCheckpointSaver
    stamp: StampFn | None = None,
    enqueue_approval: EnqueueApprovalFn | None = None,
    on_terminal: OnTerminalFn | None = None,
    default_tier: str | None = None,
    parked_runs: ParkedRunRegistry | None = None,
) -> AsyncIterator[Any]:
    """Run one query end-to-end, yielding the ordered stream of stamped events.

    Args:
        query: The user's question.
        persona: The adapter persona id scoping data and tools.
        role: The caller's RBAC role (recorded on the run).
        deps: Injected capabilities (required — the composition root builds them).
        registry: The approval rendezvous; defaults to the process-wide registry.
        run_id: An explicit run id; a random one is minted when omitted.
        session_id: Conversation/session id for multi-turn memory. ``None`` (default)
            keeps the run single-shot — the memory nodes stay inert and the stream is
            identical to today.
        memory_subject: The adapter-resolved subject memory is scoped to (the app-level
            isolation key). ``None`` disables memory for this run.
        checkpointer: The LangGraph checkpoint store to compile against; defaults to a
            fresh :class:`~langgraph.checkpoint.memory.InMemorySaver`.
        stamp: The event validator ``(payload, run_id, seq) -> event``; defaults to a
            plain dict stamp.
        enqueue_approval: Best-effort durable-inbox writer returning an SLA ISO string;
            defaults to a no-op returning ``None``.
        on_terminal: Best-effort post-run hook fired after ``run_finished``; defaults to
            a no-op.
        default_tier: The approver tier stamped on a freshly queued gate.
        parked_runs: The parked-run handle registry a gated run registers into; defaults
            to the process-wide registry. A host that owns its own registry (so an
            out-of-band resumer can wipe it to simulate a fresh worker) injects it here.

    Yields:
        Stamped events in wire order (validated by the injected ``stamp``).
    """
    if deps is None:
        raise ValueError(
            "run_agent requires injected deps; the composition root builds them "
            "(there is no default wiring inside aegis.agent)."
        )
    registry = registry if registry is not None else get_approval_registry()
    run_id = run_id or uuid4().hex
    stamp = stamp or _dict_stamp
    enqueue_approval = enqueue_approval or _noop_enqueue_approval
    on_terminal = on_terminal or _noop_on_terminal
    checkpointer = checkpointer or InMemorySaver()
    parked_runs = parked_runs if parked_runs is not None else get_parked_runs()
    graph = build_agent(deps, checkpointer=checkpointer)
    config: dict[str, Any] = {"configurable": {"thread_id": run_id}}

    seq = 0

    # Guardrail verdicts are emitted as stream events (not retained in graph state);
    # collect them so a post-run hook can grade the GUARDRAIL trajectory steps.
    guardrail_events: list[dict[str, Any]] = []

    # Real per-node wall-clock timings (from each ``node_finished``'s ``duration_ms``),
    # collected side-effect-only so a completed run can be folded into the in-process
    # latency window. Never touches the emitted stream.
    node_latencies: list[dict[str, Any]] = []

    def emit(payload: dict[str, Any]) -> Any:  # noqa: ANN401 - stamped event (host type)
        nonlocal seq
        event = stamp(payload, run_id=run_id, seq=seq)
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
                        if isinstance(chunk, dict):
                            ctype = chunk.get("type")
                            if ctype == "guardrail":
                                guardrail_events.append(dict(chunk))
                            elif ctype == "node_finished":
                                node_latencies.append(
                                    {
                                        "node": chunk.get("node"),
                                        "duration_ms": chunk.get("duration_ms"),
                                    }
                                )
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

                # The registration above is live only for as long as THIS generator is:
                # between it and the ``wait`` below sit an await plus three yields, and a
                # disconnected SSE client closes the generator at any of them. The
                # ``finally`` guarantees the future is discarded in that case, so a
                # decision arriving afterwards cannot be mistaken for a live wake-up
                # (which would audit the gate APPROVED while the tool never ran) and
                # cannot leak a future nobody will ever await.
                try:
                    # Persist the durable inbox row — the source of truth for the paused
                    # run — and retain a resumable handle so an out-of-band decision can
                    # continue this run from its checkpoint if the socket parks.
                    sla_deadline = await enqueue_approval(
                        approval_id,
                        run_id=run_id,
                        trace_id=trace_id,
                        persona=persona,
                        action=action,
                        args=args,
                        risk=risk,
                        rationale=rationale,
                        # NOTE: no ``ml_snapshot`` is passed. The host's approvals
                        # row still HAS that column and the console still reads it,
                        # but no ML step runs in this graph any more, so there is
                        # nothing honest to freeze at the gate. Every new row is
                        # therefore ``{}`` by design — an empty snapshot is the
                        # correct state, not a bug. The column stays because
                        # dropping it needs a migration, which is deferred.
                    )
                    parked_runs.register(run_id, graph, config)

                    yield emit(events.node_started("approval", "Human approval gate"))
                    yield emit(
                        events.approval_queued(
                            approval_id,
                            action=action,
                            args=args,
                            risk=risk,
                            rationale=rationale,
                            sla_deadline=sla_deadline,
                            assignee_tier=default_tier,
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
                        # Park: the run is NOT lost — it survives as a durable PENDING
                        # row plus a checkpoint, resumed out-of-band from the inbox. This
                        # also catches ``GateHandedOffError``: a decision that we failed
                        # to take in time now belongs to the resumer, so parking here is
                        # exactly right — the action still runs exactly once, over there.
                        logger.info(
                            "Run %s parked awaiting approval %s", run_id, approval_id
                        )
                        yield emit(events.run_finished(RunStatus.AWAITING_APPROVAL))
                        return
                finally:
                    registry.discard(approval_id)
                stream_input = Command(
                    resume={"approved": outcome.approved, "approver": outcome.approver}
                )

            # Live run completed within the socket — drop the resumable handle.
            parked_runs.pop(run_id)
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
            # Terminal run reached: fire the best-effort post-run hook (e.g. trace-eval)
            # OFF the hot path. This never blocks or delays the stream — the run_finished
            # above is already yielded — and a failure is swallowed by the hook itself.
            on_terminal(
                run_id=run_id,
                query=query,
                final=dict(final),
                guardrail_events=guardrail_events,
            )
            # Fold this completed run's REAL measured per-node timings into the
            # in-process latency window (side-effect-only telemetry; never raises).
            record_run_latency(node_latencies)
        except BudgetExceededError as exc:
            # A per-tenant/user cap tripped at the LiteLLM chokepoint before spend:
            # end the run cleanly as blocked, not a crash (§3.3).
            logger.info("Agent run %s blocked by budget: %s", run_id, exc.message)
            parked_runs.pop(run_id)
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
            # Drop the resumable handle exactly as the budget path above does: an errored
            # run is terminal, so a retained handle pins a compiled graph plus its
            # checkpointer (the whole run state) with nothing left to resume.
            parked_runs.pop(run_id)
            yield emit(events.error(str(exc)))
            yield emit(events.run_finished(RunStatus.ERROR))


def _is_interrupt(chunk: Any) -> bool:  # noqa: ANN401 - opaque astream chunk
    """Return whether an ``updates`` chunk carries a LangGraph interrupt."""
    return isinstance(chunk, dict) and "__interrupt__" in chunk


async def resume_parked_run(
    run_id: str | None,
    decision: ApprovalDecision,
    *,
    graph: Any,  # noqa: ANN401 - CompiledStateGraph
    config: dict[str, Any] | None = None,
    approver: str | None = None,
) -> bool:
    """Drive a parked run from its checkpoint to completion (headless, exactly once).

    A pure helper over an already-resolved gate: the caller (the host's durable
    decide-approval glue) has won the optimistic ``PENDING → RESUMING`` transition and
    supplies the compiled ``graph`` (its checkpointer holds the paused state) plus the
    ``config`` carrying ``thread_id == run_id``. This resumes with ``Command(resume=...)``
    and streams headless; the gated tool executes exactly once.

    Returns:
        ``True`` if the run was resumed to completion; ``False`` when there is nothing
        resumable (no ``run_id``, or the checkpoint holds no pending step).

    Raises:
        ResumeFailedError: If the headless drive failed mid-flight (a tool raised, the
            worker lost its store, …). This is deliberately NOT flattened into ``False``:
            the caller has already won the durable ``PENDING → RESUMING`` transition, and
            a silent ``False`` leaves the row wedged in ``RESUMING`` forever — no sweeper
            or later decision matches it, so the run is stranded neither approved nor
            rejected. The caller must distinguish "nothing to resume" from "resume broke"
            in order to release the row.
    """
    if run_id is None:
        return False
    config = config or {"configurable": {"thread_id": run_id}}
    try:
        resumable = bool(graph.get_state(config).next)
    except Exception as exc:  # noqa: BLE001 - a broken store is a failure, not an absence
        logger.exception("Cannot read the checkpoint for run %s", run_id)
        raise ResumeFailedError(f"checkpoint read for run {run_id} failed: {exc}") from exc
    if not resumable:
        logger.info("No resumable checkpoint for run %s; cannot rehydrate", run_id)
        return False
    resume_cmd = Command(
        resume={"approved": decision is ApprovalDecision.APPROVE, "approver": approver}
    )
    try:
        async for _mode, _chunk in graph.astream(
            resume_cmd, config, stream_mode=["custom", "updates"]
        ):
            pass  # drive headless to completion; the tool runs exactly once
    except Exception as exc:  # noqa: BLE001 - surfaced as ResumeFailedError, never lost
        logger.exception("Headless resume of run %s failed", run_id)
        raise ResumeFailedError(f"headless resume of run {run_id} failed: {exc}") from exc
    return True
