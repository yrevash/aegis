"""Make a memory path's tenant scope survive the commits that path makes.

**The defect this module exists to remove.**
:func:`aegis.governance.rls.set_tenant_scope` writes ``app.tenant_id`` /
``app.tenant_all`` with ``set_config(..., is_local => true)``, so Postgres discards them
at the end of the *transaction*. That is deliberate and must not change: a session-level
``SET`` would survive on the pooled connection and hand one tenant's scope to whoever
borrows it next, which is a cross-tenant read — strictly worse than the bug fixed here.

The consequence is that a path which **commits in the middle of its work and keeps
going** continues unscoped. The memory subsystem does exactly that, in more than one
place and on purpose:

* :func:`aegis.memory.recall.recall` commits the read-path access bumps, and the
  assembler then re-reads the raw window on the same session;
* :func:`aegis.memory.consolidate.sweep_pending` commits **per job**, deliberately, so
  one bad job cannot roll back the batch — and then runs the whole consolidation, the
  terminal status update and the forget sweep on that same session;
* :func:`aegis.memory.stream.stream_add` / ``stream_forget`` commit the authoritative
  rows before dropping the derived cache.

Every statement after such a commit ran with no scope bound. Under the fail-open policy
predicate that is invisible (the app-level ``subject_id``/``tenant_id`` predicates still
land every row on the right tenant), which is why it never bit. Under
``RLS_FAIL_CLOSED=true`` those statements match **zero** rows: consolidation would stop
marking jobs DONE and half of recall would come back empty, silently.

**The fix, in one sentence:** re-bind the scope at the start of every transaction on the
session, for as long as the memory work lasts, instead of once per call site.

Why an ``after_begin`` listener rather than the alternatives:

* *``SET SESSION`` with a reset on release* — makes the scope a property of the
  **connection**, so any path that fails to reset (an exception between checkout and
  release, a connection invalidated mid-request, a ``DISCARD`` that never ran) returns a
  live scope to the pool. That is a cross-tenant read, and it is a failure mode this
  approach cannot have: the GUC stays transaction-local, so the *database* forgets the
  scope at every commit and rollback whether or not our code remembers to.
* *A ``set_tenant_scope`` call after each commit* — correct only for the commits that
  exist today. The next commit added to a memory path re-opens the hole, and it re-opens
  it silently. Binding the session covers transactions that do not exist yet.
* *Restructuring so the work does not commit* — right where a single transaction is
  legitimate, wrong for the sweeper: its per-job commit is a durability decision, not an
  accident.

**Scope of the binding.** Once bound, the scope is a property of that session for the
rest of its life (or until it is re-bound). A session must therefore not carry memory
work for one tenant and unbound work for another; a caller that legitimately serves
several tenants on one session re-binds per unit of work, which is exactly what
:func:`aegis.memory.consolidate.sweep_pending` does around each job.

**``tenant_id=None`` is the platform scope**, per ``set_tenant_scope``'s own contract —
not "no scope". Everywhere in this package it is also the *null-tenant* app-level scope
(``tenant_id IS NULL``, see ``_tenant_clause``), so the widened RLS predicate can still
only return rows that belong to no tenant: the app-level predicate is the isolator, and
RLS is the belt. Nothing here relies on RLS to separate two numbered tenants.

Postgres-only and dependency-light: :mod:`aegis.governance` is imported lazily, so
``aegis.memory`` keeps its slim import graph (the same rule
:func:`~aegis.memory.consolidate.sweep_pending` follows for ``governed``), and a build
without the governance extra degrades to a no-op rather than an ImportError.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

#: Key under which a session carries its memory scope holder. The value is a mutable
#: one-item dict so a re-bind can *retarget* the already-registered listener instead of
#: stacking a second one on top of it — ten jobs for ten tenants in one sweep would
#: otherwise leave ten listeners racing, with the registration order deciding the winner.
_HOLDER_KEY = "aegis_memory_scope"


def _session_info(session: AsyncSession) -> dict[str, Any] | None:
    """Return the mutable ``info`` dict of ``session``, or ``None`` if it has none."""
    info = getattr(session, "info", None)
    return info if isinstance(info, dict) else None


def bound_memory_scope(session: AsyncSession) -> tuple[bool, int | None]:
    """Return ``(is_bound, tenant_id)`` for this session's memory scope.

    Exposed for tests and diagnostics: the assertion "this session re-binds its scope
    after every commit" is otherwise only observable by reading Postgres' GUC.

    Args:
        session: The session to inspect.

    Returns:
        ``(False, None)`` when no memory scope has been bound; otherwise ``(True, t)``
        where ``t`` is the bound tenant (``None`` = the platform scope).
    """
    info = _session_info(session)
    holder = info.get(_HOLDER_KEY) if info is not None else None
    if holder is None:
        return False, None
    return True, holder["tenant_id"]


async def bind_memory_scope(session: AsyncSession, tenant_id: int | None) -> None:
    """Bind ``tenant_id`` on ``session`` for every transaction it opens from now on.

    Idempotent: binding the same tenant twice costs nothing (no statement, no second
    listener). Binding a *different* tenant retargets the existing listener and re-binds
    the in-flight transaction immediately, so a sweeper can move from the platform scope
    to a job's tenant and back on one session.

    A no-op off PostgreSQL (the tests' SQLite has neither RLS nor session GUCs) and a
    no-op when :mod:`aegis.governance` is not installed.

    Args:
        session: The session doing the memory work.
        tenant_id: The tenant to scope to, or ``None`` for the platform scope — the
            positive assertion "this work spans every tenant", which is what the queue
            drain and the forget sweep genuinely are.
    """
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return
    try:
        # Local import for the reason ``sweep_pending`` states: keep the governance
        # package (pyjwt, argon2) off ``aegis.memory``'s import graph.
        from aegis.governance.rls import (  # noqa: PLC0415 - see above
            SCOPE_BINDING_SQL,
            mark_scope_bound,
            scope_binding_params,
            set_tenant_scope,
        )
    except ImportError:  # pragma: no cover - governance ships with the data extra
        logger.debug("aegis.governance is unavailable; memory scope binding is inert")
        return

    info = _session_info(session)
    if info is None:  # pragma: no cover - every Session carries an info dict
        await set_tenant_scope(session, tenant_id)
        return

    holder = info.get(_HOLDER_KEY)
    if holder is not None:
        if holder["tenant_id"] == tenant_id:
            return  # already the session's scope, and every future BEGIN re-applies it
        holder["tenant_id"] = tenant_id
        # Retarget the transaction that is open *now*: ``after_begin`` has already fired
        # for it and will not fire again.
        await set_tenant_scope(session, tenant_id)
        return

    from sqlalchemy import event, text  # noqa: PLC0415 - local, mirrors the RLS seam

    holder = {"tenant_id": tenant_id}
    info[_HOLDER_KEY] = holder

    def _on_begin(_session: Any, _transaction: Any, connection: Any) -> None:  # noqa: ANN401
        """Re-write both scope GUCs at the start of each transaction on this session."""
        if connection.dialect.name != "postgresql":
            return
        scope = holder["tenant_id"]
        connection.execute(text(SCOPE_BINDING_SQL), scope_binding_params(scope))
        mark_scope_bound(connection, scope)

    # The listener goes on the *sync* session, where SQLAlchemy's ORM events live; it
    # runs inside the greenlet driving the async session, so issuing SQL from it is
    # legitimate and never blocks the event loop.
    event.listen(getattr(session, "sync_session", session), "after_begin", _on_begin)
    # And bind the transaction that may already be open — the caller's own
    # ``set_tenant_scope`` will have opened one, and ``after_begin`` has already passed
    # for it. With none open there is nothing to catch up: the next statement begins a
    # transaction, and the listener above binds it before any SQL of ours is sent.
    if session.in_transaction():
        await set_tenant_scope(session, tenant_id)


__all__ = ["bind_memory_scope", "bound_memory_scope"]
