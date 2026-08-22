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

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

__all__ = ["bind_run_id", "current_run_id", "reset_run_id", "run_scope"]


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
    try:
        yield run_id
    finally:
        reset_run_id(token)
