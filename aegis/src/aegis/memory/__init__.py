"""Long-term memory + context engineering (SOTA; see ``docs/architecture/memory-spec.md``).

A domain-agnostic, importable core subsystem: persist raw turns cheaply, distil durable
facts lazily with a cheap model (mem0-style ADD/UPDATE/INVALIDATE, Zep bitemporal), recall
by a Generative-Agents blend (relevance+recency+importance+frequency), and assemble working
memory under a hard token budget with lost-in-the-middle-aware ordering.

LLM-agnostic (inject a completer + embedder) and domain-agnostic: what a "fact", "profile"
or "skill" *means* is supplied by an injected :class:`~aegis.memory.spec.MemorySpec` (pass
``spec=`` or configure a process-wide default via :func:`set_default_spec`). The store
models register on :class:`aegis.data.AegisBase`; the host owns the engine and drives
``create_all`` (+ any RLS). Importing this package pulls ``sqlalchemy`` (under
``aegis[data]``) but none of the heavy retrieval/gateway extras.
"""

from __future__ import annotations

from aegis.memory.cache import (
    BACKEND_MEMORY,
    BACKEND_REDIS,
    MemoryCacheHit,
    MemorySemanticCache,
)
from aegis.memory.config import MemoryBackend, MemoryConfig
from aegis.memory.consolidate import (
    ConsolidationResult,
    consolidate,
    enqueue_consolidation,
    prune_forgotten,
    sweep_pending,
)
from aegis.memory.crud import add_fact, correct_fact, forget_fact, get_fact, list_facts
from aegis.memory.recall import RecallBundle, load_raw_window, recall
from aegis.memory.retention import (
    RetentionPolicy,
    RetentionSweep,
    apply_retention,
    retention_preview,
)
from aegis.memory.scoring import (
    ForgetPolicy,
    RecallCandidate,
    minmax,
    rank_top,
    recency_decay,
    score_candidates,
)
from aegis.memory.spec import MemorySpec, get_default_spec, resolve_spec, set_default_spec
from aegis.memory.stores import (
    ConsolidationStatus,
    MemoryConsolidationJob,
    MemoryFact,
    MemoryMessage,
    MemoryOrigin,
    MemoryProfile,
    MemorySession,
    MemoryWriteLog,
    WriteOp,
)
from aegis.memory.stream import stream_add, stream_assemble, stream_forget
from aegis.memory.vector_ops import (
    MemoryVectorIndex,
    get_default_index,
    reset_default_index,
    set_default_index,
)
from aegis.memory.working import (
    AssembledMemory,
    assemble_working_memory,
    build_working_text,
)

__all__ = [
    "BACKEND_MEMORY",
    "BACKEND_REDIS",
    "AssembledMemory",
    "ConsolidationResult",
    "ConsolidationStatus",
    "ForgetPolicy",
    "MemoryBackend",
    "MemoryCacheHit",
    "MemoryConfig",
    "MemoryConsolidationJob",
    "MemoryFact",
    "MemoryMessage",
    "MemoryOrigin",
    "MemoryProfile",
    "MemorySemanticCache",
    "MemorySession",
    "MemorySpec",
    "MemoryVectorIndex",
    "MemoryWriteLog",
    "RecallBundle",
    "RecallCandidate",
    "RetentionPolicy",
    "RetentionSweep",
    "WriteOp",
    "add_fact",
    "apply_retention",
    "assemble_working_memory",
    "build_working_text",
    "consolidate",
    "correct_fact",
    "enqueue_consolidation",
    "forget_fact",
    "get_default_index",
    "get_default_spec",
    "get_fact",
    "list_facts",
    "load_raw_window",
    "minmax",
    "prune_forgotten",
    "rank_top",
    "recall",
    "recency_decay",
    "reset_default_index",
    "resolve_spec",
    "retention_preview",
    "score_candidates",
    "set_default_index",
    "set_default_spec",
    "stream_add",
    "stream_assemble",
    "stream_forget",
    "sweep_pending",
]
