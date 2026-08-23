"""The fan-out: N sub-agents, concurrently, **inside one graph node**.

Why one node and not a subgraph. A probe against the installed langgraph confirmed
``get_stream_writer()`` propagates through contextvars into ``asyncio.gather``-spawned
tasks, so three concurrent workers inside a single node emit live, interleaved custom
events. Subgraphs would also work — and would change ``astream``'s yielded tuple from
``(mode, chunk)`` to ``(namespace, mode, chunk)``, rewriting the orchestrator hot loop
*including* the ``__interrupt__`` detection and the ``get_state()`` call that make the
human gate durable. That is high blast radius on the one piece of code that must not
break, in exchange for nothing. So: ``asyncio.gather`` inside one node.

The constraint that falls out is enforced in :mod:`aegis.agent.subagent`: **no
``interrupt()`` inside a gathered task.** Sub-agents propose; the main graph's single
``gate → approval → act`` path executes. This module therefore hands its aggregated
proposals to the existing ``tool_calls`` state key and changes nothing about the gate.

Three rules govern the gather itself:

1. **A lane's failure is a value, never a raise.** One agent's failure must never cancel
   its siblings — that is the whole difference between graceful degradation and a run
   that dies because one lane hit a slow provider. Each lane is its own task and is
   waited on individually, so the wall clock can cut a lane that is still running and
   can never erase the result — or the ``BudgetExceededError`` — of one that finished.
2. **The node returns ONE summed delta.** Because the gather is inside a node, the node
   contributes a single ``{prompt_tokens, completion_tokens, cost_usd}`` and the
   existing ``operator.add`` reducers keep working untouched.
3. **One shared retrieval pool per run** (:class:`SharedRetrievalPool`). Four agents
   must not retrieve the tenant's chunks four times. This is the supply-side
   optimisation the platform makes *instead* of restricting what the user may ask for.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from aegis.core.models import ModelRole
from aegis.gateway.types import BudgetExceededError

from .deps import AgentDeps
from .retry import call_with_retry
from .subagent import (
    SubAgentResult,
    SubAgentSpec,
    SubAgentStatus,
    WriterFn,
    run_subagent,
)

__all__ = [
    "SharedRetrievalPool",
    "TeamOutcome",
    "TeamPlan",
    "TeamTask",
    "build_team",
    "plan_team_tasks",
    "run_team",
    "synthesise",
    "synthesis_note",
]

logger = logging.getLogger(__name__)

#: Seconds between successive launches, so N agents do not hit the gateway as a burst.
_STAGGER_S = 0.25

#: How long the whole run's shared retrieval may take before the lanes proceed without
#: it. A degraded pool is context-free agents, not a failed run.
_POOL_TIMEOUT_S = 20.0


@dataclass(frozen=True)
class TeamTask:
    """One roster agent bound to the sub-question it owns this turn."""

    spec: SubAgentSpec
    task: str


@dataclass(frozen=True)
class TeamPlan:
    """The dispatched sub-tasks **and what deciding them cost**.

    The planner's split is one cheap model call per team turn, made on the tenant's
    behalf. It used to be spent and thrown away: the usage never left
    :func:`_split_query`, so the run's reported total — and therefore every budget check
    downstream of it — was short by exactly the planner's spend on every fan-out. A
    model call the totals cannot see is a model call the cap cannot stop, so the usage
    comes back with the tasks rather than being an optional thing a caller may ask for.
    """

    tasks: list[TeamTask]
    usage: Any = None


@dataclass
class TeamOutcome:
    """Everything one fan-out produced, ready to fold into a single state delta."""

    results: list[SubAgentResult] = field(default_factory=list)
    #: A tenant-cap failure captured from a gathered task, re-raised after fan-in.
    budget_error: BudgetExceededError | None = None

    @property
    def contributing(self) -> list[SubAgentResult]:
        """The lanes whose findings the synthesis can actually use."""
        return [r for r in self.results if r.contributed]

    @property
    def omitted(self) -> list[SubAgentResult]:
        """The lanes that produced nothing usable — named, never silently dropped."""
        return [r for r in self.results if not r.contributed]

    @property
    def proposed_actions(self) -> list[dict[str, Any]]:
        """Every lane's gate-bound proposals, in lane order."""
        return [p for r in self.results for p in r.proposed_actions]

    def totals(self) -> dict[str, Any]:
        """Return the ONE summed usage delta this node contributes to graph state."""
        return {
            "prompt_tokens": sum(r.prompt_tokens for r in self.results),
            "completion_tokens": sum(r.completion_tokens for r in self.results),
            "cost_usd": sum(r.cost_usd for r in self.results),
        }


