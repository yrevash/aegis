"""What the durable-job worker is actually doing, as a fact this process can be asked.

**Why this module exists.** The Temporal worker runs as an ``asyncio`` task inside the
API's lifespan. When Temporal was down at boot, that task raised, the supervisor callback
logged it at ERROR — and then nothing. The task was never restarted, so bringing Temporal
back did not bring the worker back; and ``GET /health`` went on returning
``{"status": "ok"}`` the whole time, because it reported that the *web process* was
alive, which was true and useless. A platform whose entire durable substrate is dead
while its health probe is green is worse than one that is honestly down: a load balancer
keeps routing to it, an operator keeps trusting it, and every uploaded document is
accepted into a queue nothing will ever drain.

The fix has two halves, and they are complementary rather than alternative:

* **Supervise** — :func:`app.main.run_worker_supervised` re-runs the worker with capped
  exponential backoff for as long as the process lives, so an orchestrator that comes
  back is picked up without a restart of the API.
* **Surface** — this module is the one place that knows which of those attempts is
  currently true, so ``GET /health`` can stop claiming the substrate is fine and
  ``GET /ready`` can answer 503 while it is not.

Neither half is sufficient. Restarting silently would leave an operator with no way to
see the outage while it lasts; reporting without restarting would name a problem that
needs a process bounce to clear. The state below is what the two halves talk through.

**Process-local, deliberately.** This is the health of *this* process's worker, not a
cluster view. A second worker process has its own answer, and merging them here would
invent a consensus nothing measured.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime

__all__ = [
    "WORKER_DISABLED",
    "WORKER_DOWN",
    "WORKER_RUNNING",
    "WORKER_STARTING",
    "WORKER_STOPPED",
    "WorkerHealth",
    "note_worker_restart",
    "reset_worker_health",
    "set_worker_state",
    "worker_health",
]

#: The worker is not meant to run in this process — no stores, or the in-process worker
#: is switched off. **Not** a failure, and reported separately from one precisely so a
#: deliberately API-only deployment does not read as broken.
WORKER_DISABLED = "disabled"

#: Configured to run, and this process is trying to reach the orchestrator.
WORKER_STARTING = "starting"

#: Connected; the workers are polling their queues.
WORKER_RUNNING = "running"

#: It was meant to be running and is not. The reason is on
#: :attr:`WorkerHealth.detail`, already translated by
#: :exc:`app.jobs.client.TemporalUnavailableError` when the cause is the connection.
WORKER_DOWN = "down"

#: Shut down cleanly on the lifespan's stop event. A terminal, non-error state.
WORKER_STOPPED = "stopped"

#: The states in which the durable substrate can actually accept work.
_HEALTHY = frozenset({WORKER_RUNNING})


@dataclass(frozen=True, slots=True)
class WorkerHealth:
    """A snapshot of the worker's state in this process.

    Attributes:
        state: One of the ``WORKER_*`` constants.
        detail: Why, when the state is :data:`WORKER_DOWN` — the translated connection
            error, not a tonic transport string. ``None`` otherwise.
        since: When this state was entered (UTC).
        restarts: How many times the supervisor has re-run the worker since boot. A
            non-zero value on a ``running`` worker is the visible trace of an outage that
            healed, which a bare state word would erase.
    """

    state: str = WORKER_DISABLED
    detail: str | None = None
    since: datetime | None = None
    restarts: int = 0

    @property
    def ready(self) -> bool:
        """Whether the durable substrate can accept work right now.

        ``disabled`` counts as ready: a deployment that never intended to run a worker in
        this process is not *failing*, and answering 503 for it would make ``/ready``
        useless in exactly the configuration the lite demo ships.
        """
        return self.state in _HEALTHY or self.state == WORKER_DISABLED


# Guarded by a plain lock rather than an asyncio one: the setter is called from the
# lifespan's task and read from request handlers on the same loop, but ``python -m
# app.jobs.worker`` and the tests reach it from other threads, and a lock that only works
# on one loop would be a lie in the module whose whole job is not to lie about state.
_lock = threading.Lock()
_health = WorkerHealth()


def set_worker_state(state: str, *, detail: str | None = None) -> None:
    """Record the worker's current state.

    Args:
        state: One of the ``WORKER_*`` constants.
        detail: The reason, for :data:`WORKER_DOWN`. Cleared for every other state, so a
            recovered worker never carries the sentence from the outage it survived.
    """
    global _health
    with _lock:
        _health = replace(
            _health,
            state=state,
            detail=detail if state == WORKER_DOWN else None,
            since=datetime.now(UTC),
        )


def note_worker_restart() -> None:
    """Count one supervisor re-run of the worker."""
    global _health
    with _lock:
        _health = replace(_health, restarts=_health.restarts + 1)


def worker_health() -> WorkerHealth:
    """Return the current snapshot (a frozen value — safe to hold and render)."""
    with _lock:
        return _health


def reset_worker_health() -> None:
    """Return to the boot state. For tests, and for a re-created app in one process."""
    global _health
    with _lock:
        _health = WorkerHealth()
