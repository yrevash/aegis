"""Backend shim: the governance context seam now lives in ``aegis.governance``.

The typed :class:`GovernanceContext` / :class:`GovernanceLimits` and the contextvar
``set`` / ``get`` / ``reset`` helpers moved to the standalone ``aegis.governance``
package (``aegis.governance.context`` + ``aegis.governance.types``). Re-exported here
under their historical names so every existing importer (``app.api.routes``,
``app.core.llm``, the agent graph/orchestrator) is unchanged.
"""

from __future__ import annotations

from aegis.governance.context import (
    GovernanceContext,
    GovernanceLimits,
    get_governance_context,
    governed,
    reset_governance_context,
    set_governance_context,
)

__all__ = [
    "GovernanceContext",
    "GovernanceLimits",
    "get_governance_context",
    "governed",
    "reset_governance_context",
    "set_governance_context",
]
