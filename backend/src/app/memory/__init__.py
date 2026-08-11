"""Backend shim: long-term memory now lives in the standalone :mod:`aegis.memory` package.

The full three-tier subsystem (episodic / semantic-bitemporal / procedural + durable
consolidation queue, Generative-Agents recall blend, lost-in-the-middle assembly) has
moved to the LLM- and domain-agnostic ``aegis.memory`` package (see ``/aegis``). This
package is the **strangler shim**: each submodule re-exports the package's public API, and
this ``__init__`` wires the platform's domain contract — ``app.adapter.memory_spec`` — as
the process-wide default :class:`~aegis.memory.spec.MemorySpec`, so every call site keeps
passing no ``spec`` and the completer/embedder stay injected by ``app.agent.deps.MemoryDeps``.

The store models register on :class:`aegis.data.AegisBase`; :func:`app.data.session.bootstrap`
materialises that metadata alongside the platform's own tables.
"""

from __future__ import annotations

from aegis.memory import set_default_spec
from aegis.memory.config import MemoryBackend, MemoryConfig
from aegis.memory.scoring import (
    ForgetPolicy,
    RecallCandidate,
    minmax,
    rank_top,
    recency_decay,
    score_candidates,
)

from app.adapter import memory_spec as _memory_spec

# Wire the platform's domain seam once, so aegis.memory's recall/consolidate resolve it as
# the default (call sites never thread a spec). Rewritten on the hackathon day with the
# domain, exactly like the rest of the adapter — nothing in aegis.memory changes.
set_default_spec(_memory_spec)

__all__ = [
    "ForgetPolicy",
    "MemoryBackend",
    "MemoryConfig",
    "RecallCandidate",
    "minmax",
    "rank_top",
    "recency_decay",
    "score_candidates",
]