class SharedRetrievalPool:
    """One retrieval per run, shared by every lane — Amendment A's supply-side rule.

    Fanning out four agents must not retrieve the tenant's chunks four times. The pool
    performs **exactly one** ``deps.retrieve`` call for the run's query and hands the
    same context to every lane, however many lanes there are and however concurrently
    they ask for it (the lock is what makes "however concurrently" true rather than a
    hopeful comment).

    A retrieval failure degrades to empty context and is logged: agents without the
    corpus are worse agents, but a failed pool must not be a failed run.

    **The whole result is kept, not just the context string.** The pool used to reduce
    the retrieval to ``answer_context`` and drop everything else on the floor, so a
    fan-out's sources, scores, provenance and graph delta existed for the length of one
    expression and were never reported — which is why a team run emitted no ``retrieval``
    event and the console told the user the answer was grounded in nothing. The lanes
    still consume only the context; :attr:`result` is what the caller reports from.
    """

    def __init__(self, deps: AgentDeps, query: str, scope: Any) -> None:  # noqa: ANN401 - RetrievalScope
        """Bind the pool to one run's query and tenant scope (nothing is fetched yet)."""
        self._deps = deps
        self._query = query
        self._scope = scope
        self._lock = asyncio.Lock()
        self._context: str | None = None
        #: The retrieval this run performed, or ``None`` if it has not run yet or
        #: failed. ``None`` is the signal NOT to emit a retrieval event: a ``done``
        #: carrying zeroes would claim we looked and found nothing, which is a
        #: different (and false) statement from "the retrieval never completed".
        self.result: Any = None
        self.calls = 0

    async def context(self) -> str:
        """Return the shared context, retrieving it at most once for the whole run."""
        if self._context is not None:
            return self._context
        async with self._lock:
            if self._context is not None:
                return self._context
            try:
                self.calls += 1
                result = await asyncio.wait_for(
                    self._deps.retrieve(self._query, scope=self._scope),
                    timeout=_POOL_TIMEOUT_S,
                )
                self._context = str(getattr(result, "answer_context", "") or "")
                self.result = result
            except Exception:  # noqa: BLE001 - a degraded pool is not a failed run
                logger.warning(
                    "Shared retrieval pool unavailable; agents run context-free",
                    exc_info=True,
                )
                self._context = ""
            return self._context


def _coerce_spec(entry: Any, index: int, config: Any) -> SubAgentSpec | None:  # noqa: ANN401 - roster entry
    """Normalise one adapter roster entry into a :class:`SubAgentSpec`.

    The roster is host data read defensively, exactly as the specialist roster is: a
    malformed entry is skipped with a warning rather than failing a run, because a bad
    roster row must not be able to take the agent offline.
    """
    if isinstance(entry, SubAgentSpec):
        spec = entry
    else:
        try:
            spec = SubAgentSpec(
                agent_id=str(getattr(entry, "agent_id", "") or f"agent-{index + 1}"),
                role=str(entry.role),
                label=str(getattr(entry, "label", "") or entry.role),
                system_prompt=str(getattr(entry, "system_prompt", "") or ""),
                prompt_key=str(getattr(entry, "prompt_key", "") or ""),
                tool_allowlist=frozenset(getattr(entry, "tool_allowlist", ()) or ()),
                model_role=getattr(entry, "model_role", ModelRole.CHEAP),
            )
        except Exception:  # noqa: BLE001 - one bad roster row, not a broken run
            logger.warning("Skipping unreadable sub-agent roster entry %r", entry,
                           exc_info=True)
            return None
    # The bounds are the PLATFORM's, never the roster's: a roster entry cannot grant
    # itself more steps or a longer wall clock than the tenant's config allows.
    return SubAgentSpec(
        agent_id=spec.agent_id,
        role=spec.role,
        label=spec.label,
        system_prompt=spec.system_prompt,
        prompt_key=spec.prompt_key,
        tool_allowlist=spec.tool_allowlist,
        model_role=spec.model_role,
        max_steps=min(spec.max_steps, config.subagent_max_steps),
        timeout_s=min(spec.timeout_s, config.subagent_timeout_s),
    )


