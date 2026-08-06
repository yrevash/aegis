"""Long-term memory + context engineering (SOTA; see ``docs/MEMORY_SPEC.md``).

A domain-agnostic core subsystem: persist raw turns cheaply, distil durable facts
lazily with a cheap model (mem0-style ADD/UPDATE/INVALIDATE, Zep bitemporal), recall by
a Generative-Agents blend (relevance+recency+importance), and assemble working memory
under a hard token budget with lost-in-the-middle-aware ordering. What a "fact" or
"skill" *means* is supplied by ``app.adapter.memory_spec``; the mechanism here never
knows the domain. Memory is separate from the Neo4j/LightRAG domain-knowledge graph.
"""

from __future__ import annotations

from app.memory.config import MemoryBackend, MemoryConfig
from app.memory.scoring import (
    ForgetPolicy,
    RecallCandidate,
    minmax,
    rank_top,
    recency_decay,
    score_candidates,
)

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
