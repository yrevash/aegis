"""Config-driven model registry: a *role → model* routing table + cost tables.

Heterogeneous model routing means code requests a model by **role**, never by a
hard-coded id, so swapping the underlying fleet is a one-file change
(env-overridable per role). This module owns the routing table and the per-role
cost-per-1k lookup the gateway falls back to when a provider's own cost map has
no entry for a custom deployment id.

Depends only on :mod:`aegis.core.models` — no litellm, no network.
"""

from __future__ import annotations

import os

from aegis.core.models import ModelRole

__all__ = ["is_small_model", "model_for", "routing_table"]

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
    """Return the effective role → model map (for a dashboard / docs)."""
    return {role.value: model_for(role) for role in ModelRole}


# Substrings that mark a deployment id as a small/cheap model, used by the
# measured small-model-share efficiency metric (single source of truth).
_SMALL_MODEL_MARKERS: tuple[str, ...] = ("mini", "3.5", "3-5", "llama-3.2", "phi-3.5")


def is_small_model(model_id: str) -> bool:
    """Return whether ``model_id`` names a small/cheap model deployment."""
    lowered = model_id.lower()
    return any(marker in lowered for marker in _SMALL_MODEL_MARKERS)


# Per-role token pricing (USD per 1k tokens, input/output) used ONLY as a
# fallback when a provider's cost map has no entry for a model — the case for
# custom/self-hosted deployment ids. Approximate but honest and non-zero;
# override any role via ``COST_<ROLE>_IN`` / ``COST_<ROLE>_OUT``.
_COST_PER_1K: dict[ModelRole, tuple[float, float]] = {
    ModelRole.CHEAP: (0.00015, 0.0006),
    ModelRole.REASONING: (0.0011, 0.0044),
    ModelRole.GENERATION: (0.0025, 0.01),
    ModelRole.EMBEDDING: (0.00013, 0.0),
    ModelRole.VISION: (0.0025, 0.01),
    ModelRole.VOICE: (0.006, 0.0),
}


def _rate_for(role: ModelRole) -> tuple[float, float]:
    """Return the (input, output) $/1k rate for ``role`` (env-overridable)."""
    default_in, default_out = _COST_PER_1K.get(role, (0.0, 0.0))
    rate_in = float(os.environ.get(f"COST_{role.name}_IN", default_in))
    rate_out = float(os.environ.get(f"COST_{role.name}_OUT", default_out))
    return rate_in, rate_out