def build_team(deps: AgentDeps, width: int) -> list[SubAgentSpec]:
    """Return the ``width`` sub-agents this run fans out to, in roster order.

    Reads ``deps.subagent_roster`` defensively: no hook, a hook that raises, or an empty
    roster all yield ``[]``, which the caller turns into a SINGLE-pass run. The core
    never invents a roster — a fan-out to agents the host never declared would be the
    platform making a spend decision on the tenant's behalf.
    """
    provider = deps.subagent_roster
    if provider is None:
        return []
    try:
        entries = list(provider() or ())
    except Exception:  # noqa: BLE001 - an optional adapter contract, read defensively
        logger.warning("Sub-agent roster hook failed; running single-pass", exc_info=True)
        return []
    specs = [
        spec
        for index, entry in enumerate(entries)
        if (spec := _coerce_spec(entry, index, deps.config)) is not None
    ]
    return _with_unique_ids(specs)[: max(0, width)]


def _with_unique_ids(specs: list[SubAgentSpec]) -> list[SubAgentSpec]:
    """Return ``specs`` with every ``agent_id`` distinct, renaming collisions loudly.

    ``agent_id`` is the key the fan-out's result slots, the per-agent harness records
    and the ``run_events.agent_id`` column are all keyed by. Two roster rows sharing one
    collapsed both lanes onto the *second* spec — the first row's remit was silently
    discarded, the same result object landed in ``outcome.results`` twice, and every
    total it carried was counted twice. It was the one malformed-roster case that did
    not warn.

    A collision is repaired rather than dropped: the roster declared two distinct remits
    and both are work the tenant asked for, so the second keeps its remit under a
    suffixed id. Only the id is invented, and the warning says so.
    """
    seen: set[str] = set()
    unique: list[SubAgentSpec] = []
    for spec in specs:
        agent_id = spec.agent_id
        if agent_id in seen:
            suffix = 2
            while f"{agent_id}-{suffix}" in seen:
                suffix += 1
            renamed = f"{agent_id}-{suffix}"
            logger.warning(
                "Sub-agent roster declares agent_id %r more than once; the %r entry "
                "runs as %r so both lanes keep their own remit and neither is "
                "double-counted.",
                agent_id,
                spec.role,
                renamed,
            )
            spec = replace(spec, agent_id=renamed)  # noqa: PLW2901
            agent_id = renamed
        seen.add(agent_id)
        unique.append(spec)
    return unique


async def plan_team_tasks(
    query: str,
    specs: Sequence[SubAgentSpec],
    *,
    deps: AgentDeps,
    retry: Any = None,  # noqa: ANN401 - langgraph RetryPolicy | None
) -> TeamPlan:
    """Turn the query + the chosen agents into one sub-task each, and report the cost.

    One cheap model call, with a **deterministic fallback**: when the model is absent,
    fails, or returns something unreadable, every agent gets the original query framed
    by its own remit. That fallback is a working team, not a degraded one — which is
    why the model call is allowed to be best-effort. Best-effort is not the same as
    free, so the call's usage rides back on the :class:`TeamPlan` even when its answer
    was discarded: the tenant was billed for the attempt either way.
    """
    fallback = [TeamTask(spec=spec, task=query) for spec in specs]
    if not specs:
        return TeamPlan(tasks=[])
    usage: Any = None
    try:
        lines, usage = await call_with_retry(
            lambda: _split_query(query, specs, deps),
            policy=retry,
            label="Team planner",
        )
    except BudgetExceededError:
        raise
    except Exception:  # noqa: BLE001 - the planner must never be why a run dies
        logger.warning("Team task planner failed; using the whole query per agent",
                       exc_info=True)
        return TeamPlan(tasks=fallback)
    if not lines:
        return TeamPlan(tasks=fallback, usage=usage)
    tasks: list[TeamTask] = []
    for index, spec in enumerate(specs):
        tasks.append(
            TeamTask(spec=spec, task=lines[index] if index < len(lines) else query)
        )
    return TeamPlan(tasks=tasks, usage=usage)


