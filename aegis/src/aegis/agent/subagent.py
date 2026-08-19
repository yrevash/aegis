"""One sub-agent: a bounded ReAct loop that **proposes** rather than acts on risk.

This is the unit the fan-out in :mod:`aegis.agent.team` runs N of, concurrently, inside
a single graph node. It reuses the capabilities that already exist — ``deps.complete``,
``deps.run_tool``, ``deps.tool_definitions_for``, ``deps.tool_risk`` — and adds no new
seam of its own.

**The constraint this module exists to honour, and it is not negotiable:**

    No :func:`langgraph.types.interrupt` inside a gathered task.

A sub-agent runs inside an ``asyncio.gather`` within one node. LangGraph's interrupt is
a node-level control-flow signal; raising it from a gathered sibling would tear the
fan-out apart and, far worse, would create a *second* path to a consequential action.
So a sub-agent may execute nothing at or above ``config.gate_min_risk``. Anything it
wants at that tier comes back in :attr:`SubAgentResult.proposed_actions` and is executed
— if a human approves — by the main graph's single ``gate → approval → act`` path.

This is a security improvement, not a limitation: **no concurrent agent can take a
consequential action without passing the one gate**, and there is exactly one gate to
audit rather than one per lane. The enforcement is in code (:func:`_partition_calls`),
never in a prompt, and this module deliberately does not import ``interrupt`` at all.

Two more invariants, both enforced here rather than hoped for:

* **Tools are the spec's allowlist intersected with the persona's**, and the persona
  half comes from ``deps.tool_definitions_for(persona)`` — which is the host's
  ``is_allowed`` and nothing else. There is ONE intersection in the codebase, because
  two is how the second one ends up subtly more permissive.
* **It never raises.** Every failure becomes a :class:`SubAgentResult` with a terminal
  ``status``. The single exception is
  :class:`~aegis.gateway.types.BudgetExceededError`, which is allowed to propagate so
  the fan-out can re-raise it after fan-in and the orchestrator's existing handler
  still terminates the run cleanly as ``blocked``. A tenant's own cap is the one thing
  that may refuse a run.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from aegis.core.models import ModelRole
from aegis.gateway.types import BudgetExceededError
from aegis.observability import SpanKind, semconv, span

from . import events
from .deps import AgentDeps, risk_at_least
from .retry import call_with_retry

__all__ = [
    "SubAgentResult",
    "SubAgentSpec",
    "SubAgentStatus",
    "run_subagent",
]

logger = logging.getLogger(__name__)

#: Splits a sub-agent's reasoning text into sentence-sized ``reasoning`` events, exactly
#: as the main planner's plan text is chunked, so a lane reads the same as the main lane.
_SENTENCE = re.compile(r"[^.!?]+[.!?]*")

#: The writer a sub-agent streams through: a plain ``(payload) -> None`` callable. The
#: fan-out binds one per agent so every event carries its ``agent_id`` automatically and
#: no call site has to remember to stamp it.
WriterFn = Callable[[dict[str, Any]], Any]


class SubAgentStatus(StrEnum):
    """How one sub-agent's lane ended.

    ``TIMEOUT`` is a **designed** terminal state, not an error: the synthesis names the
    agent as omitted and says why, which is what turns a spinning card into visible,
    graceful degradation.
    """

    OK = "ok"
    FAILED = "failed"
    TIMEOUT = "timeout"
    #: The lane was cancelled from outside (a killed agent, or the fan-out's wall clock).
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class SubAgentSpec:
    """One sub-agent's identity, remit and bounds — the adapter's roster entry shape.

    Attributes:
        agent_id: Stable id stamped on every event this agent emits, and (from §5.4)
            the ``run_events.agent_id`` column. Unique within a run.
        role: The agent's kind (``research`` | ``knowledge`` | ``data`` | ``policy`` …).
        label: Human label for the lane's card in the console.
        system_prompt: The floor prompt. §5.9 promotes this to a registry ``prompt_key``;
            the adapter's string remains the floor when no ACTIVE version exists.
        tool_allowlist: The tool names this agent may reach. Intersected with the
            persona's allowlist — never a widening of it.
        model_role: Which model tier this agent runs on. ``CHEAP`` for the agents that
            do not reason; that choice is most of why a fan-out is affordable.
        max_steps: Hard cap on loop iterations — the guarantee it terminates.
        timeout_s: The lane's wall clock.
    """

    agent_id: str
    role: str
    label: str
    system_prompt: str
    tool_allowlist: frozenset[str] = frozenset()
    model_role: ModelRole = ModelRole.CHEAP
    max_steps: int = 4
    timeout_s: float = 45.0


@dataclass
class SubAgentResult:
    """What one lane produced — always a value, never an exception.

    Attributes:
        agent_id / role / label: Copied off the spec so the synthesis can attribute a
            claim without holding the roster.
        status: The lane's terminal state.
        findings: The agent's answer to its sub-task (empty on a failed/timed-out lane).
        proposed_actions: Tool calls at or above ``gate_min_risk`` the agent wants taken.
            **Not executed here.** They flow into the main graph's single gate.
        tool_calls: The within-ceiling calls this agent actually executed, with outcomes.
        steps: How many loop iterations ran.
        error: The failure detail for a non-``OK`` status (never ``None`` when failed).
        prompt_tokens / completion_tokens / cost_usd: This lane's own spend, summed by
            the fan-out node into ONE delta so the existing ``operator.add`` reducers
            keep working untouched.
    """

    agent_id: str
    role: str
    label: str
    status: SubAgentStatus = SubAgentStatus.OK
    findings: str = ""
    proposed_actions: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    steps: int = 0
    error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def contributed(self) -> bool:
        """Whether this lane produced findings the synthesis can use."""
        return self.status is SubAgentStatus.OK and bool(self.findings.strip())

    def as_dict(self) -> dict[str, Any]:
        """Return a plain, JSON-friendly record (for graph state and the wire)."""
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "label": self.label,
            "status": self.status.value,
            "findings": self.findings,
            "proposed_actions": list(self.proposed_actions),
            "tool_calls": list(self.tool_calls),
            "steps": self.steps,
            "error": self.error,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": self.cost_usd,
        }


def _sentences(text: str) -> list[str]:
    """Split ``text`` into trimmed, non-empty sentence chunks."""
    return [s.strip() for s in _SENTENCE.findall(text) if s.strip()]


def _tool_name(definition: dict[str, Any]) -> str:
    """Read the tool name off an OpenAI-shaped tool definition."""
    return str(definition.get("function", {}).get("name", ""))


def allowed_tool_definitions(
    spec: SubAgentSpec, deps: AgentDeps, persona: str
) -> list[dict[str, Any]]:
    """Return the tool definitions this sub-agent may see: spec ∩ persona.

    The persona half is **not** re-derived here. It is exactly what
    ``deps.tool_definitions_for(persona)`` returns, which host-side is the adapter's
    ``is_allowed`` allowlist and nothing else. So a sub-agent can only ever *narrow*
    what its persona could already reach, and Phase 6's tool pins — which go through the
    same function — cannot become a second, more permissive intersection.
    """
    return [
        d
        for d in deps.tool_definitions_for(persona)
        if _tool_name(d) and _tool_name(d) in spec.tool_allowlist
    ]


def _partition_calls(
    calls: list[Any], deps: AgentDeps, allowed: set[str]
) -> tuple[list[Any], list[Any], list[Any]]:
    """Split the model's tool calls into (executable, proposed, refused).

    This is where the non-negotiable constraint is enforced, in code:

    * **executable** — allowed, and strictly below ``config.gate_min_risk``.
    * **proposed** — allowed, but at or above the gate floor. A sub-agent may not run
      these at any cost; they are returned for the main graph's one gate.
    * **refused** — outside the persona ∩ spec intersection. The model asked for
      something it was never offered, so nothing happens and it is told so.
    """
    floor = deps.config.gate_min_risk
    executable: list[Any] = []
    proposed: list[Any] = []
    refused: list[Any] = []
    for call in calls:
        if call.name not in allowed:
            refused.append(call)
        elif risk_at_least(deps.tool_risk(call.name), floor):
            proposed.append(call)
        else:
            executable.append(call)
    return executable, proposed, refused


async def run_subagent(
    spec: SubAgentSpec,
    task: str,
    *,
    deps: AgentDeps,
    persona: str,
    writer: WriterFn,
    context: str = "",
    working_memory: str = "",
    trace_id: str | None = None,
    retry: Any = None,  # noqa: ANN401 - langgraph RetryPolicy | None
) -> SubAgentResult:
    """Run one sub-agent's bounded loop and return its result (it never raises).

    Args:
        spec: The agent's identity, remit and bounds.
        task: The sub-question this agent owns.
        deps: The already-wired capabilities (no new seam is introduced).
        persona: The run's persona — the other half of the tool intersection.
        writer: The scoped event writer for this lane (pre-bound to ``agent_id``).
        context: The **shared** retrieval pool for the run. Four agents must not
            retrieve the tenant's chunks four times, so the pool is fetched once by
            the fan-out and handed to every lane (Amendment A's supply-side rule).
        working_memory: The user's rendered profile/durable facts, selected by the
            adapter for the main graph and passed through unchanged.
        trace_id: Correlation id for tool audit rows.
        retry: Optional :class:`~langgraph.types.RetryPolicy` for this agent's model
            calls (the fan-out passes the graph's ``_MODEL_RETRY``).

    Returns:
        A :class:`SubAgentResult`. A timeout, a model failure, a tool explosion — all
        of them are a result with a terminal ``status``, because one lane failing must
        never be able to fail the run or cancel its siblings.

    Raises:
        BudgetExceededError: The ONE exception allowed out. The tenant's own cap is the
            only thing that may refuse a run; the fan-out re-raises it after fan-in so
            the orchestrator's existing handler ends the run cleanly as ``blocked``.
    """
    result = SubAgentResult(agent_id=spec.agent_id, role=spec.role, label=spec.label)
    writer(
        events.agent_status(
            agent_id=spec.agent_id,
            role=spec.role,
            label=spec.label,
            status="started",
            detail=task,
        )
    )
    try:
        await asyncio.wait_for(
            _loop(
                spec,
                task,
                deps=deps,
                persona=persona,
                writer=writer,
                context=context,
                working_memory=working_memory,
                trace_id=trace_id,
                retry=retry,
                result=result,
            ),
            timeout=spec.timeout_s,
        )
    except BudgetExceededError:
        # Deliberately NOT swallowed. See the module docstring: the tenant's cap is the
        # one refusal this platform makes, and the orchestrator already knows how to
        # end a run cleanly as blocked. Re-raised by the fan-out after fan-in.
        writer(
            events.agent_status(
                agent_id=spec.agent_id,
                role=spec.role,
                label=spec.label,
                status="failed",
                detail="tenant budget exceeded",
            )
        )
        raise
    except TimeoutError:
        result.status = SubAgentStatus.TIMEOUT
        result.error = f"timed out after {spec.timeout_s:g}s"
        writer(
            events.agent_status(
                agent_id=spec.agent_id,
                role=spec.role,
                label=spec.label,
                status="timeout",
                detail=result.error,
            )
        )
        return result
    except asyncio.CancelledError:
        # A killed lane (the fan-out's wall clock, or an explicit cancel). Recorded as a
        # designed terminal state rather than propagated, so the siblings still land and
        # the synthesis can name this one as omitted.
        result.status = SubAgentStatus.CANCELLED
        result.error = "cancelled mid-run"
        writer(
            events.agent_status(
                agent_id=spec.agent_id,
                role=spec.role,
                label=spec.label,
                status="failed",
                detail=result.error,
            )
        )
        return result
    except Exception as exc:  # noqa: BLE001 - a lane's failure is a value, never a raise
        logger.warning("Sub-agent %s failed", spec.agent_id, exc_info=True)
        result.status = SubAgentStatus.FAILED
        result.error = str(exc)
        writer(
            events.agent_status(
                agent_id=spec.agent_id,
                role=spec.role,
                label=spec.label,
                status="failed",
                detail=result.error,
            )
        )
        return result

    writer(
        events.agent_status(
            agent_id=spec.agent_id,
            role=spec.role,
            label=spec.label,
            status="done",
            detail=f"{result.steps} step(s), {len(result.proposed_actions)} proposed",
        )
    )
    return result


async def _loop(
    spec: SubAgentSpec,
    task: str,
    *,
    deps: AgentDeps,
    persona: str,
    writer: WriterFn,
    context: str,
    working_memory: str,
    trace_id: str | None,
    retry: Any,  # noqa: ANN401 - langgraph RetryPolicy | None
    result: SubAgentResult,
) -> None:
    """The bounded ReAct loop itself, mutating ``result`` as it goes.

    Kept separate from :func:`run_subagent` so the timeout wrapper has something to
    cancel and every partial finding made before the cut is still on ``result``.
    """
    definitions = allowed_tool_definitions(spec, deps, persona)
    allowed = {_tool_name(d) for d in definitions}
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _system_prompt(spec, working_memory)},
        {"role": "user", "content": _user_prompt(task, context)},
    ]

    with span(
        SpanKind.AGENT,
        f"subagent.{spec.role}",
        attributes={
            semconv.A2A_FROM: "supervisor",
            semconv.A2A_TO: spec.role,
            semconv.A2A_REASON: task,
            semconv.A2A_PROTOCOL: "a2a",
        },
    ):
        for step in range(1, max(1, spec.max_steps) + 1):
            result.steps = step
            writer(
                events.agent_status(
                    agent_id=spec.agent_id,
                    role=spec.role,
                    label=spec.label,
                    status="thinking",
                    detail=f"step {step}/{spec.max_steps}",
                )
            )
            completion = await call_with_retry(
                lambda: deps.complete(
                    spec.model_role, messages, tools=definitions or None
                ),
                policy=retry,
                label=f"Sub-agent {spec.agent_id}",
            )
            _accrue(result, completion.usage)
            for sentence in _sentences(completion.content or ""):
                writer(events.reasoning(sentence))

            calls = list(completion.tool_calls or [])
            if not calls:
                result.findings = completion.content or ""
                return

            executable, proposed, refused = _partition_calls(calls, deps, allowed)
            messages.append(
                {
                    "role": "assistant",
                    "content": completion.content or "",
                }
            )
            for call in refused:
                messages.append(
                    _tool_message(
                        call,
                        f"'{call.name}' is not available to this agent; it was not run.",
                    )
                )
            for call in proposed:
                risk = deps.tool_risk(call.name)
                # Namespaced by agent: several lanes routinely hand back the same
                # provider-generated call id, and these ids become the main graph's
                # ``tool_calls``, where a collision would silently drop one proposal.
                proposal_id = f"{spec.agent_id}:{call.id or uuid.uuid4().hex}"
                result.proposed_actions.append(
                    {
                        "id": proposal_id,
                        "name": call.name,
                        "args": dict(call.args or {}),
                        "agent_id": spec.agent_id,
                        "risk": risk.value,
                    }
                )
                # Visible as a PROPOSAL: the console shows the call, and the absence of
                # a matching ``tool_result`` in this lane is the honest signal that
                # nothing ran here. It runs — or does not — at the one human gate.
                writer(events.tool_call(proposal_id, call.name, dict(call.args or {}), risk))
                messages.append(
                    _tool_message(
                        call,
                        f"'{call.name}' is a {risk.value}-risk action. It has been "
                        "proposed for human approval and will be executed by the main "
                        "graph if approved. Continue without its result.",
                    )
                )
            if executable:
                writer(
                    events.agent_status(
                        agent_id=spec.agent_id,
                        role=spec.role,
                        label=spec.label,
                        status="acting",
                        detail=", ".join(c.name for c in executable),
                    )
                )
            for call in executable:
                summary = await _execute(
                    call,
                    spec=spec,
                    deps=deps,
                    persona=persona,
                    writer=writer,
                    trace_id=trace_id,
                    result=result,
                )
                messages.append(_tool_message(call, summary))

        # Step cap reached with the model still wanting tools: finalise on what we have
        # rather than looping. The cap is what guarantees termination.
        result.findings = _last_assistant_text(messages)


async def _execute(
    call: Any,  # noqa: ANN401 - ToolCallResult duck-type
    *,
    spec: SubAgentSpec,
    deps: AgentDeps,
    persona: str,
    writer: WriterFn,
    trace_id: str | None,
    result: SubAgentResult,
) -> str:
    """Execute ONE within-ceiling tool call, recording and streaming its outcome.

    Only ever reached for a call :func:`_partition_calls` classified as executable —
    i.e. inside the persona ∩ spec intersection and strictly below ``gate_min_risk``.
    """
    risk = deps.tool_risk(call.name)
    args = dict(call.args or {})
    writer(events.tool_call(call.id or "", call.name, args, risk))
    with span(
        SpanKind.TOOL,
        f"tool.{call.name}",
        attributes={semconv.TOOL_NAME: call.name, semconv.TOOL_RISK: risk.value},
    ) as tool_span:
        try:
            outcome = await deps.run_tool(
                persona,
                call.name,
                args,
                actor=persona,
                model=None,
                trace_id=trace_id,
                approver=None,
            )
            ok, summary = bool(outcome.ok), str(outcome.summary)
        except BudgetExceededError:
            raise
        except Exception as exc:  # noqa: BLE001 - a tool failure is a result, not a crash
            ok, summary = False, f"Tool error: {exc}"
        tool_span.set_attribute(semconv.TOOL_OK, ok)
    writer(events.tool_result(call.id or "", ok, summary))
    result.tool_calls.append(
        {
            "id": call.id,
            "name": call.name,
            "args": args,
            "ok": ok,
            "summary": summary,
            "agent_id": spec.agent_id,
        }
    )
    return summary


def _tool_message(call: Any, content: str) -> dict[str, Any]:  # noqa: ANN401 - ToolCallResult
    """Build the ``tool``-role reply the loop feeds back to the model."""
    return {"role": "tool", "tool_call_id": call.id or "", "content": content}


def _last_assistant_text(messages: list[dict[str, Any]]) -> str:
    """Return the most recent assistant text in ``messages`` (empty when there is none)."""
    for message in reversed(messages):
        if message.get("role") == "assistant" and message.get("content"):
            return str(message["content"])
    return ""


def _system_prompt(spec: SubAgentSpec, working_memory: str) -> str:
    """Compose the lane's system prompt: the spec's floor + the user's durable facts.

    The memory block is the SAME one the main graph assembled through the adapter's
    selector — a sub-agent that cannot see the user's durable facts would be a worse
    agent than the single one it replaced, and re-selecting it here would be a second
    selector in the codebase.
    """
    parts = [spec.system_prompt.strip()]
    if working_memory.strip():
        parts.append(f"What you know about this user:\n{working_memory.strip()}")
    parts.append(
        "You are ONE agent in a concurrent team; answer only your own sub-task and say "
        "plainly what you could not establish. Any high-risk action you want taken must "
        "be requested as a tool call — it will be routed to a human for approval, not "
        "executed by you."
    )
    return "\n\n".join(parts)


def _user_prompt(task: str, context: str) -> str:
    """Compose the lane's user turn from its sub-task and the shared retrieval pool."""
    if context.strip():
        return (
            f"Shared retrieved context for this run:\n{context.strip()}\n\n"
            f"Your sub-task: {task}"
        )
    return f"Your sub-task: {task}"


def _accrue(result: SubAgentResult, usage: Any) -> None:  # noqa: ANN401 - Usage duck-type
    """Add one model call's spend to this lane's running totals."""
    result.prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
    result.completion_tokens += int(getattr(usage, "completion_tokens", 0) or 0)
    result.cost_usd += float(getattr(usage, "cost_usd", 0.0) or 0.0)
