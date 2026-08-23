"""The run's LangGraph checkpoint history — the durable-execution evidence surface.

``GET /agent/checkpoints/{run_id}`` reads ``graph.get_state_history(config)`` for one
run's thread and returns the checkpoint chain: the ordered snapshots LangGraph wrote as
the graph advanced, which node produced each one, which node was pending at it, and
which one an ``interrupt`` parked on.

Why this endpoint is worth having: the human gate is a real
:func:`langgraph.types.interrupt`, so the run *pauses on a checkpointer* and is resumed
out of band. With ``AGENT_CHECKPOINTER=postgres`` the pause survives a restart. All of
that was previously only assertable — the console could show the graph and the trace,
but nothing showed the checkpoints, so "the resume continued from the gate rather than
re-running the graph" had to be taken on trust. Here it is a chain with one entry
(``source: "input"``) checkpoint, one continuous ``step`` sequence, and a post-decision
checkpoint whose parent is the interrupted one.

**What this deliberately does NOT return, and why.** A checkpoint's payload is the
agent's whole state: ``query``, the retrieved passages, the plan, the tool arguments,
the draft answer, memory recalled about a named subject. That is the run's most
sensitive content and it has surfaces of its own (``/query``'s stream, the trace, the
approvals inbox) with their own redaction. So the projection here carries **ids,
structure and timing only**:

- ``checkpoint.channel_values`` / ``StateSnapshot.values`` — dropped entirely.
- ``Interrupt.value`` — dropped. It carries the proposed action and its arguments;
  ``interrupted`` says an interrupt is parked here, which is all a timeline needs.
- ``StateSnapshot.tasks[].error`` and ``.result`` — dropped for the same reason.
- ``metadata`` — only ``source`` and ``step`` are read; anything a future LangGraph
  puts there (including ``writes``) is not passed through.

Tenant scope is enforced in the app layer, on the ``runs`` header, **before** the
checkpoint store is touched: this deployment's RLS posture is fail-**open** for an
unbound scope, so the ``WHERE tenant_id`` here is load-bearing rather than a
belt-and-braces echo of a policy. The checkpoint tables are LangGraph's own and carry no
``tenant_id`` column at all, so there is no policy on them to fall back on — which is
exactly why an unknown or other-tenant ``run_id`` answers 404 before any read of them.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aegis.retrieval.types import AllTenants
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.routes import AuthContext, _require_scope, require_auth

logger = logging.getLogger(__name__)

checkpoints_router = APIRouter()

#: How many checkpoints one response may carry. A fan-out run writes one per super-step
#: and a timeline nobody scrolls is not more honest than a bounded one; the response
#: says when it truncated rather than silently dropping the tail.
_MAX_CHECKPOINTS = 200


class CheckpointRow(BaseModel):
    """One checkpoint — structure and timing, never the state it snapshotted."""

    model_config = ConfigDict(extra="forbid")

    checkpoint_id: str = Field(description="LangGraph's checkpoint id (a UUIDv6).")
    parent_checkpoint_id: str | None = Field(
        default=None,
        description=(
            "The checkpoint this one continued from. A single unbroken parent chain is "
            "what says the run advanced rather than restarted."
        ),
    )
    step: int = Field(
        description=(
            "LangGraph's super-step counter. -1 is the input checkpoint, 0 the first "
            "loop step. Monotonic across a resume — that is the point."
        )
    )
    source: str = Field(
        description="LangGraph's checkpoint source: input, loop, update or fork."
    )
    created_at: str | None = Field(
        default=None, description="ISO-8601 timestamp the checkpoint was written."
    )
    produced_by: list[str] = Field(
        default_factory=list,
        description=(
            "The node(s) that ran to produce this checkpoint — the parent checkpoint's "
            "pending tasks. Empty for the entry checkpoint, which no node produced."
        ),
    )
    next_nodes: list[str] = Field(
        default_factory=list,
        description=(
            "The node(s) pending at this checkpoint. Empty means the graph had finished."
        ),
    )
    interrupted: bool = Field(
        default=False,
        description=(
            "Whether an interrupt is parked at this checkpoint — the human approval "
            "gate. The interrupt's payload is deliberately not returned."
        ),
    )


class CheckpointHistoryResponse(BaseModel):
    """One run's checkpoint chain, oldest first, plus what it proves."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(description="The run, which is also the checkpoint thread id.")
    store: str = Field(
        description=(
            "The configured checkpoint store: 'postgres' (durable — survives a restart) "
            "or 'memory' (this process only)."
        )
    )
    durable: bool = Field(
        description="Whether the store outlives the process that wrote the checkpoints."
    )
    checkpoints: list[CheckpointRow] = Field(
        default_factory=list, description="Oldest first, so the timeline reads forward."
    )
    entries: int = Field(
        default=0,
        description=(
            "How many times the graph was entered from the top (checkpoints with "
            "source 'input'). 1 after a resume is the evidence that the resume "
            "continued the run rather than re-running it."
        ),
    )
    interrupted_at: str | None = Field(
        default=None,
        description="The checkpoint id where the approval gate parked, if any.",
    )
    resumed_from: str | None = Field(
        default=None,
        description=(
            "The interrupted checkpoint id, when a later checkpoint names it as parent "
            "— i.e. the run was resumed and continued from exactly there. Null while "
            "the run is still parked."
        ),
    )
    truncated: bool = Field(
        default=False,
        description=f"Whether the chain was longer than {_MAX_CHECKPOINTS} checkpoints.",
    )