async def _split_query(
    query: str, specs: Sequence[SubAgentSpec], deps: AgentDeps
) -> tuple[list[str], Any]:
    """Ask one cheap model for one sub-task per agent; return ``(lines, usage)``."""
    menu = "\n".join(f"{i + 1}. {s.role} — {s.label}" for i, s in enumerate(specs))
    result = await deps.complete(
        ModelRole.CHEAP,
        [
            {
                "role": "system",
                "content": (
                    "You split a request into independent sub-tasks for a team of "
                    f"{len(specs)} agents. Reply with exactly {len(specs)} lines, one "
                    "per agent in the order listed, each a single self-contained "
                    "instruction for that agent. No numbering, no preamble.\n"
                    f"Agents:\n{menu}"
                ),
            },
            {"role": "user", "content": query},
        ],
    )
    usage = getattr(result, "usage", None)
    lines = [
        line.strip(" -•\t")
        for line in (getattr(result, "content", "") or "").splitlines()
        if line.strip()
    ]
    return (lines if len(lines) >= len(specs) else []), usage


async def run_team(
    tasks: Sequence[TeamTask],
    *,
    deps: AgentDeps,
    persona: str,
    writer: WriterFn,
    pool: SharedRetrievalPool | None = None,
    working_memory: str = "",
    trace_id: str | None = None,
    retry: Any = None,  # noqa: ANN401 - langgraph RetryPolicy | None
) -> TeamOutcome:
    """Run every task concurrently and fan in. Never cancels a sibling, never raises.

    Args:
        tasks: The agents and the sub-questions they own.
        deps: The already-wired capabilities.
        persona: The run's persona (the other half of every tool intersection).
        writer: The node's stream writer. Each lane gets a copy of it **bound to that
            lane's ``agent_id``**, so every event a sub-agent emits carries its identity
            automatically and no call site has to remember to stamp it.
        pool: The run's shared retrieval pool (one retrieval, N readers).
        working_memory: The user's rendered profile, selected once by the adapter.
        trace_id: Correlation id for tool audit rows.
        retry: The graph's model retry policy, extended to sub-agent model calls.

    Returns:
        A :class:`TeamOutcome` holding one result per task — including for the lanes
        that timed out or were killed, which are terminal states, not absences.
    """
    if not tasks:
        return TeamOutcome()

    semaphore = asyncio.Semaphore(max(1, deps.config.max_concurrent_agents))
    # Pre-seeded so that even a lane the wall clock cancels before it can return has a
    # result. Without this, a wall-clock cut would produce an agent that simply is not
    # in the synthesis — which reads to an audience exactly like a bug.
    slots: dict[str, SubAgentResult] = {
        t.spec.agent_id: SubAgentResult(
            agent_id=t.spec.agent_id,
            role=t.spec.role,
            label=t.spec.label,
            status=SubAgentStatus.CANCELLED,
            error="the team's wall clock elapsed before this agent finished",
        )
        for t in tasks
    }

    async def _lane(index: int, task: TeamTask) -> SubAgentResult:
        # Staggered launches: N agents starting in the same millisecond is a burst
        # against a shared gateway, and a burst is how a fan-out discovers rate limits.
        if index:
            await asyncio.sleep(_STAGGER_S * index)
        async with semaphore:
            context = await pool.context() if pool is not None else ""
            result = await run_subagent(
                task.spec,
                task.task,
                deps=deps,
                persona=persona,
                writer=_scoped_writer(writer, task.spec.agent_id),
                context=context,
                working_memory=working_memory,
                trace_id=trace_id,
                retry=retry,
            )
        slots[task.spec.agent_id] = result
        return result

    # One task per lane, waited on INDIVIDUALLY rather than through a single
    # ``wait_for(gather(...))``. That shape looks equivalent and is not: a wall-clock
    # timeout cancels the gather, and with it every result and every exception the
    # lanes had already produced — including the tenant's ``BudgetExceededError``. The
    # run then completed, billed, past a cap the platform had already refused. Waiting
    # per task means the wall clock can cut a lane that is still running and cannot
    # erase one that has already finished.
    lanes = [
        asyncio.create_task(_lane(index, task), name=f"lane:{task.spec.agent_id}")
        for index, task in enumerate(tasks)
    ]
    _done, pending = await asyncio.wait(lanes, timeout=deps.config.team_wall_clock_s)
    if pending:
        logger.warning(
            "Team wall clock of %.0fs elapsed; cutting %d lane(s) and finishing with "
            "the ones that landed",
            deps.config.team_wall_clock_s,
            len(pending),
        )
        for task in pending:
            task.cancel()
        # Awaited so each cut lane runs its own ``CancelledError`` handler and records a
        # terminal state before the synthesis reads it.
        await asyncio.gather(*pending, return_exceptions=True)
        # The lane only knows it was cancelled; the fan-out knows WHY. Naming the cause
        # here is the difference between a synthesis that says "was cut short (cancelled
        # mid-run)" — which reads like a bug — and one that says the wall clock elapsed.
        by_lane = dict(zip(lanes, tasks, strict=True))
        for task in pending:
            slot = slots[by_lane[task].spec.agent_id]
            if slot.status is SubAgentStatus.CANCELLED:
                slot.error = (
                    f"the team's wall clock of {deps.config.team_wall_clock_s:g}s "
                    "elapsed before this agent finished"
                )

    outcome = TeamOutcome(results=[slots[t.spec.agent_id] for t in tasks])
    for task in lanes:
        # A lane's failure is a VALUE here, never a raise: one lane must never cancel a
        # sibling. The tenant's own cap is the only one that may end the run, and it does
        # so after fan-in — so the siblings still finish and the orchestrator's existing
        # handler still sees it.
        if task.cancelled():
            continue
        item = task.exception()
        if isinstance(item, BudgetExceededError):
            outcome.budget_error = outcome.budget_error or item
        elif item is not None:
            logger.warning("A sub-agent lane raised %r after fan-in", item)
    return outcome


