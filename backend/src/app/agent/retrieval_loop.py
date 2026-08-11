"""Backend shim: the agentic/iterative retrieval loop now lives in ``aegis.retrieval.agentic``.

It depended only on retrieval Protocols/models (no gateway, no observability), so it
moved into ``aegis.retrieval`` as ``agentic.py`` rather than staying backend-only.
This module re-exports the package's public API by identity so the existing
``from .retrieval_loop import agentic_retrieve`` call site in ``app.agent.graph``
keeps working unchanged.
"""

from __future__ import annotations

from aegis.retrieval.agentic import (
    AgenticRetrievalResult,
    RetrievalRound,
    RetrieveFn,
    RewriteFn,
    Sufficiency,
    agentic_retrieve,
    assess_sufficiency,
)

__all__ = [
    "AgenticRetrievalResult",
    "RetrievalRound",
    "RetrieveFn",
    "RewriteFn",
    "Sufficiency",
    "agentic_retrieve",
    "assess_sufficiency",
]
