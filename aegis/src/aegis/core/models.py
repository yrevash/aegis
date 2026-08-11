"""A dependency-free *job → role* enum shared by any module that routes by role.

Code should request a model by **role**, never by a hard-coded id, so swapping the
underlying fleet is a one-file change elsewhere (the gateway module owns the actual
routing table). This module intentionally contains *only* the enum — no litellm, no
routing table, no env reads — so every light module (retrieval, guardrails, the
future gateway) can depend on it without pulling in anything heavy.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["ModelRole"]


class ModelRole(StrEnum):
    """A *job*, not a specific model. Route by role, not by habit."""

    CHEAP = "cheap"  # classify, extract, route, guardrail classifier
    REASONING = "reasoning"  # hard reasoning steps, LLM-as-judge
    GENERATION = "generation"  # main answer generation
    EMBEDDING = "embedding"  # vector embeddings (fixed across the app)
    VISION = "vision"  # image understanding (if used)
    VOICE = "voice"  # speech-to-text (if used)