def _scoped_writer(writer: WriterFn, agent_id: str) -> WriterFn:
    """Bind ``agent_id`` onto every event one lane emits.

    Identity is stamped at the seam rather than at each call site, for the same reason
    the orchestrator stamps ``run_id``/``seq`` at its seam: a field every emitter has to
    remember is a field that will be missing somewhere.
    """

    def _write(payload: dict[str, Any]) -> Any:  # noqa: ANN401 - passthrough
        return writer({**payload, "agent_id": agent_id})

    return _write


def synthesis_note(outcome: TeamOutcome) -> str:
    """Return the one-line honest account of who contributed and who did not.

    *"synthesised from 3 of 4 agents; the policy agent timed out."* This is not
    politeness. A partial fan-out that says nothing reads as a bug — a card sits
    spinning and the audience concludes the system is broken. Naming the omission is
    what makes the degradation visible **and** graceful.
    """
    total = len(outcome.results)
    if not total:
        return ""
    contributing = len(outcome.contributing)
    note = f"Synthesised from {contributing} of {total} agents"
    reasons = [
        f"the {r.label.lower()} {_omission_phrase(r)}" for r in outcome.omitted
    ]
    if reasons:
        note += "; " + "; ".join(reasons)
    return note + "."


def _omission_phrase(result: SubAgentResult) -> str:
    """Describe one omitted lane's terminal state in words a jury can read."""
    if result.status is SubAgentStatus.TIMEOUT:
        return f"timed out ({result.error})"
    if result.status is SubAgentStatus.CANCELLED:
        return f"was cut short ({result.error})"
    if result.status is SubAgentStatus.FAILED:
        return f"failed ({result.error})"
    return "returned nothing usable"


async def synthesise(
    query: str,
    outcome: TeamOutcome,
    *,
    deps: AgentDeps,
    persona: str,
    working_memory: str = "",
    retry: Any = None,  # noqa: ANN401 - langgraph RetryPolicy | None
) -> tuple[str, Any]:
    """Merge the lanes' findings into one answer. Returns ``(text, usage)``.

    The synthesiser is told to attribute each claim to the agent that produced it, and
    the honest coverage note (:func:`synthesis_note`) is appended **in code** rather
    than asked for in the prompt — a model that forgets to mention the timed-out agent
    would turn a designed terminal state back into an invisible one.
    """
    note = synthesis_note(outcome)
    contributing = outcome.contributing
    if not contributing:
        return (
            f"No agent produced a usable answer for this request. {note}".strip(),
            None,
        )
    findings = "\n\n".join(
        f"[{r.label} ({r.role})]\n{r.findings.strip()}" for r in contributing
    )
    messages = [
        {
            "role": "system",
            "content": deps.render_system_prompt(persona, extra_context=working_memory),
        },
        {
            "role": "user",
            "content": (
                f"Question: {query}\n\n"
                f"Findings from the agents that worked on it:\n{findings}\n\n"
                "Write one answer that merges these findings. Attribute each claim to "
                "the agent that produced it, do not invent anything no agent reported, "
                "and say plainly where the agents disagree or left a gap."
            ),
        },
    ]
    result = await call_with_retry(
        lambda: deps.complete(ModelRole.GENERATION, messages),
        policy=retry,
        label="Team synthesis",
    )
    text = (getattr(result, "content", "") or "").strip()
    return (f"{text}\n\n{note}".strip() if note else text), result
