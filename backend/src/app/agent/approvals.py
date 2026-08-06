"""The human-in-the-loop approval **notify cache** over the durable inbox.

Bounded autonomy pauses a run at a gate. The durable source of truth is the
``approvals`` table (see :mod:`app.data.approvals`); this module is the fast in-
process layer over it that keeps the *live* money-shot gate dramatic:

- :class:`ApprovalRegistry` is a notify cache — the streaming ``/query`` run
  registers a future keyed by ``approval_id`` and awaits it, and a decision resolves
  that future to wake the socket instantly. If no live waiter exists (the socket
  closed / the worker parked the run), the decision still lands durably and a resumer
  picks it up from the checkpoint. Both models share one resolve path.
- :class:`ParkedRunRegistry` holds the compiled-graph handle for a run parked at the
  gate, keyed by ``run_id``, so an out-of-band resumer can continue it from the
  checkpoint in-process (the default ``InMemorySaver`` lives inside that graph). With
  the durable ``PostgresSaver`` a fresh worker resumes by ``thread_id`` instead.

Both registries are process-local and fully async-native, so the whole surface is
trivially unit-testable with no infrastructure.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from app.api.schemas import ApprovalDecision


@dataclass(frozen=True)
class ApprovalOutcome:
    """The resolved decision for a gated action.

    Attributes:
        decision: The human's verdict (approve/reject).
        approver: Identifier of the human who decided, for the audit trail.
    """

    decision: ApprovalDecision
    approver: str | None = None

    @property
    def approved(self) -> bool:
        """Return whether the action was approved."""
        return self.decision is ApprovalDecision.APPROVE


class UnknownApprovalError(KeyError):
    """Raised when resolving an ``approval_id`` that is not pending."""


class ApprovalRegistry:
    """A registry of pending human-approval gates keyed by ``approval_id``."""

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._pending: dict[str, asyncio.Future[ApprovalOutcome]] = {}

    def register(self, approval_id: str) -> asyncio.Future[ApprovalOutcome]:
        """Register a new pending gate and return its awaitable future.

        Args:
            approval_id: The unique id of the gate to register.

        Returns:
            A future that resolves to the :class:`ApprovalOutcome` once a human
            decides.
        """
        future: asyncio.Future[ApprovalOutcome] = asyncio.get_running_loop().create_future()
        self._pending[approval_id] = future
        return future

    async def wait(
        self, approval_id: str, *, timeout: float | None = None
    ) -> ApprovalOutcome:
        """Await the human decision for ``approval_id`` then forget the gate.

        Args:
            approval_id: The gate to await.
            timeout: Seconds to wait before parking. ``None`` (default) waits
                indefinitely — the live demo gate. A positive value bounds the
                socket-held wait so the run can *park* (the durable row remains the
                source of truth) instead of holding the connection forever.

        Returns:
            The resolved :class:`ApprovalOutcome`.

        Raises:
            TimeoutError: If ``timeout`` elapses before a decision arrives; the
                caller parks the run and the gate is forgotten from the cache.
        """
        future = self._pending.get(approval_id)
        if future is None:
            future = self.register(approval_id)
        try:
            if timeout is None:
                return await future
            return await asyncio.wait_for(asyncio.shield(future), timeout)
        finally:
            self._pending.pop(approval_id, None)

    def resolve(
        self, approval_id: str, decision: ApprovalDecision, *, approver: str | None = None
    ) -> bool:
        """Resolve a pending gate with a human decision.

        Args:
            approval_id: The gate to resolve.
            decision: The human's verdict.
            approver: Who made the decision (recorded in the audit trail).

        Returns:
            ``True`` if a pending gate was resolved, ``False`` if it was unknown
            or already resolved.
        """
        future = self._pending.get(approval_id)
        if future is None or future.done():
            return False
        future.set_result(ApprovalOutcome(decision=decision, approver=approver))
        return True

    def is_pending(self, approval_id: str) -> bool:
        """Return whether ``approval_id`` is currently awaiting a decision."""
        future = self._pending.get(approval_id)
        return future is not None and not future.done()

    def pending_ids(self) -> list[str]:
        """Return the ids of every gate currently awaiting a human decision."""
        return [aid for aid, fut in self._pending.items() if not fut.done()]


# Process-wide default registry shared by the API layer and the orchestrator.
_default_registry = ApprovalRegistry()


def get_approval_registry() -> ApprovalRegistry:
    """Return the process-wide default :class:`ApprovalRegistry`."""
    return _default_registry


@dataclass(frozen=True)
class ParkedRun:
    """A resumable handle for a run paused at the gate.

    Attributes:
        graph: The compiled graph whose checkpointer holds the paused state.
        config: The LangGraph config (carries ``thread_id == run_id``) to resume it.
    """

    graph: Any
    config: dict[str, Any]


class ParkedRunRegistry:
    """Process-local map of ``run_id`` → :class:`ParkedRun` for in-process resume."""

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._parked: dict[str, ParkedRun] = {}

    def register(self, run_id: str, graph: Any, config: dict[str, Any]) -> None:  # noqa: ANN401
        """Record the resumable handle for a run parked at the gate."""
        self._parked[run_id] = ParkedRun(graph=graph, config=config)

    def pop(self, run_id: str) -> ParkedRun | None:
        """Remove and return the handle for ``run_id`` (``None`` if not parked)."""
        return self._parked.pop(run_id, None)

    def get(self, run_id: str) -> ParkedRun | None:
        """Return the handle for ``run_id`` without removing it."""
        return self._parked.get(run_id)


# Process-wide parked-run registry shared by the orchestrator and the resumer.
_default_parked = ParkedRunRegistry()


def get_parked_runs() -> ParkedRunRegistry:
    """Return the process-wide default :class:`ParkedRunRegistry`."""
    return _default_parked