def _store_kind() -> tuple[str, bool]:
    """Return the configured checkpoint store's name and whether it is durable."""
    from app.config import get_settings

    kind = get_settings().agent_checkpointer.strip().lower()
    durable = kind in {"postgres", "postgresql", "pg"}
    return ("postgres" if durable else kind or "memory"), durable


async def _owns_run(run_id: str, auth: AuthContext) -> bool:
    """Return whether ``auth``'s tenant scope may read ``run_id``'s checkpoints.

    Reads the ``runs`` header — the run's tenant of record — through the serving
    engine, with an explicit tenant predicate. Platform staff
    (:data:`~aegis.retrieval.types.ALL_TENANTS`) read any run; everybody else reads
    only their own tenant's, and an id belonging to another tenant is indistinguishable
    from one that does not exist.
    """
    from aegis.runs.models import Run
    from sqlalchemy import select

    from app.data.session import get_sessionmaker

    scope = _require_scope(auth)
    stmt = select(Run.run_id).where(Run.run_id == run_id)
    if not isinstance(scope, AllTenants):
        stmt = stmt.where(Run.tenant_id == scope)
    async with get_sessionmaker()() as session:
        return (await session.execute(stmt)).first() is not None


#: The compiled graph this endpoint reads history through, built once.
#:
#: Compiling is what supplies the topology LangGraph needs in order to say which node
#: is *pending* at a checkpoint, and it is the expensive half of the request (it also
#: constructs ``AgentDeps.default()``). Caching is safe precisely because nothing here
#: runs: no node body is invoked, no model is called, and the checkpoint store it binds
#: is already the process-wide singleton. Deps decide how a node *behaves*, never the
#: shape of the graph, so one compiled graph answers every tenant's history read.
#: Rebuilt whenever the process-wide checkpointer is replaced — which
#: ``app.data.session.reset_agent_checkpointer`` does between tests. A graph cached past
#: that would answer from the store the *previous* test wrote to.
_reader_graph: tuple[Any, Any] | None = None


def _history_graph() -> Any:  # noqa: ANN401 - CompiledStateGraph
    """Return the read-only graph used to walk checkpoint history, built once."""
    global _reader_graph
    from app.data.session import get_agent_checkpointer

    store = get_agent_checkpointer()
    if _reader_graph is None or _reader_graph[0] is not store:
        from app.agent.deps import AgentDeps
        from app.agent.orchestrator import _durable_graph

        _reader_graph = (store, _durable_graph(AgentDeps.default()))
    return _reader_graph[1]


