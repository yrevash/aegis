"""Backend shim: the supervisor router now lives in ``aegis.agent.router``.

The router *mechanism* — the deterministic-first classifier, the hand-off contract
(:class:`RouterDecision`), the bounded cheap-LLM tiebreak and the ``qa``-only core
fallback roster — moved into the standalone ``aegis.agent`` package. This module
re-exports that public surface by identity, and keeps the one host coupling
app-side: :func:`load_roster`, which reads the **domain adapter's** ``agent_roster``
contract (``app.adapter``). That adapter-backed roster is what the graph consumes
through the injected ``deps.agent_roster`` hook (wired in :meth:`AgentDeps.default`);
the core's own :func:`aegis.agent.router.load_roster` is only the defensive
``qa``-only fallback.
"""

from __future__ import annotations

import logging
from typing import Any

from aegis.agent.router import (
    RouterDecision,
    _FallbackRoster,
    classify_deterministic,
    route_query,
)

logger = logging.getLogger(__name__)

__all__ = [
    "RouterDecision",
    "classify_deterministic",
    "load_roster",
    "route_query",
]


def load_roster() -> Any:  # noqa: ANN401 - adapter AgentRoster duck-type
    """Return the domain adapter's agent roster, or a ``qa``-only fallback.

    Read defensively so a domain that has not (yet) declared an ``agent_roster``
    contract still runs: the supervisor then only ever routes to ``qa`` and the graph
    behaves exactly as it did before the router existed.
    """
    try:
        from app.adapter import agent_roster

        roster = agent_roster()
        if roster is not None and roster.roles():
            return roster
    except Exception:  # noqa: BLE001 - the roster is an optional adapter contract
        logger.warning("Agent roster unavailable; routing everything to qa", exc_info=True)
    return _FallbackRoster()
