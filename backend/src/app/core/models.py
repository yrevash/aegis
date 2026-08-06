"""Config-driven model registry: a *role → model* routing table.

Heterogeneous model routing is a stated architectural decision (see
`docs/backend.md` §2) and part of the cost story surfaced on the dashboard.
Code throughout the app requests a model by **role**, never by hard-coded id, so
swapping the underlying fleet is a one-file change (env-overridable per role).

The ids below are the current `docs/backend.md` fleet used through the LiteLLM
custom OpenAI-compatible provider (bare deployment names, no `azure/` prefix).
The embedding model is fixed; the rest may be finalised closer to the day.
"""

from __future__ import annotations

import os
from enum import StrEnum


class ModelRole(StrEnum):
    """A *job*, not a specific model. Route by role, not by habit."""

    CHEAP = "cheap"            # classify, extract, route, guardrail classifier
    REASONING = "reasoning"    # hard reasoning steps, LLM-as-judge
    GENERATION = "generation"  # main answer generation
    EMBEDDING = "embedding"    # vector embeddings (fixed across the app)
    VISION = "vision"          # image understanding (if used)
    VOICE = "voice"            # speech-to-text (if used)


# Default role → deployment-id map. Override any entry via an env var of the form
# ``MODEL_<ROLE>`` (e.g. ``MODEL_GENERATION=genailab-maas-DeepSeek-V3-0324``).
_DEFAULT_ROUTING: dict[ModelRole, str] = {
    ModelRole.CHEAP: "genailab-maas-gpt-4o-mini",
    ModelRole.REASONING: "genailab-maas-Phi-4-reasoning",
    ModelRole.GENERATION: "genailab-maas-gpt-4o",
    ModelRole.EMBEDDING: "genailab-maas-text-embedding-3-large",
    ModelRole.VISION: "genailab-maas-Llama-3.2-90B-Vision-Instruct",
    ModelRole.VOICE: "genailab-maas-whisper",
}


def model_for(role: ModelRole) -> str:
    """Return the deployment id configured for ``role`` (env override wins)."""
    return os.environ.get(f"MODEL_{role.name}", _DEFAULT_ROUTING[role])


def routing_table() -> dict[str, str]:
    """Return the effective role → model map (for the dashboard / docs)."""
    return {role.value: model_for(role) for role in ModelRole}


# Substrings that mark a deployment id as a small/cheap model, used by the
# measured small-model-share efficiency metric (single source of truth).
_SMALL_MODEL_MARKERS: tuple[str, ...] = ("mini", "3.5", "3-5", "llama-3.2", "phi-3.5")


def is_small_model(model_id: str) -> bool:
    """Return whether ``model_id`` names a small/cheap model deployment."""
    lowered = model_id.lower()
    return any(marker in lowered for marker in _SMALL_MODEL_MARKERS)
