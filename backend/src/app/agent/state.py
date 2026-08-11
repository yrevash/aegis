"""Backend shim: the typed graph state now lives in ``aegis.agent.state``.

``AgentState`` is a pure ``TypedDict`` with zero couplings, so it moved verbatim
into the standalone ``aegis.agent`` package. This module re-exports it by identity
so every existing ``from app.agent.state import AgentState`` call site is unchanged.
"""

from __future__ import annotations

from aegis.agent.state import AgentState

__all__ = ["AgentState"]
