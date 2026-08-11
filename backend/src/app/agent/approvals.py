"""Backend shim + host-owned singletons for the approval rendezvous.

The registry *classes* — :class:`ApprovalRegistry` (the notify cache over the durable
inbox) and :class:`ParkedRunRegistry` (the in-process resumable-handle map) — are pure
asyncio and moved verbatim into ``aegis.agent.approvals``. This module re-exports them
by identity and OWNS the process-wide default singletons (``_default_registry`` /
``_default_parked``), because the host's live gate (``run_agent``) and its durable
decision glue (``decide_approval``/``resume_parked_run``) must share the *same*
registries — and an out-of-band resumer test wipes ``_default_parked`` here to simulate
a fresh worker/restart. The core loop registers into whichever registry the host injects
(``run_agent(..., parked_runs=get_parked_runs())``).
"""

from __future__ import annotations

from aegis.agent.approvals import (
    ApprovalOutcome,
    ApprovalRegistry,
    ParkedRun,
    ParkedRunRegistry,
    UnknownApprovalError,
)

__all__ = [
    "ApprovalOutcome",
    "ApprovalRegistry",
    "ParkedRun",
    "ParkedRunRegistry",
    "UnknownApprovalError",
    "get_approval_registry",
    "get_parked_runs",
]

# Process-wide default registries shared by the API layer, the live run loop and the
# durable decision glue. Owned here (not re-exported from aegis) so a fresh-worker test
# can ``monkeypatch.setattr(app.agent.approvals, "_default_parked", ...)`` and the next
# ``get_parked_runs()`` re-reads it — the mechanism behind the cross-worker resume tests.
_default_registry = ApprovalRegistry()
_default_parked = ParkedRunRegistry()


def get_approval_registry() -> ApprovalRegistry:
    """Return the process-wide default :class:`ApprovalRegistry`."""
    return _default_registry


def get_parked_runs() -> ParkedRunRegistry:
    """Return the process-wide default :class:`ParkedRunRegistry` (re-read each call)."""
    return _default_parked
