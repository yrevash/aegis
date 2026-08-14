"""The human-in-the-loop approval **notify cache** over the durable inbox.

Bounded autonomy pauses a run at a gate. The durable source of truth is the host's
approvals inbox (persisted through the injected durable-store seam); this module is
the fast in-process layer over it that keeps the *live* money-shot gate dramatic:

- :class:`ApprovalRegistry` is a notify cache — the streaming ``/query`` run
  registers a future keyed by ``approval_id`` and awaits it, and a decision resolves
  that future to wake the socket instantly. If no live waiter exists (the socket
  closed / the worker parked the run), the decision still lands durably and a resumer
  picks it up from the checkpoint. Both models share one resolve path.
- :class:`ParkedRunRegistry` holds the compiled-graph handle for a run parked at the
  gate, keyed by ``run_id``, so an out-of-band resumer can continue it from the
  checkpoint in-process (the default ``InMemorySaver`` lives inside that graph). With
  the durable ``PostgresSaver`` a fresh worker resumes by ``thread_id`` instead.

**Exactly-once hand-off.** A registered future proves a gate *exists*, not that a live
run will *consume* it: the streaming generator registers before it emits (so a fast
decision cannot race past the wait) and only reaches :meth:`ApprovalRegistry.wait`
several ``yield``s later. If the SSE client disconnects in that window the generator is
closed and nothing ever takes the outcome. :meth:`ApprovalRegistry.notify_live` therefore
hands the outcome over **with an acknowledgement**: it reports ``True`` only once a
waiter actually took it, and otherwise *disowns* the gate so the durable resumer — not a
dead socket — executes the action. A waiter that wakes to find its gate disowned raises
:class:`GateHandedOffError` and parks instead of executing.

**Bounded memory.** Both registries evict on a TTL, because the paths that would
otherwise remove an entry are not guaranteed to run: an approval can expire or be
auto-rejected by the SLA sweeper (which only touches durable rows), and a live socket
can vanish mid-gate. Each :class:`ParkedRun` pins a compiled LangGraph plus its
checkpointer — i.e. the whole run state — so an unbounded map is a real leak.

Both registries are process-local and fully async-native, so the whole surface is
trivially unit-testable with no infrastructure.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from aegis.core.types import ApprovalDecision

logger = logging.getLogger(__name__)

#: How long a registered-but-unresolved gate may linger before it is evicted.
DEFAULT_GATE_TTL_SECONDS = 3600.0

#: How long a parked-run handle (a compiled graph + its checkpointer) may linger.
DEFAULT_PARKED_TTL_SECONDS = 3600.0

#: How long :meth:`ApprovalRegistry.notify_live` waits for a live waiter to *take* the
#: outcome before disowning the gate to the durable resumer. Short: a live streaming
#: run reaches its ``wait`` within a couple of event-loop turns.
DEFAULT_LIVE_ACK_TIMEOUT = 1.0


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


class GateHandedOffError(TimeoutError):
    """Raised by :meth:`ApprovalRegistry.wait` when its gate was disowned.

    The decision was handed to the durable resumer because this waiter did not take it
    in time (a closed socket, or a consumer that stopped pumping the stream). Subclasses
    :class:`TimeoutError` so the orchestrator's existing park path handles it: the run
    parks, and the resumer — the side that now owns the outcome — executes the action.
    Exactly one of the two sides ever proceeds.
    """


@dataclass
class _Gate:
    """The in-process rendezvous for one ``approval_id``."""

    future: asyncio.Future[ApprovalOutcome]
    consumed: asyncio.Event = field(default_factory=asyncio.Event)
    registered_at: float = 0.0
    waiting: int = 0
    abandoned: bool = False


class ApprovalRegistry:
    """A registry of pending human-approval gates keyed by ``approval_id``."""

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_GATE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialise an empty registry.

        Args:
            ttl_seconds: How long a registered gate with no active waiter may linger
                before it is evicted (bounded memory — see the module docstring).
            clock: Monotonic time source, injectable so TTL eviction is testable.
        """
        self._gates: dict[str, _Gate] = {}
        self._ttl_seconds = ttl_seconds
        self._clock = clock

    # ── registration / waiting ────────────────────────────────────────────────

    def register(self, approval_id: str) -> asyncio.Future[ApprovalOutcome]:
        """Register a new pending gate and return its awaitable future.

        Args:
            approval_id: The unique id of the gate to register.

        Returns:
            A future that resolves to the :class:`ApprovalOutcome` once a human
            decides.
        """
        self.sweep()
        future: asyncio.Future[ApprovalOutcome] = asyncio.get_running_loop().create_future()
        self._gates[approval_id] = _Gate(future=future, registered_at=self._clock())
        return future

    async def wait(
        self, approval_id: str, *, timeout: float | None = None
    ) -> ApprovalOutcome:
        """Await the human decision for ``approval_id`` then forget the gate.

        Taking the outcome is an explicit hand-off: on return the gate is marked
        *consumed*, which is what lets :meth:`notify_live` report a genuinely live
        wake-up rather than merely "a future existed".

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
            GateHandedOffError: If the decision was disowned to the durable resumer
                while this waiter was suspended. The caller must park, NOT execute —
                the resumer owns the outcome.
        """
        gate = self._gates.get(approval_id)
        if gate is None:
            self.register(approval_id)
            gate = self._gates[approval_id]
        gate.waiting += 1
        try:
            if timeout is None:
                outcome = await gate.future
            else:
                outcome = await asyncio.wait_for(asyncio.shield(gate.future), timeout)
        finally:
            gate.waiting -= 1
            if self._gates.get(approval_id) is gate:
                del self._gates[approval_id]
        # No ``await`` between the wake-up and the acknowledgement below: the check and
        # the hand-off are one critical section on the single-threaded event loop, so a
        # racing ``notify_live`` timeout either sees ``consumed`` or wins ``abandoned`` —
        # never both.
        if gate.abandoned:
            raise GateHandedOffError(
                f"approval {approval_id} was handed to the durable resumer"
            )
        gate.consumed.set()
        return outcome

    # ── resolution ────────────────────────────────────────────────────────────

    def resolve(
        self, approval_id: str, decision: ApprovalDecision, *, approver: str | None = None
    ) -> bool:
        """Resolve a pending gate with a human decision.

        This is the *fire-and-forget* form used by an in-process caller that also drives
        the stream (tests, the offline harness). The durable decision path must use
        :meth:`notify_live` instead, because only that form proves the outcome was taken
        by a live run before the row is finalised as approved.

        Args:
            approval_id: The gate to resolve.
            decision: The human's verdict.
            approver: Who made the decision (recorded in the audit trail).

        Returns:
            ``True`` if a pending gate was resolved, ``False`` if it was unknown
            or already resolved.
        """
        gate = self._gates.get(approval_id)
        if gate is None or gate.future.done():
            return False
        gate.future.set_result(ApprovalOutcome(decision=decision, approver=approver))
        return True

    async def notify_live(
        self,
        approval_id: str,
        decision: ApprovalDecision,
        *,
        approver: str | None = None,
        ack_timeout: float = DEFAULT_LIVE_ACK_TIMEOUT,
    ) -> bool:
        """Hand the decision to a live waiter and report whether one **took** it.

        The acknowledged form of :meth:`resolve`. A registered future is only evidence
        that a gate exists; the streaming run registers it several ``yield``s before it
        awaits, so a client that disconnects in that window leaves an orphan nobody will
        ever consume. Reporting such an orphan as "the live run woke up" is what lets a
        gate be audited APPROVED while the tool never runs.

        So: set the result, then wait (briefly) for the waiter to acknowledge that it
        took the outcome. If no acknowledgement arrives the gate is **disowned** — a
        waiter that shows up later raises :class:`GateHandedOffError` and parks — and we
        report ``False`` so the caller resumes the run from its durable checkpoint
        instead. Exactly one side ever executes the action.

        Args:
            approval_id: The gate to resolve.
            decision: The human's verdict.
            approver: Who made the decision (recorded in the audit trail).
            ack_timeout: Seconds to wait for a waiter to take the outcome.

        Returns:
            ``True`` only if a live waiter consumed the decision; ``False`` when the
            gate was unknown, already resolved, or disowned to the durable resumer.
        """
        gate = self._gates.get(approval_id)
        if gate is None or gate.future.done():
            return False
        gate.future.set_result(ApprovalOutcome(decision=decision, approver=approver))
        try:
            await asyncio.wait_for(gate.consumed.wait(), ack_timeout)
        except TimeoutError:
            # A waiter may have consumed it in the same loop turn the timeout fired;
            # ``consumed`` is authoritative, so check it before disowning.
            if gate.consumed.is_set():
                return True
            logger.info(
                "No live waiter took approval %s; handing it to the durable resumer",
                approval_id,
            )
            gate.abandoned = True
            self._forget(approval_id, gate)
            return False
        return True

    def discard(self, approval_id: str) -> bool:
        """Forget a gate nothing will consume (the live run went away).

        Called by the streaming run's cleanup so a closed SSE connection cannot leave an
        orphan future behind — the leak that both makes :meth:`notify_live` lie and grows
        ``_gates`` without bound.

        Returns:
            ``True`` if a gate was actually discarded.
        """
        gate = self._gates.pop(approval_id, None)
        if gate is None:
            return False
        gate.abandoned = True
        return True

    # ── introspection / housekeeping ──────────────────────────────────────────

    def is_pending(self, approval_id: str) -> bool:
        """Return whether ``approval_id`` is currently awaiting a decision."""
        self.sweep()
        gate = self._gates.get(approval_id)
        return gate is not None and not gate.future.done()

    def pending_ids(self) -> list[str]:
        """Return the ids of every gate currently awaiting a human decision.

        Deliberately the only size-ish accessor: defining ``__len__`` would make an
        empty registry falsy, and ``registry or get_approval_registry()`` at every call
        site would then silently swap an injected registry for the global singleton.
        """
        self.sweep()
        return [aid for aid, gate in self._gates.items() if not gate.future.done()]

    def sweep(self, *, now: float | None = None) -> list[str]:
        """Evict gates older than the TTL that have no active waiter.

        Returns:
            The evicted ``approval_id``s (for logging/tests).
        """
        if self._ttl_seconds <= 0:
            return []
        cutoff = (now if now is not None else self._clock()) - self._ttl_seconds
        stale = [
            aid
            for aid, gate in self._gates.items()
            if gate.waiting == 0 and gate.registered_at <= cutoff
        ]
        for aid in stale:
            gate = self._gates.pop(aid, None)
            if gate is not None:
                gate.abandoned = True
        if stale:
            logger.info("Evicted %d expired approval gate(s) from the notify cache", len(stale))
        return stale

    def _forget(self, approval_id: str, gate: _Gate) -> None:
        """Drop ``approval_id`` only if it still maps to ``gate`` (no ABA races)."""
        if self._gates.get(approval_id) is gate:
            del self._gates[approval_id]


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
        parked_at: Monotonic timestamp used for TTL eviction.
    """

    graph: Any
    config: dict[str, Any]
    parked_at: float = 0.0


class ParkedRunRegistry:
    """Process-local map of ``run_id`` → :class:`ParkedRun` for in-process resume.

    Entries are TTL-bounded. Several paths never pop: an approval that the SLA sweeper
    expires or auto-rejects only touches durable rows, and a run that fails after parking
    may never reach a pop site. Since each entry pins a compiled graph plus its
    checkpointer (the entire run state), an unbounded map leaks whole runs. Eviction is
    safe: the durable checkpoint remains, so a decision that arrives after eviction
    rehydrates by ``thread_id`` exactly as a fresh worker does.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_PARKED_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialise an empty registry.

        Args:
            ttl_seconds: How long a parked handle may linger before eviction.
            clock: Monotonic time source, injectable so TTL eviction is testable.
        """
        self._parked: dict[str, ParkedRun] = {}
        self._ttl_seconds = ttl_seconds
        self._clock = clock

    def register(self, run_id: str, graph: Any, config: dict[str, Any]) -> None:  # noqa: ANN401
        """Record the resumable handle for a run parked at the gate."""
        self.sweep()
        self._parked[run_id] = ParkedRun(
            graph=graph, config=config, parked_at=self._clock()
        )

    def pop(self, run_id: str) -> ParkedRun | None:
        """Remove and return the handle for ``run_id`` (``None`` if not parked)."""
        self.sweep()
        return self._parked.pop(run_id, None)

    def get(self, run_id: str) -> ParkedRun | None:
        """Return the handle for ``run_id`` without removing it."""
        self.sweep()
        return self._parked.get(run_id)

    def sweep(self, *, now: float | None = None) -> list[str]:
        """Evict handles older than the TTL.

        Returns:
            The evicted ``run_id``s (for logging/tests).
        """
        if self._ttl_seconds <= 0:
            return []
        cutoff = (now if now is not None else self._clock()) - self._ttl_seconds
        stale = [
            rid for rid, parked in self._parked.items() if parked.parked_at <= cutoff
        ]
        for rid in stale:
            self._parked.pop(rid, None)
        if stale:
            logger.info(
                "Evicted %d expired parked-run handle(s); a late decision now "
                "rehydrates from the durable checkpoint",
                len(stale),
            )
        return stale

    def ids(self) -> list[str]:
        """Return the ``run_id``s currently holding a resumable handle.

        Deliberately NOT ``__len__``: a registry is passed around as
        ``registry or get_default()``, and a ``__len__`` would make an empty one falsy —
        silently swapping an injected registry for the process-wide singleton.
        """
        self.sweep()
        return list(self._parked)


# Process-wide parked-run registry shared by the orchestrator and the resumer.
_default_parked = ParkedRunRegistry()


def get_parked_runs() -> ParkedRunRegistry:
    """Return the process-wide default :class:`ParkedRunRegistry`."""
    return _default_parked
