"""The in-flight run's identity, threaded through :mod:`contextvars`.

Spend is metered at one chokepoint (:func:`aegis.gateway.complete` / ``embed`` /
``transcribe``) that is called from deep inside graph nodes, tool wrappers and
guardrail screens. None of those signatures carries a run id, and threading one
through all of them would be a change to every node in the graph.

So the run id is bound once, at the top of a run, and read at the chokepoint — the
identical seam :mod:`aegis.governance.context` already uses for the tenant. Nothing
else about the gateway changes.

**Why this exists at all.** ``usage_ledger`` used to attach spend to a run only via
``trace_id``, and that link is not the same fact:

* a trace is an *observability* identity. It is minted by the tracer, it is shared by
  everything that happens under one root span, and when there is no tracer configured
  the orchestrator falls back to using the run id as the trace id — so the two names
  agree by accident on one deployment and not on another;
* measured on real traffic (``taif_run1``, tenant 1): **173 of 1932** ledger rows —
  8.95% of them, $0.104562 — carried a trace that matched no ``runs`` row at all, and
  every single one of the 95 runs that *did* match disagreed with the ledger sum for
  its trace. Attribution through a join key that is only sometimes the same thing is
  not attribution.

A ``ContextVar`` and not an argument, and not a global: each asyncio task copies the
context at creation, so a run's id cannot leak into a *different* request's task, and
LangGraph's node tasks — created while the var is bound — inherit it exactly as they
inherit the governance context.

``None`` is a real answer and the important one: a model call made by a background
job, an ingest pipeline, the chat endpoint or a platform probe belongs to **no run**,
and the ledger row it writes must say so rather than being guessed onto the nearest
run. See :class:`aegis.governance.models.UsageLedger` for what a NULL ``run_id`` is
allowed to mean downstream.

It lives in ``aegis.core`` — stdlib only — rather than in :mod:`aegis.runs`, which is
where it conceptually belongs, for one mechanical reason: the reader is
:mod:`aegis.gateway.llm`, and importing ``aegis.runs`` pulls SQLAlchemy and the whole
governance/jobs ORM into a gateway that deliberately depends on neither. It is
re-exported from :mod:`aegis.runs` so it is still findable where it belongs.
"""

from __future__ import annotations

import dataclasses
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

__all__ = [
    "RunUsage",
    "accrue_run_usage",
    "bind_run_id",
    "current_run_id",
    "reset_run_id",
    "run_scope",
    "run_usage",
]


#: The process-wide slot holding the in-flight run's id. ``None`` — the default —
#: means "this call belongs to no agent run", which is a fact, not a gap.
_run_id: ContextVar[str | None] = ContextVar("aegis_run_id", default=None)


def current_run_id() -> str | None:
    """Return the run id bound for the current context, or ``None``.

    Returns:
        The in-flight run's id, or ``None`` when this call belongs to no run (a job, an
        ingest pass, a platform probe). ``None`` is never a placeholder for "unknown".
    """
    return _run_id.get()


def bind_run_id(run_id: str | None) -> Token[str | None]:
    """Bind ``run_id`` for the current context and return a reset token.

    Args:
        run_id: The run whose spend the calls that follow belong to.

    Returns:
        A token for :func:`reset_run_id`. Prefer :func:`run_scope`, which cannot be
        left unbalanced.
    """
    return _run_id.set(run_id)


def reset_run_id(token: Token[str | None]) -> None:
    """Restore the run id to whatever ``token`` captured."""
    _run_id.reset(token)


@contextmanager
def run_scope(run_id: str | None) -> Iterator[str | None]:
    """Bind ``run_id`` for the duration of a block, and always unbind it.

    The failure mode of a forgotten ``reset`` is not a crash: the id simply *stays*
    bound, and the next unit of work on that task — a different run's, or none —
    has its spend filed under the previous one. That is the same class of bug as a
    leaked governance context, so it gets the same shape of fix.

    Args:
        run_id: The run to attribute the block's model calls to.

    Yields:
        The bound id, so a caller can log or assert on it.
    """
    token = bind_run_id(run_id)
    if run_id is not None:
        _open_usage(run_id)
    try:
        yield run_id
    finally:
        reset_run_id(token)


