"""Backend shim: the plan-and-execute LangGraph now lives in ``aegis.agent.graph``.

The graph topology + every node body — guardrail → route → retrieve → ml_predict →
plan → gate → act → reflect → generate → guardrail → stream, the bounded self-repair
loop and the human-in-the-loop gate — moved into the standalone ``aegis.agent``
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


# The durable saver is a process-wide singleton: its Postgres connection stays open
# for the app's lifetime so every compiled graph shares one checkpoint store, and the
# context manager is retained so the connection is not closed by garbage collection.
_pg_checkpointer: Any = None
_pg_keepalive: list[Any] = []


def _build_postgres_checkpointer() -> Any:  # noqa: ANN401 - BaseCheckpointSaver
    """Build (once) the durable ``PostgresSaver`` bound to ``settings.postgres_dsn``.

    Lazily imports ``langgraph.checkpoint.postgres`` (so the default memory path never
    needs Postgres), opens a long-lived connection from the configured DSN, runs
    ``setup()`` to create the checkpoint tables idempotently, and caches the saver as
    a process-wide singleton. Each ``interrupt`` then checkpoints durably, so a paused
    run survives a restart or another worker and resumes by ``thread_id == run_id``.

    Returns:
        A ``PostgresSaver`` bound to ``settings.postgres_dsn``, schema initialised.

    Raises:
        RuntimeError: If ``langgraph-checkpoint-postgres`` is not installed.
    """
    global _pg_checkpointer
    if _pg_checkpointer is not None:  # pragma: no cover - prod singleton reuse
        return _pg_checkpointer

    from app.config import get_settings

    try:
        from langgraph.checkpoint.postgres import PostgresSaver
    except ImportError as exc:  # pragma: no cover - prod-only (no Postgres in tests)
        raise RuntimeError(
            "agent_checkpointer='postgres' requires the "
            "'langgraph-checkpoint-postgres' package; install it "
            "(pip install '.[agent]') or use the default 'memory' saver."
        ) from exc

    # ``from_conn_string`` is a context manager over an open connection; enter it and
    # retain the CM so the connection lives for the app's lifetime.
    cm = PostgresSaver.from_conn_string(get_settings().postgres_dsn)  # pragma: no cover
    saver = cm.__enter__()  # pragma: no cover - prod-only lifecycle
    saver.setup()  # pragma: no cover - creates checkpoint tables idempotently
    _pg_keepalive.append(cm)  # pragma: no cover
    _pg_checkpointer = saver  # pragma: no cover
    return saver  # pragma: no cover
