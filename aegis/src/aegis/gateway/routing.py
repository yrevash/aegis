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
import re
from enum import StrEnum

from aegis.core.models import ModelRole

__all__ = [
    "BillingUnit",
    "baseline_role",
    "billable_input_units",
    "billing_unit",
    "is_routable_role",
    "is_small_model",
    "model_for",
    "routing_table",
    "unit_cost",
]

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


#: The role whose model prices the *frontier baseline* the savings calc compares
#: actual spend against — i.e. "what every call would have cost at the frontier".
#: Defaults to ``GENERATION`` (the main answer-generation model); override with
#: ``GATEWAY_BASELINE_ROLE`` (e.g. ``REASONING``) to reprice savings against a
#: different tier without touching code.
_DEFAULT_BASELINE_ROLE: ModelRole = ModelRole.GENERATION


def baseline_role() -> ModelRole:
    """Return the frontier-baseline role for the savings calc (env-overridable).

    Read from ``GATEWAY_BASELINE_ROLE`` (case-insensitive role *name*, e.g.
    ``GENERATION`` / ``REASONING``); an unset or unrecognised value falls back to
    the default (``GENERATION``) rather than raising, so a typo can never break a
    live call — it just leaves the baseline at its safe default.
    """
    raw = os.environ.get("GATEWAY_BASELINE_ROLE", "").strip().upper()
    if not raw:
        return _DEFAULT_BASELINE_ROLE
    try:
        return ModelRole[raw]
    except KeyError:
        return _DEFAULT_BASELINE_ROLE


# Substrings that mark a deployment id as a small/cheap model, used by the
# measured small-model-share efficiency metric (single source of truth).
_SMALL_MODEL_MARKERS: tuple[str, ...] = ("mini", "3.5", "3-5", "llama-3.2", "phi-3.5")

# A parameter count spelled in the deployment id (``…-90B-…``, ``…-70b``) is
# AUTHORITATIVE and vetoes every generation marker above. Without this veto the
# ``llama-3.2`` marker matched ``genailab-maas-Llama-3.2-90B-Vision-Instruct`` — a
# 90-billion-parameter vision model — and counted it as a "small model", inflating
# ``small_model_share`` and the headline savings story in the favourable
# direction. The Llama 3.2 family spans 1B/3B (genuinely small) and 11B/90B
# (vision, not small), so the generation alone can never decide it.
_SMALL_MODEL_MAX_PARAM_B: float = 10.0
_PARAM_COUNT_RE = re.compile(r"(?<![a-z0-9.])(\d+(?:\.\d+)?)b(?![a-z0-9])")


def _param_count_b(model_id: str) -> float | None:
    """Return the parameter count in billions named in ``model_id``, if any."""
    match = _PARAM_COUNT_RE.search(model_id.lower())
    return float(match.group(1)) if match else None


def is_small_model(model_id: str) -> bool:
    """Return whether ``model_id`` names a small/cheap model deployment.

    A parameter count in the id wins over the generation markers: a 90B model is
    never "small", however small its base generation's other members are.
    """
    lowered = model_id.lower()
    params_b = _param_count_b(lowered)
    if params_b is not None and params_b >= _SMALL_MODEL_MAX_PARAM_B:
        return False
    return any(marker in lowered for marker in _SMALL_MODEL_MARKERS)


# The roles small-model routing actually chooses BETWEEN — i.e. the calls whose
# ``small_model_share`` is a meaningful efficiency signal. An embedding or a
# transcription has exactly one deployment in the fleet and no cheaper tier, so
# counting it would silently dilute the denominator of a routing metric with
# calls that were never routable.
_ROUTABLE_ROLES: frozenset[ModelRole] = frozenset(
    {ModelRole.CHEAP, ModelRole.GENERATION, ModelRole.REASONING, ModelRole.VISION}
)


def is_routable_role(role: ModelRole | None) -> bool:
    """Return whether ``role``'s calls count toward ``small_model_share``.

    A legacy caller that passes no role is treated as routable, preserving the
    pre-existing metric exactly for every chat call site.
    """
    return role is None or role in _ROUTABLE_ROLES