# ─────────────────────────────────────────────────────────────────────────────
# What the run actually spent, accrued at the same chokepoint that meters it
# ─────────────────────────────────────────────────────────────────────────────


@dataclasses.dataclass
class RunUsage:
    """One run's spend as the gateway metered it — the ledger's own numbers.

    Mutable and shared by reference: the chokepoint adds to it from whichever task made
    the call, and the orchestrator reads the same object when it closes the run.

    Attributes:
        prompt_tokens: Prompt tokens across every metered call of the run.
        completion_tokens: Completion tokens across every metered call of the run.
        cost_usd: What those calls cost.
        calls: How many metered calls there were. ``0`` is the load-bearing value: it
            means the metering chokepoint saw nothing for this run (an offline/lite
            deployment, or a test whose ``complete`` is a stub), which is *not* the
            same statement as "this run cost nothing" and must not be reported as one.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0


#: How many finished runs' totals stay readable. The orchestrator reads a run's totals
#: at its terminal event, and a *resumed* run reads them again minutes later from the
#: same process — so entries cannot be dropped at scope exit. They are bounded instead:
#: oldest-first eviction, because the run that is going to be resumed is a recent one.
_MAX_TRACKED_RUNS = 2048

#: run_id → the run's accrued usage. Process-local by design: this is the same number
#: the durable ``usage_ledger`` row carries (both are written from
#: ``aegis.gateway.llm._record_usage``), reachable without a database round-trip on the
#: hot path that closes a run.
_RUN_USAGE: OrderedDict[str, RunUsage] = OrderedDict()


def _open_usage(run_id: str) -> RunUsage:
    """Return ``run_id``'s accumulator, creating (and LRU-trimming) it if needed."""
    usage = _RUN_USAGE.get(run_id)
    if usage is None:
        usage = RunUsage()
        _RUN_USAGE[run_id] = usage
        while len(_RUN_USAGE) > _MAX_TRACKED_RUNS:
            _RUN_USAGE.popitem(last=False)
    else:
        _RUN_USAGE.move_to_end(run_id)
    return usage


def accrue_run_usage(
    *, prompt_tokens: int, completion_tokens: int, cost_usd: float
) -> None:
    """Add one metered model call to the in-flight run's running total.

    Called from :func:`aegis.gateway.llm._record_usage` — the one place a call's cost is
    known — with the *same* numbers the ``usage_ledger`` row is written from, so the two
    cannot drift into disagreement the way the graph's per-node accrual did.

    **Why this exists.** The terminal ``run_finished`` event used to report the totals
    LangGraph's state reducers had folded up, and those miss every model call made
    outside a node's returned usage — the guardrail screens, the classifier, the
    grounding self-check. Measured on ``taif_run1``: a completed run reported
    ``$0.0172955`` against ``$0.0205096`` over 24 ledger rows, and a run that ended
    BLOCKED or ERROR reported ``$0.0000`` against a ledger that held real spend.

    A call that belongs to no run (a job, an ingest pass, a platform probe) accrues
    nowhere, which is the same statement its NULL ``usage_ledger.run_id`` makes.
    """
    run_id = current_run_id()
    if run_id is None:
        return
    usage = _open_usage(run_id)
    usage.prompt_tokens += int(prompt_tokens)
    usage.completion_tokens += int(completion_tokens)
    usage.cost_usd += float(cost_usd)
    usage.calls += 1


def run_usage(run_id: str | None) -> RunUsage | None:
    """Return what the gateway metered for ``run_id``, or ``None`` if it is unknown.

    ``None`` means "not measured here" — a run this process never opened a scope for
    (it was resumed in a different worker, or evicted from the bounded window). It is
    deliberately distinct from a :class:`RunUsage` with ``calls == 0``, which means the
    scope was open and the chokepoint metered nothing. Neither is "$0.00".
    """
    if run_id is None:
        return None
    return _RUN_USAGE.get(run_id)
