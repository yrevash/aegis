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

Three more invariants, all enforced here rather than hoped for:

* **Every tool result this lane executes is screened before it enters the lane's own
  context** (:func:`aegis.agent.rails.screen_tool_result`, the ``TOOL_RESULT`` rail).
  A record a tool returns is third-party text the model reads as instructions-adjacent
  context; it is the OWASP LLM01 surface, and it is screened by the same function the
  main graph's ``act`` uses.

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
import json
import logging
import re
import time
import uuid
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from aegis.core.models import ModelRole
from aegis.gateway.types import BudgetExceededError
from aegis.observability import SpanKind, semconv, span

from . import events
from .deps import AgentDeps, risk_at_least
from .rails import screen_tool_result
from .retry import call_with_retry

__all__ = [
    "MAIN_AGENT_ID",
    "SubAgentResult",
    "SubAgentSpec",
    "SubAgentStatus",
    "agent_node_id",
    "current_agent_id",
    "resolve_system_prompt",
    "run_subagent",
]

logger = logging.getLogger(__name__)

#: The agent id a run is on when no fan-out lane has claimed it: the main persona.
#: The same string as :data:`aegis.skills.store.MAIN_AGENT_ID`, restated because
#: importing that module would pull SQLAlchemy into :mod:`aegis.agent`, which is
#: deliberately import-light. ``test_the_main_agent_id_is_one_string`` asserts the two
#: agree, so the copy cannot drift.
MAIN_AGENT_ID = "main"

#: Which lane is executing, for the seams too narrow to carry it as an argument.
_CURRENT_AGENT: ContextVar[str] = ContextVar(
    "aegis_current_agent_id", default=MAIN_AGENT_ID
)


def current_agent_id() -> str:
    """Return the agent id of the lane on this task, or ``main`` outside a fan-out."""
    return _CURRENT_AGENT.get()


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
    #: The lane's trajectory reached its token ceiling. Like ``TIMEOUT`` this is a
    #: DESIGNED terminal state and not an error: what the lane found so far is kept and
    #: the synthesis names it as cut short. Aegis has no trajectory compaction, so this
    #: is the bound that stands in for it — and a lane that stops at a stated ceiling is
    #: a different thing from one that fails at a context-window error nobody predicted.
    CEILING = "ceiling"
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
        system_prompt: The **floor** prompt — the adapter's shipped string. The lane
            actually sends the registry's ACTIVE version for :attr:`prompt_key` when one
            exists (§5.9b), and this when it does not, exactly as the main persona
            prompt already behaves.
        prompt_key: The LLM-Ops registry key this agent's system prompt is versioned
            under. Empty ⇒ the derived default ``subagent:<role>``, so a roster entry
            gets a registry identity without having to remember to declare one.
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
    prompt_key: str = ""
    tool_allowlist: frozenset[str] = frozenset()
    model_role: ModelRole = ModelRole.CHEAP
    max_steps: int = 4
    timeout_s: float = 45.0

    @property
    def registry_key(self) -> str:
        """Return the ``prompt_key`` this agent's system prompt is versioned under.

        Derived from the role when the roster entry names none, so every sub-agent is
        addressable by the LLM-Ops loop by construction rather than by discipline.
        """
        return self.prompt_key or f"subagent:{self.role}"


def resolve_system_prompt(spec: SubAgentSpec, deps: AgentDeps) -> tuple[str, int | None]:
    """Return ``(system_prompt, version)`` for one lane: registry ACTIVE, else the floor.

    The same resolution order the main persona prompt already uses — an ACTIVE
    :class:`~aegis.ops.models.PromptVersion` wins, and the adapter's shipped string is
    the floor. It is read through the injected ``deps.active_prompt`` seam rather than by
    importing :mod:`aegis.ops` (which pulls SQLAlchemy) so ``aegis.agent`` stays
    import-light, and every failure mode — no seam, no active version, a blank version,
    a raising registry — resolves to the floor. **A registry outage degrades to the
    shipped prompt, never to none.**
    """
    reader = deps.active_prompt
    if reader is None:
        return spec.system_prompt, None
    try:
        active = reader(spec.registry_key)
    except Exception:  # noqa: BLE001 - a registry read must never be why a lane dies
        logger.warning(
            "Prompt registry read failed for %s; using the adapter floor",
            spec.registry_key,
            exc_info=True,
        )
        return spec.system_prompt, None
    if not active:
        return spec.system_prompt, None
    prompt, _config, version = active
    if not str(prompt or "").strip():
        return spec.system_prompt, None
    return str(prompt), int(version)


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
        model: The model this lane's calls actually resolved to (the last one seen),
            so the per-agent harness record names it rather than the run's.
        prompt_version: The registry version whose prompt was sent, or ``None`` when the
            adapter floor was used.
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
    model: str | None = None
    prompt_version: int | None = None
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
            "model": self.model,
            "prompt_version": self.prompt_version,
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
    # Whose lane this is, for anything downstream that has to resolve per-agent state
    # and is reached through a seam too narrow to carry an argument — ``load_skill``
    # being the case that exists. Set here rather than in ``_loop`` so it covers the
    # tool dispatch as well as the prompt, and reset in a ``finally`` so a lane's
    # identity cannot outlive it. Each gathered lane runs in its own context copy, so
    # siblings never see each other's value.
    token = _CURRENT_AGENT.set(spec.agent_id)
    # The lane is a unit of work, so it reports itself as one: a node_started /
    # node_finished pair through the lane's own writer. That pair is what carries this
    # agent's model, tokens, cost and duration on the WIRE, which is what lets
    # ``run_summary`` fold one record per sub-agent out of the same events the client
    # saw (§5.9a) instead of out of a second bookkeeping path. It reuses the existing
    # event variants rather than inventing a per-agent one — the ``agent_id`` the
    # scoped writer stamps is what separates a lane's pair from the graph's.
    node = agent_node_id(spec.agent_id)
    writer(events.node_started(node, spec.label))
    started = time.perf_counter()
    try:
        return await _guarded(spec, task, deps=deps, persona=persona, writer=writer,
                              context=context, working_memory=working_memory,
                              trace_id=trace_id, retry=retry, result=result)
    finally:
        _CURRENT_AGENT.reset(token)
        writer(
            events.node_finished(
                node,
                spec.label,
                int(round((time.perf_counter() - started) * 1000)),
                model=result.model,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                cost_usd=result.cost_usd,
            )
        )