# Per-role pricing, ``(input_rate, output_rate)``, used ONLY as a fallback when a
# provider's cost map has no entry for a model — the case for custom/self-hosted
# deployment ids. Approximate but honest and non-zero; override any role via
# ``COST_<ROLE>_IN`` / ``COST_<ROLE>_OUT``.
#
# The OUTPUT rate is always USD per 1k completion tokens. The INPUT rate is USD
# per one *billable input unit* — see :class:`BillingUnit` / :func:`billing_unit`:
# per 1k prompt tokens for a text model, per audio-MINUTE for ``VOICE`` (Whisper
# bills per minute of audio and produces no billable output tokens, which is
# exactly what ``(0.006, 0.0)`` says), per image for an image-billed role.
_COST_PER_1K: dict[ModelRole, tuple[float, float]] = {
    ModelRole.CHEAP: (0.00015, 0.0006),
    ModelRole.REASONING: (0.0011, 0.0044),
    ModelRole.GENERATION: (0.0025, 0.01),
    ModelRole.EMBEDDING: (0.00013, 0.0),
    ModelRole.VISION: (0.0025, 0.01),
    ModelRole.VOICE: (0.006, 0.0),
}


class BillingUnit(StrEnum):
    """The unit a role's *input* rate is charged per.

    Not every model bills per token. Whisper bills per minute of audio; an
    image-generation/understanding deployment may bill per image. Carrying the
    unit explicitly is what stops a per-minute call from being ledgered as
    ``prompt_tokens=0`` → ``$0.00``, which would let a tenant with a USD cap
    transcribe without limit.
    """

    TOKENS = "tokens"  # input rate is USD per 1k prompt tokens
    AUDIO_MINUTES = "audio_minutes"  # input rate is USD per minute of audio
    IMAGES = "images"  # input rate is USD per image


#: Role → billing unit for the role's input rate. Anything absent bills per token
#: (the overwhelming default), so adding a role never silently changes pricing.
#: ``VISION`` stays on tokens because the fleet's vision deployment charges image
#: content as input *tokens*; the image COUNT is still carried end to end as a
#: measured unit, and becomes billable the moment ``COST_VISION_UNIT=images`` is
#: set. Override any role with ``COST_<ROLE>_UNIT``.
_BILLING_UNIT: dict[ModelRole, BillingUnit] = {
    ModelRole.VOICE: BillingUnit.AUDIO_MINUTES,
}


def billing_unit(role: ModelRole) -> BillingUnit:
    """Return the billing unit ``role``'s input rate is charged per.

    Read from ``COST_<ROLE>_UNIT`` (e.g. ``COST_VOICE_UNIT=audio_minutes``); an
    unset or unrecognised value falls back to the table default rather than
    raising, so a typo can never break a live call.
    """
    default = _BILLING_UNIT.get(role, BillingUnit.TOKENS)
    raw = os.environ.get(f"COST_{role.name}_UNIT", "").strip().lower()
    if not raw:
        return default
    try:
        return BillingUnit(raw)
    except ValueError:
        return default


def _rate_for(role: ModelRole) -> tuple[float, float]:
    """Return the (input, output) rate for ``role`` (env-overridable).

    The output rate is always $/1k completion tokens; the input rate is per one
    unit of :func:`billing_unit` for the role.
    """
    default_in, default_out = _COST_PER_1K.get(role, (0.0, 0.0))
    rate_in = float(os.environ.get(f"COST_{role.name}_IN", default_in))
    rate_out = float(os.environ.get(f"COST_{role.name}_OUT", default_out))
    return rate_in, rate_out


def billable_input_units(
    role: ModelRole,
    *,
    prompt_tokens: int = 0,
    audio_seconds: float = 0.0,
    images: int = 0,
) -> float:
    """Return how many billable input units ``role`` consumed on one call.

    The count is expressed in the role's own :func:`billing_unit`: thousands of
    prompt tokens, minutes of audio, or images.
    """
    unit = billing_unit(role)
    if unit is BillingUnit.AUDIO_MINUTES:
        return max(0.0, audio_seconds) / 60.0
    if unit is BillingUnit.IMAGES:
        return float(max(0, images))
    return max(0, prompt_tokens) / 1000.0


def unit_cost(
    role: ModelRole,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    audio_seconds: float = 0.0,
    images: int = 0,
) -> float:
    """Return the fallback USD cost of one ``role`` call from its billable units.

    ``input_units × input_rate + completion_tokens/1000 × output_rate``, where the
    input unit is whatever :func:`billing_unit` says for the role. A token-only
    call reduces exactly to the original per-1k-token formula, so existing
    behaviour is unchanged.
    """
    rate_in, rate_out = _rate_for(role)
    units_in = billable_input_units(
        role, prompt_tokens=prompt_tokens, audio_seconds=audio_seconds, images=images
    )
    return units_in * rate_in + (max(0, completion_tokens) / 1000) * rate_out
