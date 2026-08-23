"""Backend shim: the plan-and-execute LangGraph now lives in ``aegis.agent.graph``.

The graph topology + every node body — guardrail → route → retrieve → plan → gate →
act → reflect → generate → guardrail → stream, the bounded self-repair loop and the
human-in-the-loop gate — moved into the standalone ``aegis.agent``
package as a pure graph-over-injected-deps. This module is the strangler shim:

- :func:`build_agent` re-binds the core builder to this host's **shared** agent
  checkpointer by default (so a run parked on one compiled graph resumes by
  ``thread_id`` from any other — the cross-worker resume seam), while still allowing
  an explicit checkpointer to be injected.
- :func:`_build_postgres_checkpointer` — the durable ``PostgresSaver`` construction —
  stays here (it needs ``app.config`` + ``langgraph-checkpoint-postgres``); it is the
  checkpointer seam ``app.data.session.get_agent_checkpointer`` builds when
  ``agent_checkpointer='postgres'``.
"""

from __future__ import annotations

from typing import Any

from aegis.agent.deps import AgentDeps
from aegis.agent.graph import build_agent as _build_agent
from langgraph.graph.state import CompiledStateGraph

__all__ = ["build_agent"]


def build_agent(
    deps: AgentDeps, *, checkpointer: Any = None  # noqa: ANN401 - BaseCheckpointSaver
) -> CompiledStateGraph:
    """Compile the agent graph bound to this host's shared checkpoint store.

    Args:
        deps: The injected capabilities the nodes call.
        checkpointer: An explicit checkpoint store; when omitted the process-wide
            shared store from :func:`app.data.session.get_agent_checkpointer` is used,
            so every compiled graph in the process (and, with the ``PostgresSaver``,
            every worker) checkpoints into ONE store.

    Returns:
        A compiled graph bound to the resolved checkpointer.
    """
    if checkpointer is None:
        from app.data.session import get_agent_checkpointer

        checkpointer = get_agent_checkpointer()
    return _build_agent(deps, checkpointer=checkpointer)


def _build_postgres_checkpointer() -> Any:  # noqa: ANN401 - BaseCheckpointSaver
    """Build (once) the durable checkpoint store bound to ``settings.postgres_dsn``.

    Lazily imports :mod:`app.agent.checkpointer` (which imports
    ``langgraph.checkpoint.postgres``), so the default ``memory`` path still needs
    neither Postgres nor the package. The store is a
    :class:`~app.agent.checkpointer.HybridPostgresSaver` — the stock ``PostgresSaver``
    with its missing async half delegated to a worker thread — because this app drives
    the graph with ``astream`` *and* reads it with the sync ``get_state``, and neither
    shipped saver serves both (see that module's docstring). Each ``interrupt`` then
    checkpoints durably, so a paused run survives a restart or another worker and
    resumes by ``thread_id == run_id``.

    Returns:
        The process-wide durable saver, its checkpoint tables ensured.

    Raises:
        RuntimeError: If ``langgraph-checkpoint-postgres`` is not installed.
    """
    from app.config import get_settings

    try:
        from app.agent.checkpointer import build_postgres_checkpointer
    except ImportError as exc:  # pragma: no cover - prod-only (no Postgres in tests)
        raise RuntimeError(
            "agent_checkpointer='postgres' requires the "
            "'langgraph-checkpoint-postgres' package; install it "
            "(pip install '.[agent]') or use the default 'memory' saver."
        ) from exc

    from app.data.session import serving_role_name

    settings = get_settings()
    return build_postgres_checkpointer(
        settings.postgres_dsn,
        # DDL on the owner connection, DML as the serving role — the same split
        # ``app.data.session.bootstrap`` uses for the app's own tables.
        admin_dsn=settings.admin_dsn,
        serving_role=serving_role_name(),
    )