def agent_node_id(agent_id: str) -> str:
    """Return the ``node`` id one sub-agent's lane reports its work under.

    Namespaced so a lane's node record can never collide with a graph node's, and so a
    reader that only has the node id (rather than the stamped ``agent_id``) can still
    tell the two apart.
    """
    return f"agent:{agent_id}"


async def _guarded(
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
) -> SubAgentResult:
    """Run the lane's loop under its wall clock, turning every failure into a result."""
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
    base_prompt, result.prompt_version = resolve_system_prompt(spec, deps)
    lane_memory = await _lane_working_memory(spec, deps, working_memory)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _system_prompt(spec, base_prompt, lane_memory)},
        {"role": "user", "content": _user_prompt(task, context)},
    ]

    with span(
        SpanKind.AGENT,
        f"subagent.{spec.role}",
        attributes={
            semconv.HANDOFF_FROM: "supervisor",
            semconv.HANDOFF_TO: spec.role,
            semconv.HANDOFF_REASON: task,
            semconv.HANDOFF_SCOPE: "in-process",
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
            # The trajectory ceiling, checked before the call rather than after it.
            # Checking after would mean paying for the call that breached the bound,
            # which is the one call the bound exists to avoid.
            budget = getattr(deps.config, "max_trajectory_tokens", 0)
            if budget:
                # Deferred: `aegis.agent` must import without pulling heavy dependencies
                # — `tests/agent/test_isolation.py` enforces that, and tiktoken sits
                # behind this module. The estimator degrades to len//4 without it, and a
                # monotone estimate is all a ceiling needs.
                from aegis.memory.tokens import count_tokens

                size = count_tokens(json.dumps(messages, default=str))
                if size > budget:
                    result.status = SubAgentStatus.CEILING
                    result.error = (
                        f"the lane stopped at its trajectory ceiling ({size} tokens, "
                        f"limit {budget}); what it found before that is kept."
                    )
                    break

            completion = await call_with_retry(
                lambda: deps.complete(
                    spec.model_role, messages, tools=definitions or None
                ),
                policy=retry,
                label=f"Sub-agent {spec.agent_id}",
            )
            _accrue(result, completion.usage)
            result.model = getattr(completion, "model", None) or result.model
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
                        max_tokens=getattr(deps.config, "max_tool_result_tokens", 0),
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
                # The bound applies here above all: `summary` is whatever the tool
                # returned, and that is the unbounded input a run is actually exposed to.
                messages.append(
                    _tool_message(
                        call,
                        summary,
                        max_tokens=getattr(deps.config, "max_tool_result_tokens", 0),
                    )
                )

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
    # §5.7: the tool's own output is third-party text this lane is about to read as
    # context. It is screened BEFORE it is streamed or fed back to the model, so a
    # poisoned record cannot reach either the transcript or the console verbatim.
    allowed, summary = await screen_tool_result(
        summary, tool_name=call.name, deps=deps, writer=writer
    )
    ok = ok and allowed
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


def _tool_message(
    call: Any,  # noqa: ANN401 - ToolCallResult
    content: str,
    *,
    max_tokens: int = 0,
) -> dict[str, Any]:
    """Build the ``tool``-role reply the loop feeds back to the model.

    ``max_tokens`` bounds ONE result's contribution to the trajectory, and it is the
    bound that bites first in practice: a run's real exposure is one unbounded tool
    result — a search that returns a whole document, a query that matches ten thousand
    rows — not a long conversation. The whole-trajectory ceiling would catch that too,
    but only by ending the lane, when truncating one oversized result would have let it
    finish.

    The truncation is **marked, never silent**. A model handed a quietly shortened result
    will reason confidently about the part it was given, and that is worse than a model
    told plainly that it is looking at the beginning of something longer.

    The full text is not lost: it stays on the result record that the trace and the audit
    read, so the model loses the tail and the evidence does not.
    """
    if max_tokens > 0 and content:
        from aegis.memory.tokens import count_tokens

        size = count_tokens(content)
        if size > max_tokens:
            # Proportional cut on characters — the estimator is monotone, so this lands
            # close enough, and being slightly under a ceiling is the safe direction.
            keep = max(200, int(len(content) * (max_tokens / size)))
            content = (
                content[:keep]
                + f"\n\n[truncated: {size} tokens exceeded the {max_tokens}-token "
                + "ceiling for one tool result; the full text is on the run record]"
            )
    return {"role": "tool", "tool_call_id": call.id or "", "content": content}


def _last_assistant_text(messages: list[dict[str, Any]]) -> str:
    """Return the most recent assistant text in ``messages`` (empty when there is none)."""
    for message in reversed(messages):
        if message.get("role") == "assistant" and message.get("content"):
            return str(message["content"])
    return ""


def _system_prompt(spec: SubAgentSpec, base_prompt: str, working_memory: str) -> str:
    """Compose the lane's system prompt: the resolved base + the user's durable facts.

    ``base_prompt`` is the registry's ACTIVE version for this agent when one exists and
    the adapter's floor when it does not (:func:`resolve_system_prompt`) — the same
    resolution order the main persona prompt uses, so a sub-agent's prompt improves by
    promotion through the eval gate rather than by an edit to this file.

    The memory block is the SAME one the main graph assembled through the adapter's
    selector — a sub-agent that cannot see the user's durable facts would be a worse
    agent than the single one it replaced, and re-selecting it here would be a second
    selector in the codebase.
    """
    parts = [base_prompt.strip() or spec.system_prompt.strip()]
    if working_memory.strip():
        parts.append(f"What you know about this user:\n{working_memory.strip()}")
    parts.append(
        "You are ONE agent in a concurrent team; answer only your own sub-task and say "
        "plainly what you could not establish. Any high-risk action you want taken must "
        "be requested as a tool call — it will be routed to a human for approval, not "
        "executed by you."
    )
    return "\n\n".join(parts)


#: The header the run's working-memory block puts its tier-1 skill cards under
#: (:mod:`aegis.memory.working`). Restated rather than imported because importing it
#: would pull SQLAlchemy into :mod:`aegis.agent`, which is deliberately import-light;
#: :func:`test_the_skills_header_is_the_one_the_assembler_writes` asserts the two are
#: the same string, so the copy cannot drift silently.
SKILLS_HEADER_PREFIX = "## Skills available"


def _strip_skills_section(block: str) -> str:
    """Return ``block`` without its skills section.

    The run assembles ONE working-memory block and hands it to every lane, so the
    section under :data:`SKILLS_HEADER_PREFIX` is the *main* lane's answer to "which
    skills are in force". A lane that has its own answer must not carry the main lane's
    as well, or a skill assigned to one agent would be advertised to all of them.

    Only that section is removed: everything from its header down to the next ``##``
    heading, or to the end. Every other tier the assembler wrote — profile, facts,
    summary, episodic, raw — is left byte for byte where it was.
    """
    lines = block.splitlines()
    out: list[str] = []
    skipping = False
    for line in lines:
        if line.startswith(SKILLS_HEADER_PREFIX):
            skipping = True
            continue
        if skipping:
            if line.startswith("## "):
                skipping = False
            else:
                continue
        out.append(line)
    return "\n".join(out).strip()


async def _lane_working_memory(
    spec: SubAgentSpec, deps: AgentDeps, working_memory: str
) -> str:
    """Return the memory block for THIS lane: the shared one, with its own skills.

    Best-effort by construction. Without the ``deps.skill_cards_for`` seam — and after
    any failure of it — the lane inherits the shared block untouched, which is what
    every lane did before skills could be assigned to an agent and is still right while
    nothing is assigned. A skills outage is never why a lane does not run.
    """
    reader = deps.skill_cards_for
    if reader is None:
        return working_memory
    try:
        cards = [str(card).strip() for card in await reader(spec.agent_id) if str(card).strip()]
    except Exception:  # noqa: BLE001 - a skills read must never be why a lane dies
        logger.warning(
            "Per-agent skills read failed for lane %s; using the run's own block",
            spec.agent_id,
            exc_info=True,
        )
        return working_memory
    stripped = _strip_skills_section(working_memory)
    if not cards:
        return stripped
    section = "\n".join([_lane_skills_header(), *cards])
    return f"{stripped}\n\n{section}".strip()


def _lane_skills_header() -> str:
    """The lane's own tier-1 header — the assembler's sentence, for the same reason."""
    return (
        f"{SKILLS_HEADER_PREFIX} — call the load_skill tool with a name to read one "
        "in full"
    )


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