def _read_history(run_id: str) -> list[dict[str, Any]]:
    """Read ``run_id``'s checkpoint chain off the shared store, newest first.

    Blocking: the store is Postgres and every snapshot is a round trip, so the caller
    runs this in a worker thread.
    """
    graph = _history_graph()
    config = {"configurable": {"thread_id": run_id}}
    rows: list[dict[str, Any]] = []
    for snapshot in graph.get_state_history(config):
        configurable = (snapshot.config or {}).get("configurable", {})
        parent = (snapshot.parent_config or {}).get("configurable", {})
        metadata = snapshot.metadata or {}
        rows.append(
            {
                "checkpoint_id": str(configurable.get("checkpoint_id", "")),
                "parent_checkpoint_id": parent.get("checkpoint_id") or None,
                "step": int(metadata.get("step", 0)),
                "source": str(metadata.get("source", "")),
                "created_at": snapshot.created_at,
                "next_nodes": [str(node) for node in (snapshot.next or ())],
                "interrupted": bool(snapshot.interrupts),
            }
        )
        if len(rows) > _MAX_CHECKPOINTS:
            break
    return rows


@checkpoints_router.get(
    "/agent/checkpoints/{run_id}",
    response_model=CheckpointHistoryResponse,
    tags=["agent"],
)
async def agent_checkpoints_route(
    run_id: str,
    auth: AuthContext = Depends(require_auth),
) -> CheckpointHistoryResponse:
    """Return one run's LangGraph checkpoint chain, oldest first.

    Structure only — see this module's docstring for the list of fields deliberately
    withheld, of which the important one is the checkpoint's state payload (the query,
    the retrieved passages, the tool arguments).

    A run id the caller's tenant does not own answers **404**, not 403: the two are the
    same answer on purpose, so an id cannot be probed for existence.
    """
    if not await _owns_run(run_id, auth):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such run in this scope.",
        )

    store, durable = _store_kind()
    try:
        newest_first = await asyncio.to_thread(_read_history, run_id)
    except Exception as exc:  # noqa: BLE001 - an unreadable store is a 503, not a lie
        logger.exception("Checkpoint history unreadable for run %s", run_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"The checkpoint store could not be read: {exc}",
        ) from exc

    truncated = len(newest_first) > _MAX_CHECKPOINTS
    newest_first = newest_first[:_MAX_CHECKPOINTS]
    # ``produced_by`` is the parent's pending tasks: the nodes that had to run for this
    # checkpoint to exist. LangGraph 1.2 keeps no ``writes`` in checkpoint metadata, so
    # this is derived from the chain rather than read off a field that is not there.
    pending_at = {row["checkpoint_id"]: row["next_nodes"] for row in newest_first}
    rows = [
        CheckpointRow(
            checkpoint_id=row["checkpoint_id"],
            parent_checkpoint_id=row["parent_checkpoint_id"],
            step=row["step"],
            source=row["source"],
            created_at=row["created_at"],
            produced_by=pending_at.get(row["parent_checkpoint_id"] or "", []),
            next_nodes=row["next_nodes"],
            interrupted=row["interrupted"],
        )
        for row in reversed(newest_first)
    ]

    interrupted_at = next((r.checkpoint_id for r in rows if r.interrupted), None)
    resumed_from = (
        interrupted_at
        if interrupted_at is not None
        and any(r.parent_checkpoint_id == interrupted_at for r in rows)
        else None
    )
    return CheckpointHistoryResponse(
        run_id=run_id,
        store=store,
        durable=durable,
        checkpoints=rows,
        entries=sum(1 for r in rows if r.source == "input"),
        interrupted_at=interrupted_at,
        resumed_from=resumed_from,
        truncated=truncated,
    )


def mount(target: APIRouter) -> None:
    """Attach this module's route to ``target`` as a real ``APIRoute``.

    Idempotent, like the other sub-router mounts: a route already on ``target`` at the
    same path and methods is skipped, so mounting twice cannot put a second shadowed
    copy of the handler in the served table.

    Args:
        target: The application's main router, extended in place.
    """
    present = {
        (route.path, frozenset(getattr(route, "methods", ()) or ()))
        for route in target.routes
    }
    target.routes.extend(
        route
        for route in checkpoints_router.routes
        if (route.path, frozenset(getattr(route, "methods", ()) or ())) not in present
    )
